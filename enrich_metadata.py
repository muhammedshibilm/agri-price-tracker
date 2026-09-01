"""
enrich_metadata.py

Detects products that are missing category/image metadata and fills them in
automatically using Gemini's free API for classification + Wikimedia Commons
for a real (non-hallucinated) image URL.

Usage (called from fetch_data.py after the main sync):

    from enrich_metadata import enrich_new_products
    enrich_new_products(PRODUCT_NAMES)  # dict of product_id -> display name

Environment variables required:
    GEMINI_API_KEY   - free key from https://aistudio.google.com/app/apikey

Output:
    data/product-metadata.json
        {
          "carrot": {
            "category": "Vegetables",
            "image_url": "https://upload.wikimedia.org/...",
            "image_source": "wikimedia",
            "added_at": "2026-09-01T12:00:00Z"
          },
          ...
        }

Design notes:
- This never overwrites an existing entry. If you want to re-classify
  something, delete its entry manually first.
- If Gemini or Wikimedia fails for a product, that product is just skipped
  this run and retried next run - it will NOT block the rest of the pipeline
  or corrupt the metadata file.
- Categories are constrained to a fixed list so your frontend filter UI
  doesn't have to deal with an open-ended set of strings.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent / "data"
METADATA_PATH = DATA_DIR / "product-metadata.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Preferred model names, in order. Google renames/retires "flash" aliases
# periodically, so instead of hardcoding one and getting a silent 404 wall
# (like gemini-2.0-flash just did), we ask the API what's actually
# available to this key and pick the best match at runtime.
PREFERRED_MODEL_SUBSTRINGS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

_resolved_model_cache = None

# Free-tier Gemini has a low requests-per-minute ceiling. Rather than
# fighting it, we (a) only classify a small batch per run, and (b) retry
# individual calls with backoff if we still get rate-limited.
MAX_NEW_PER_RUN = 15
MAX_CLASSIFY_ATTEMPTS = 4
CLASSIFY_BASE_BACKOFF_SEC = 15


def resolve_gemini_model() -> str | None:
    """Return a valid model name (e.g. 'models/gemini-2.5-flash') that
    supports generateContent for this API key, or None if none found /
    the key is invalid. Cached for the life of the process."""
    global _resolved_model_cache
    if _resolved_model_cache is not None:
        return _resolved_model_cache

    if not GEMINI_API_KEY:
        return None

    try:
        resp = requests.get(
            f"{GEMINI_API_BASE}/models",
            params={"key": GEMINI_API_KEY},
            timeout=20,
        )
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except Exception as e:
        print(f"  [enrich] Could not list Gemini models: {e}", file=sys.stderr)
        return None

    usable = [
        m["name"] for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    if not usable:
        print("  [enrich] No Gemini models with generateContent support for this key", file=sys.stderr)
        return None

    for pref in PREFERRED_MODEL_SUBSTRINGS:
        for name in usable:
            if pref in name:
                _resolved_model_cache = name
                print(f"  [enrich] Using Gemini model: {name}")
                return name

    # Fall back to whatever's first if none of our preferred names matched.
    _resolved_model_cache = usable[0]
    print(f"  [enrich] Using Gemini model (fallback): {usable[0]}")
    return usable[0]

ALLOWED_CATEGORIES = [
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
    "Other",
]

WIKIMEDIA_SEARCH_URL = "https://commons.wikimedia.org/w/api.php"


def load_metadata() -> dict:
    if not METADATA_PATH.exists():
        return {}
    with open(METADATA_PATH) as f:
        return json.load(f)


def save_metadata(metadata: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def classify_with_gemini(product_id: str, display_name: str) -> dict | None:
    """Ask Gemini for a category + a good Wikimedia Commons search query.
    Returns None on any failure (caller should skip and retry next run)."""
    if not GEMINI_API_KEY:
        print("  [enrich] GEMINI_API_KEY not set - skipping AI classification", file=sys.stderr)
        return None

    model_name = resolve_gemini_model()
    if not model_name:
        print("  [enrich] No usable Gemini model available - skipping AI classification", file=sys.stderr)
        return None

    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"

    prompt = f"""You are helping classify Indian agricultural commodities for a
mandi (wholesale market) price tracking app for the state of Kerala.

Commodity: "{display_name}" (internal id: "{product_id}")

Respond with ONLY a JSON object, no markdown, no code fences, no extra text:
{{
  "category": one of {ALLOWED_CATEGORIES},
  "wikimedia_query": "a short, specific search phrase (3-6 words) likely to
      find a real, clear photo of this commodity on Wikimedia Commons"
}}"""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1},
    }

    try:
        for attempt in range(1, MAX_CLASSIFY_ATTEMPTS + 1):
            resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=30)
            if resp.status_code in (429, 503):
                if attempt == MAX_CLASSIFY_ATTEMPTS:
                    resp.raise_for_status()
                backoff = CLASSIFY_BASE_BACKOFF_SEC * attempt
                print(f"  [enrich] {resp.status_code} for {product_id}, retrying in {backoff}s "
                      f"(attempt {attempt}/{MAX_CLASSIFY_ATTEMPTS})", file=sys.stderr)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            break

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip accidental code fences just in case.
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(text)

        if parsed.get("category") not in ALLOWED_CATEGORIES:
            parsed["category"] = "Other"
        if not parsed.get("wikimedia_query"):
            parsed["wikimedia_query"] = display_name

        return parsed
    except Exception as e:
        print(f"  [enrich] Gemini classification failed for {product_id} (model={model_name}): {e}", file=sys.stderr)
        return None


def find_wikimedia_image(query: str) -> str | None:
    """Search Wikimedia Commons for a real, licensed image. Returns a direct
    file URL or None if nothing suitable was found."""
    try:
        resp = requests.get(
            WIKIMEDIA_SEARCH_URL,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrlimit": 5,
                "gsrnamespace": 6,  # File: namespace
                "prop": "imageinfo",
                "iiprop": "url|mime",
            },
            headers={"User-Agent": "agri-price-tracker/1.0 (metadata enrichment bot)"},
            timeout=20,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})

        for page in pages.values():
            imageinfo = page.get("imageinfo")
            if not imageinfo:
                continue
            info = imageinfo[0]
            mime = info.get("mime", "")
            if mime.startswith("image/") and mime != "image/svg+xml":
                return info["url"]

        return None
    except Exception as e:
        print(f"  [enrich] Wikimedia search failed for '{query}': {e}", file=sys.stderr)
        return None


def enrich_new_products(product_names: dict, sleep_between_calls: float = 4.5):
    """Main entry point. product_names: {product_id: display_name}"""
    metadata = load_metadata()
    missing = [pid for pid in product_names if pid not in metadata]

    if not missing:
        print("[enrich] No new products need metadata.")
        return

    if len(missing) > MAX_NEW_PER_RUN:
        print(f"[enrich] {len(missing)} product(s) missing metadata - classifying "
              f"{MAX_NEW_PER_RUN} this run to stay under Gemini's free-tier rate limit; "
              f"the rest will be picked up on subsequent runs.")
        missing = missing[:MAX_NEW_PER_RUN]
    else:
        print(f"[enrich] {len(missing)} product(s) missing metadata: {missing}")
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated = False

    for product_id in missing:
        display_name = product_names[product_id]
        print(f"[enrich] Classifying '{display_name}' ({product_id})...")

        classification = classify_with_gemini(product_id, display_name)
        if classification is None:
            continue  # retry next run

        image_url = find_wikimedia_image(classification["wikimedia_query"])
        if image_url is None:
            # Fall back to searching on the plain display name if the
            # AI's suggested query came up empty.
            image_url = find_wikimedia_image(display_name)

        metadata[product_id] = {
            "category": classification["category"],
            "image_url": image_url,  # may be None - frontend should show a placeholder
            "image_source": "wikimedia" if image_url else None,
            "added_at": now_iso,
        }
        updated = True
        print(f"  -> category={classification['category']!r} image={'found' if image_url else 'NOT FOUND'}")

        time.sleep(sleep_between_calls)  # be polite to both free APIs

    if updated:
        save_metadata(metadata)
        print(f"[enrich] Wrote {METADATA_PATH}")
    else:
        print("[enrich] Nothing new was successfully classified this run.")


if __name__ == "__main__":
    # Standalone test run - reads PRODUCT_NAMES from fetch_data.py
    sys.path.insert(0, str(Path(__file__).parent))
    from fetch_data import PRODUCT_NAMES
    enrich_new_products(PRODUCT_NAMES)
