import os
import re
import json
import time
import random
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
METADATA_PATH = DATA_DIR / "metadata.json"
IMAGES_PATH = DATA_DIR / "images.json"

# NVIDIA's build.nvidia.com hosts an OpenAI-compatible chat completions
# endpoint. Free-tier rate limits here are much more generous than the
# Gemini flash tier that was previously getting hammered with 429s.
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")

CATEGORIES = [
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
    "Other",
]

MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5
REQUEST_TIMEOUT_SEC = 30
THROTTLE_SEC = 1.0  # small pause between calls, stays well under any RPM cap

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

# Wikimedia's API policy rejects requests without a descriptive User-Agent
# identifying the application - a bare/default one gets a blanket 403.
# See: https://meta.wikimedia.org/wiki/User-Agent_policy
WIKIMEDIA_HEADERS = {
    "User-Agent": "agri-price-tracker/1.0 (https://github.com/muhammedshibilm/agri-price-tracker)"
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_metadata() -> dict:
    return _load_json(METADATA_PATH)


def load_images() -> dict:
    return _load_json(IMAGES_PATH)


def _classify_with_nvidia(product_name: str) -> str | None:
    """Ask an NVIDIA-hosted LLM to classify a commodity into one of
    CATEGORIES. Returns None (not 'Other') on any failure - including a
    missing API key - so a transient problem never gets permanently
    cached as a real classification. The product is simply left pending
    and retried on the next run."""
    if not NVIDIA_API_KEY:
        print("  [enrich] NVIDIA_API_KEY not set - skipping classification")
        return None

    prompt = (
        "Classify the following agricultural commodity sold in Indian mandi "
        f"markets into exactly one category.\n\nCommodity: {product_name}\n\n"
        f"Valid categories: {', '.join(CATEGORIES)}\n\n"
        "Respond with ONLY the category name, nothing else."
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 16,
    }

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                NVIDIA_BASE_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SEC
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RuntimeError(f"{resp.status_code} for {product_name}")
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            for cat in CATEGORIES:
                if cat.lower() in text.lower():
                    return cat
            return "Other"
        except Exception as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1)) + random.uniform(0, 2)
            print(f"  [enrich] {e}, retrying in {backoff:.0f}s (attempt {attempt}/{MAX_ATTEMPTS})")
            time.sleep(backoff)

    print(f"  [enrich] NVIDIA classification failed for {product_name} "
          f"(model={NVIDIA_MODEL}): {last_error}")
    return None


def _find_wikimedia_image(product_name: str) -> str | None:
    """Best-effort Wikimedia Commons image lookup. Returns a direct
    thumbnail URL, or None if nothing usable was found - never raises,
    since a missing image should never block the rest of enrichment."""
    query = re.sub(r"\(.*?\)", "", product_name).strip()
    try:
        search_resp = requests.get(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "list": "search",
                "srnamespace": 6,  # File: namespace
                "srsearch": f"{query} filetype:bitmap",
                "srlimit": 1,
                "format": "json",
            },
            headers=WIKIMEDIA_HEADERS,
            timeout=15,
        )
        search_resp.raise_for_status()
        results = search_resp.json().get("query", {}).get("search", [])
        if not results:
            return None
        title = results[0]["title"]

        info_resp = requests.get(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 400,
                "format": "json",
            },
            headers=WIKIMEDIA_HEADERS,
            timeout=15,
        )
        info_resp.raise_for_status()
        pages = info_resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo")
            if imageinfo:
                return imageinfo[0].get("thumburl") or imageinfo[0].get("url")
    except Exception as e:
        print(f"  [enrich] image lookup failed for {product_name}: {e}")
    return None


def enrich_new_products(product_names: dict) -> None:
    """For every product_id in product_names that doesn't yet have
    metadata, classify it via NVIDIA's API and look up a Wikimedia
    Commons image. Writes results incrementally so a crash/interrupt
    mid-run doesn't lose already-completed work."""
    metadata = load_metadata()
    images = load_images()

    pending = [(pid, name) for pid, name in product_names.items() if pid not in metadata]
    if not pending:
        return

    print(f"  [enrich] classifying {len(pending)} product(s) via NVIDIA ({NVIDIA_MODEL})...")

    for i, (product_id, product_name) in enumerate(pending, 1):
        category = _classify_with_nvidia(product_name)
        if category is not None:
            metadata[product_id] = {"category": category}
            _save_json(METADATA_PATH, metadata)

        if product_id not in images:
            image_url = _find_wikimedia_image(product_name)
            if image_url:
                images[product_id] = image_url
                _save_json(IMAGES_PATH, images)

        if i < len(pending):
            time.sleep(THROTTLE_SEC)

    print("  [enrich] done.")
