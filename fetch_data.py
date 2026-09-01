
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
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

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
        resp = requests.post(GEMINI_URL, json=body, timeout=30)
        resp.raise_for_status()
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
        print(f"  [enrich] Gemini classification failed for {product_id}: {e}", file=sys.stderr)
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


def enrich_new_products(product_names: dict, sleep_between_calls: float = 1.0):
    """Main entry point. product_names: {product_id: display_name}"""
    metadata = load_metadata()
    missing = [pid for pid in product_names if pid not in metadata]

    if not missing:
        print("[enrich] No new products need metadata.")
        return

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
