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

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = os.environ.get(
    "NVIDIA_MODEL",
    "meta/llama-3.1-8b-instruct",
)

CATEGORIES = [
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
]

MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5
REQUEST_TIMEOUT_SEC = 30
THROTTLE_SEC = 1.0

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

WIKIMEDIA_HEADERS = {
    "User-Agent": (
        "agri-price-tracker/1.0 "
        "(https://github.com/muhammedshibilm/agri-price-tracker)"
    )
}


# ---------------------------------------------------------------------------
# Deterministic categories
# ---------------------------------------------------------------------------
#
# Do NOT waste an LLM call for commodities we already understand.
# NVIDIA is used only as a fallback for genuinely unknown commodities.
#
CATEGORY_OVERRIDES = {
    # Vegetables
    "amaranthus": "Vegetables",
    "amaranthus-red": "Vegetables",
    "ashgourd": "Vegetables",
    "beetroot": "Vegetables",
    "bhindi": "Vegetables",
    "bitter-gourd": "Vegetables",
    "bottle-gourd": "Vegetables",
    "brinjal": "Vegetables",
    "cabbage": "Vegetables",
    "capsicum": "Vegetables",
    "carrot": "Vegetables",
    "cauliflower": "Vegetables",
    "cluster-beans": "Vegetables",
    "colacasia": "Vegetables",
    "cowpea-veg": "Vegetables",
    "cucumber": "Vegetables",
    "drumstick": "Vegetables",
    "duster-beans": "Vegetables",
    "french-beans": "Vegetables",
    "green-avare": "Vegetables",
    "green-chilli": "Spices",
    "green-peas": "Vegetables",
    "indian-beans": "Vegetables",
    "little-gourd": "Vegetables",
    "onion": "Vegetables",
    "papaya-raw": "Vegetables",
    "potato": "Vegetables",
    "pumpkin": "Vegetables",
    "ridge-gourd": "Vegetables",
    "snake-gourd": "Vegetables",
    "sweet-potato": "Vegetables",
    "tapioca": "Vegetables",
    "tomato": "Vegetables",
    "yam-ratalu": "Vegetables",
    "yam-suran": "Vegetables",
    "alsandikai": "Vegetables",
    "mushroom": "Vegetables",
    
    # Fruits
    "apple": "Fruits",
    "banana": "Fruits",
    "banana-green": "Fruits",
    "grapes": "Fruits",
    "lemon": "Fruits",
    "lime": "Fruits",
    "mango": "Fruits",
    "mango-raw-ripe": "Fruits",
    "orange": "Fruits",
    "papaya": "Fruits",
    "pineapple": "Fruits",
    "sapota": "Fruits",
    "watermelon": "Fruits",
    "amla": "Fruits",
    "galgal-lemon": "Fruits",
    "field-pea": "Vegetables",
    "long-melon": "Fruits",

    # Spices
    "black-pepper": "Spices",
    "pepper-garbled": "Spices",
    "red-chilli": "Spices",
    "green-chilli": "Spices",
    "garlic": "Spices",
    "ginger": "Spices",
    "coriander": "Spices",

    # Grains & Pulses
    "bengal-gram": "Grains & Pulses",
    "black-gram": "Grains & Pulses",
    "cowpea": "Grains & Pulses",
    "green-gram": "Grains & Pulses",
    "kabuli-chana": "Grains & Pulses",
    "red-gram": "Grains & Pulses",
    "paddy": "Grains & Pulses",

    # Plantation Crops
    "arecanut": "Plantation Crops",
    "coconut": "Plantation Crops",
    "coconut-seed": "Plantation Crops",
    "coconut-oil": "Plantation Crops",
    "coffee": "Plantation Crops",
    "copra": "Plantation Crops",
    "rubber": "Plantation Crops",

    # Egg is intentionally NOT displayable because your app categories
    # are agricultural crop categories.
    "egg": None,
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else {}

    except (json.JSONDecodeError, OSError) as e:
        print(f"  [enrich] failed reading {path}: {e}")
        return {}


def _save_json(path: Path, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )

    temp_path.replace(path)


def load_metadata() -> dict:
    return _load_json(METADATA_PATH)


def load_images() -> dict:
    return _load_json(IMAGES_PATH)


# ---------------------------------------------------------------------------
# NVIDIA classification
# ---------------------------------------------------------------------------

def _classify_with_nvidia(product_name: str) -> str | None:
    if not NVIDIA_API_KEY:
        print(
            "  [enrich] NVIDIA_API_KEY not set - "
            "cannot classify unknown commodity"
        )
        return None

    prompt = (
        "Classify this agricultural commodity sold in Indian markets "
        "into exactly one of these categories:\n\n"
        "Vegetables\n"
        "Fruits\n"
        "Spices\n"
        "Grains & Pulses\n"
        "Plantation Crops\n\n"
        f"Commodity: {product_name}\n\n"
        "Respond with ONLY the category name."
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.0,
        "max_tokens": 16,
    }

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                NVIDIA_BASE_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SEC,
            )

            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            response.raise_for_status()

            data = response.json()

            text = (
                data["choices"][0]["message"]["content"]
                .strip()
            )

            # Exact category matching first.
            normalized = text.lower().strip()

            for category in CATEGORIES:
                if normalized == category.lower():
                    return category

            # Fallback substring matching.
            for category in CATEGORIES:
                if category.lower() in normalized:
                    return category

            print(
                f"  [enrich] NVIDIA returned invalid category "
                f"for {product_name}: {text!r}"
            )

            return None

        except Exception as e:
            last_error = e

            if attempt == MAX_ATTEMPTS:
                break

            backoff = (
                BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                + random.uniform(0, 2)
            )

            print(
                f"  [enrich] {e}; retrying in "
                f"{backoff:.0f}s "
                f"(attempt {attempt}/{MAX_ATTEMPTS})"
            )

            time.sleep(backoff)

    print(
        f"  [enrich] NVIDIA classification failed for "
        f"{product_name}: {last_error}"
    )

    return None


def classify_product(product_id: str, product_name: str) -> str | None:
    """
    Deterministic classification first.
    NVIDIA only for unknown commodities.
    """

    if product_id in CATEGORY_OVERRIDES:
        category = CATEGORY_OVERRIDES[product_id]

        if category is None:
            print(
                f"  [enrich] {product_name} -> not a displayable crop"
            )

        else:
            print(
                f"  [enrich] {product_name} -> {category} "
                f"(local mapping)"
            )

        return category

    return _classify_with_nvidia(product_name)


# ---------------------------------------------------------------------------
# Wikimedia image search
# ---------------------------------------------------------------------------

def _clean_search_name(product_name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", product_name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def _find_wikimedia_image(product_name: str) -> str | None:
    """
    Search Wikimedia Commons and return a thumbnail URL.

    Uses several search terms and rejects obviously bad matches.
    """

    clean_name = _clean_search_name(product_name)

    search_terms = [
        f'"{clean_name}"',
        clean_name,
        f"{clean_name} vegetable",
        f"{clean_name} fruit",
        f"{clean_name} plant",
        f"{clean_name} crop",
    ]

    bad_words = {
        "bee",
        "insect",
        "bird",
        "instrument",
        "person",
        "football",
        "cricket",
        "actor",
        "actress",
        "painting",
        "building",
        "architecture",
        "car",
        "vehicle",
    }

    for search_term in search_terms:
        try:
            response = requests.get(
                WIKIMEDIA_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srnamespace": 6,
                    "srsearch": f"{search_term} filetype:bitmap",
                    "srlimit": 10,
                    "format": "json",
                },
                headers=WIKIMEDIA_HEADERS,
                timeout=15,
            )

            response.raise_for_status()

            results = (
                response.json()
                .get("query", {})
                .get("search", [])
            )

            for result in results:
                title = result.get("title", "")

                title_lower = title.lower()

                if any(word in title_lower for word in bad_words):
                    continue

                # Make sure the title has at least one meaningful
                # search-word overlap.
                words = [
                    w.lower()
                    for w in re.findall(r"[a-zA-Z]+", clean_name)
                    if len(w) >= 3
                ]

                if words and not any(
                    word in title_lower
                    for word in words
                ):
                    continue

                image = _get_wikimedia_image(title)

                if image:
                    return image

        except Exception as e:
            print(
                f"  [enrich] image lookup failed for "
                f"{product_name}: {e}"
            )

    return None


def _get_wikimedia_image(title: str) -> str | None:
    try:
        response = requests.get(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "iiurlwidth": 500,
                "format": "json",
            },
            headers=WIKIMEDIA_HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        pages = (
            response.json()
            .get("query", {})
            .get("pages", {})
        )

        for page in pages.values():
            imageinfo = page.get("imageinfo")

            if not imageinfo:
                continue

            info = imageinfo[0]

            mime = info.get("mime", "")

            if not mime.startswith("image/"):
                continue

            return (
                info.get("thumburl")
                or info.get("url")
            )

    except Exception as e:
        print(
            f"  [enrich] Wikimedia info failed "
            f"for {title}: {e}"
        )

    return None


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------

def enrich_new_products(product_names: dict) -> None:
    """
    Enrich all products.

    Important:
    - Existing null categories are retried.
    - Known products use CATEGORY_OVERRIDES.
    - Unknown products use NVIDIA.
    - Images are looked up independently.
    """

    metadata = load_metadata()
    images = load_images()

    for product_id, product_name in product_names.items():

        current_metadata = metadata.get(product_id)

        current_category = (
            current_metadata.get("category")
            if isinstance(current_metadata, dict)
            else None
        )

        # ---------------------------------------------------------------
        # CATEGORY
        # ---------------------------------------------------------------

        needs_category = (
            product_id in CATEGORY_OVERRIDES
            or current_category not in CATEGORIES
        )

        if needs_category:

            category = classify_product(
                product_id,
                product_name,
            )

            # For known "Other"/non-displayable commodities such as Egg,
            # persist null deliberately so we know it was processed.
            if category is None:
                metadata[product_id] = {
                    "category": None,
                    "classification_status": "excluded",
                }

            else:
                metadata[product_id] = {
                    "category": category,
                    "classification_status": "classified",
                }

            _save_json(
                METADATA_PATH,
                metadata,
            )

        # ---------------------------------------------------------------
        # IMAGE
        # ---------------------------------------------------------------

        if not images.get(product_id):

            print(
                f"  [enrich] finding image for "
                f"{product_name}..."
            )

            image_url = _find_wikimedia_image(
                product_name
            )

            if image_url:
                images[product_id] = image_url

                _save_json(
                    IMAGES_PATH,
                    images,
                )

                print(
                    f"  [enrich] image saved for "
                    f"{product_name}"
                )

            else:
                print(
                    f"  [enrich] no suitable image "
                    f"found for {product_name}"
                )

        time.sleep(THROTTLE_SEC)

    print("  [enrich] metadata enrichment complete.")
