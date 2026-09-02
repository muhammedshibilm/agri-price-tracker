"""
enrich_metadata.py

Detects products that are missing metadata and fills it in automatically:

  1. CATEGORY - classified with Gemini's free API (unchanged behaviour).
  2. IMAGE URL - looked up automatically on Wikimedia Commons (new). This is
     a real search against Commons' API, not something asked of Gemini, so
     we never end up with a hallucinated/broken URL. If no reasonable match
     is found, the product is just left without an image_url and picked up
     again on a later run.

Both are additive/non-destructive:
  - product-metadata.json entries are never overwritten once they exist
    (delete an entry manually to force re-classification).
  - product-images.json entries are never overwritten once they exist, so
    any image you've hand-picked/corrected stays put permanently.

Usage (called from fetch_data.py after the main sync):

    from enrich_metadata import enrich_new_products, load_metadata, load_images
    enrich_new_products(PRODUCT_NAMES)  # dict of product_id -> display name

Environment variables required:
    GEMINI_API_KEY   - free key from https://aistudio.google.com/app/apikey
                        (only needed for category classification; image
                        lookup uses the public, unauthenticated Commons API)

Output:
    data/product-metadata.json
        {
          "carrot": {
            "category": "Vegetables",
            "added_at": "2026-09-01T12:00:00Z"
          },
          ...
        }

    data/product-images.json
        {
          "carrot": "https://upload.wikimedia.org/wikipedia/commons/...jpg",
          ...
        }

Design notes:
- Category and image lookups never overwrite existing entries. If you want
  to re-classify or re-pick an image for something, delete its entry
  manually first (from the relevant file) and it'll be picked up again.
- If Gemini or Commons fails for a product, that product is just skipped
  this run and retried next run - it will NOT block the rest of the
  pipeline or corrupt either metadata file.
- Categories are constrained to a fixed list so your frontend filter UI
  doesn't have to deal with an open-ended set of strings.
"""

import os
import re
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent / "data"
METADATA_PATH = DATA_DIR / "product-metadata.json"
IMAGES_PATH = DATA_DIR / "product-images.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

COMMONS_API_BASE = "https://commons.wikimedia.org/w/api.php"

# Preferred model names, in order. Google renames/retires "flash" aliases
# periodically, so instead of hardcoding one and getting a silent 404 wall
# (like gemini-2.0-flash just did), we ask the API what's actually
# available to this key and pick the best match at runtime.
PREFERRED_MODEL_SUBSTRINGS = [
    "gemini-3.8-flash"
]

_resolved_model_cache = None

# Free-tier Gemini has a low requests-per-minute ceiling. Rather than
# fighting it, we (a) only classify a small batch per run, and (b) retry
# individual calls with backoff if we still get rate-limited.
MAX_NEW_PER_RUN = 15
MAX_CLASSIFY_ATTEMPTS = 4
CLASSIFY_BASE_BACKOFF_SEC = 15

# Commons image lookup is unauthenticated and much cheaper than Gemini
# calls, but we still don't want to hammer it - small per-run cap and a
# short pause between calls is plenty.
MAX_IMAGE_LOOKUPS_PER_RUN = 30
IMAGE_LOOKUP_SLEEP_SEC = 1.0
# Only accept common web image formats - Commons search sometimes turns up
# svg diagrams / pdf scans / category pages that aren't useful as photos.
ACCEPTABLE_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

ALLOWED_CATEGORIES = [
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
    "Other",
]


# ---------------------------------------------------------------------------
# metadata (category) storage
# ---------------------------------------------------------------------------

def load_metadata() -> dict:
    if not METADATA_PATH.exists():
        return {}
    with open(METADATA_PATH) as f:
        return json.load(f)


def save_metadata(metadata: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# image storage
# ---------------------------------------------------------------------------

def load_images() -> dict:
    if not IMAGES_PATH.exists():
        return {}
    with open(IMAGES_PATH) as f:
        return json.load(f)


def save_images(images: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(IMAGES_PATH, "w") as f:
        json.dump(images, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Gemini category classification
# ---------------------------------------------------------------------------

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


def classify_with_gemini(product_id: str, display_name: str) -> dict | None:
    """Ask Gemini for just a category. Returns None on any failure (caller
    should skip and retry next run)."""
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
  "category": one of {ALLOWED_CATEGORIES}
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

        return parsed
    except Exception as e:
        print(f"  [enrich] Gemini classification failed for {product_id} (model={model_name}): {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Wikimedia Commons image lookup
# ---------------------------------------------------------------------------

def _clean_search_term(display_name: str) -> str:
    """Strip parenthetical/localised text (e.g. 'Amaranthus (Cheera)' ->
    'Amaranthus') so the Commons search hits the plain English term."""
    term = re.sub(r"\(.*?\)", "", display_name)
    term = re.sub(r"\s+", " ", term).strip()
    return term or display_name


def find_commons_image(display_name: str) -> str | None:
    """Search Wikimedia Commons for a real, existing image matching this
    commodity and return its direct file URL, or None if nothing suitable
    was found. This performs an actual search - it never fabricates a URL,
    so a returned link is guaranteed to resolve."""
    term = _clean_search_term(display_name)
    query = f"{term} vegetable OR fruit OR crop OR produce"

    try:
        resp = requests.get(
            COMMONS_API_BASE,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,  # File: namespace
                "gsrlimit": 5,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "format": "json",
            },
            headers={"User-Agent": "kerala-mandi-price-app/1.0 (data enrichment script)"},
            timeout=20,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"  [enrich] Commons image search failed for '{display_name}': {e}", file=sys.stderr)
        return None

    for page in pages.values():
        for info in page.get("imageinfo", []):
            file_url = info.get("url", "")
            if file_url.lower().endswith(ACCEPTABLE_IMAGE_EXTENSIONS):
                return file_url

    return None


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def enrich_new_products(product_names: dict, sleep_between_calls: float = 4.5):
    """Main entry point. product_names: {product_id: display_name}

    For every product missing a category, ask Gemini to classify it.
    For every product missing an image, look one up on Wikimedia Commons.
    These are independent passes - a product can pick up an image before
    it has a category, or vice versa, and either can be retried on a later
    run without disturbing whatever the other pass already found.
    """
    metadata = load_metadata()
    images = load_images()

    missing_category = [pid for pid in product_names if pid not in metadata]
    missing_image = [pid for pid in product_names if pid not in images]

    _enrich_categories(product_names, metadata, missing_category, sleep_between_calls)
    _enrich_images(product_names, images, missing_image)


def _enrich_categories(product_names, metadata, missing, sleep_between_calls):
    if not missing:
        print("[enrich] No new products need category classification.")
        return

    if len(missing) > MAX_NEW_PER_RUN:
        print(f"[enrich] {len(missing)} product(s) missing category - classifying "
              f"{MAX_NEW_PER_RUN} this run to stay under Gemini's free-tier rate limit; "
              f"the rest will be picked up on subsequent runs.")
        missing = missing[:MAX_NEW_PER_RUN]
    else:
        print(f"[enrich] {len(missing)} product(s) missing category: {missing}")

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated = False

    for product_id in missing:
        display_name = product_names[product_id]
        print(f"[enrich] Classifying '{display_name}' ({product_id})...")

        classification = classify_with_gemini(product_id, display_name)
        if classification is None:
            continue  # retry next run

        metadata[product_id] = {
            "category": classification["category"],
            "added_at": now_iso,
        }
        updated = True
        print(f"  -> category={classification['category']!r}")

        time.sleep(sleep_between_calls)  # be polite to the free API

    if updated:
        save_metadata(metadata)
        print(f"[enrich] Wrote {METADATA_PATH}")
    else:
        print("[enrich] Nothing new was successfully classified this run.")


def _enrich_images(product_names, images, missing):
    if not missing:
        print("[enrich] No new products need an image lookup.")
        return

    if len(missing) > MAX_IMAGE_LOOKUPS_PER_RUN:
        print(f"[enrich] {len(missing)} product(s) missing an image - looking up "
              f"{MAX_IMAGE_LOOKUPS_PER_RUN} this run; the rest will be picked up "
              f"on subsequent runs.")
        missing = missing[:MAX_IMAGE_LOOKUPS_PER_RUN]
    else:
        print(f"[enrich] {len(missing)} product(s) missing an image: {missing}")

    updated = False

    for product_id in missing:
        display_name = product_names[product_id]
        image_url = find_commons_image(display_name)
        if image_url is None:
            print(f"  [enrich] No Commons image found for '{display_name}' ({product_id}) - will retry later")
            continue

        images[product_id] = image_url
        updated = True
        print(f"  [enrich] image for '{display_name}' ({product_id}) -> {image_url}")

        time.sleep(IMAGE_LOOKUP_SLEEP_SEC)  # be polite to the Commons API

    if updated:
        save_images(images)
        print(f"[enrich] Wrote {IMAGES_PATH}")
    else:
        print("[enrich] No new images were found this run.")


if __name__ == "__main__":
    # Standalone test run - reads PRODUCT_NAMES from fetch_data.py
    sys.path.insert(0, str(Path(__file__).parent))
    from fetch_data import PRODUCT_NAMES
    enrich_new_products(PRODUCT_NAMES)
