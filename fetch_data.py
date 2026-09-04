import os
import sys
import csv
import io
import time
import json
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_PATH = DATA_DIR / "manifest.json"

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

REQUEST_TIMEOUT_SEC = 90
NVIDIA_TIMEOUT_SEC = 30
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5
RETENTION_DAYS = 7
STATE = "Keralam"

DISPLAYABLE_CATEGORIES = {
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
}

# Stable mappings for commodities already supported by the app.
# New commodities are discovered automatically from the API.
TARGET_PRODUCTS = {
    "amaranthus": ["Amaranthus"],
    "amaranthus-red": ["Amranthas Red"],
    "apple": ["Apple"],
    "arecanut": ["Arecanut(Betelnut/Supari)"],
    "ashgourd": ["Ashgourd"],
    "banana": ["Banana"],
    "banana-green": ["Banana - Green"],
    "beetroot": ["Beetroot"],
    "bengal-gram": ["Bengal Gram(Gram)(Whole)"],
    "bhindi": ["Bhindi(Ladies Finger)"],
    "bitter-gourd": ["Bitter gourd"],
    "black-gram": ["Black Gram(Urd Beans)(Whole)"],
    "black-pepper": ["Black pepper"],
    "bottle-gourd": ["Bottle gourd"],
    "brinjal": ["Brinjal"],
    "cabbage": ["Cabbage"],
    "capsicum": ["Capsicum"],
    "carrot": ["Carrot"],
    "cauliflower": ["Cauliflower"],
    "sapota": ["Chikoos(Sapota)"],
    "red-chilli": ["Chili Red"],
    "cluster-beans": ["Cluster beans"],
    "coconut": ["Coconut"],
    "coconut-oil": ["Coconut Oil"],
    "coconut-seed": ["Coconut Seed"],
    "coffee": ["Coffee"],
    "colacasia": ["Colacasia"],
    "copra": ["Copra"],
    "coriander": ["Coriander(Leaves)"],
    "cowpea": ["Cowpea(Lobia/Karamani)"],
    "cowpea-veg": ["Cowpea(Veg)"],
    "cucumber": ["Cucumbar(Kheera)"],
    "drumstick": ["Drumstick"],
    "duster-beans": ["Duster Beans"],
    "yam-suran": ["Elephant Yam(Suran)/Amorphophallus"],
    "field-pea": ["Field Pea"],
    "french-beans": ["French Beans(Frasbean)"],
    "galgal-lemon": ["Galgal(Lemon)"],
    "garlic": ["Garlic"],
    "ginger": ["Ginger(Green)"],
    "grapes": ["Grapes"],
    "green-avare": ["Green Avare(W)"],
    "green-chilli": ["Green Chilli"],
    "green-gram": ["Green Gram(Moong)(Whole)"],
    "green-peas": ["Green Peas"],
    "indian-beans": ["Indian Beans(Seam)"],
    "kabuli-chana": ["Kabuli Chana(Chickpeas-White)"],
    "lemon": ["Lemon"],
    "lime": ["Lime"],
    "little-gourd": ["Little gourd(Kundru)"],
    "long-melon": ["Long Melon(Kakri)"],
    "mango": ["Mango"],
    "mushroom": ["Mashrooms"],
    "onion": ["Onion"],
    "orange": ["Orange"],
    "paddy": ["Paddy(Common)"],
    "papaya": ["Papaya"],
    "pepper-garbled": ["Pepper garbled"],
    "pineapple": ["Pineapple"],
    "potato": ["Potato"],
    "pumpkin": ["Pumpkin"],
    "red-gram": ["Red gram/Arhar/Tur(whole)"],
    "ridge-gourd": ["Ridgeguard(Tori)"],
    "rubber": ["Rubber"],
    "snake-gourd": ["Snakeguard"],
    "sweet-potato": ["Sweet Potato"],
    "tapioca": ["Tapioca"],
    "tomato": ["Tomato"],
    "watermelon": ["Water Melon"],
    "yam-ratalu": ["Yam(Ratalu)"],
    "alsandikai": ["Alsandikai"],
    "amla": ["Amla(Nelli Kai)"],
    "papaya-raw": ["Papaya(Raw)"],
}

PRODUCT_NAMES = {
    "amaranthus": "Amaranthus (Cheera)",
    "amaranthus-red": "Amaranthus - Red",
    "apple": "Apple",
    "arecanut": "Arecanut",
    "ashgourd": "Ash Gourd",
    "banana": "Banana",
    "banana-green": "Banana (Green)",
    "beetroot": "Beetroot",
    "bengal-gram": "Bengal Gram (Whole)",
    "bhindi": "Bhindi (Ladies Finger)",
    "bitter-gourd": "Bitter Gourd",
    "black-gram": "Black Gram / Urd Beans (Whole)",
    "black-pepper": "Black Pepper",
    "bottle-gourd": "Bottle Gourd",
    "brinjal": "Brinjal",
    "cabbage": "Cabbage",
    "capsicum": "Capsicum",
    "carrot": "Carrot",
    "cauliflower": "Cauliflower",
    "sapota": "Sapota (Chikoo)",
    "red-chilli": "Red Chilli",
    "cluster-beans": "Cluster Beans",
    "coconut": "Coconut",
    "coconut-oil": "Coconut Oil",
    "coconut-seed": "Coconut Seed",
    "coffee": "Coffee",
    "colacasia": "Colocasia (Chembu)",
    "copra": "Copra",
    "coriander": "Coriander Leaves",
    "cowpea": "Cowpea (Lobia/Karamani)",
    "cowpea-veg": "Cowpea (Vegetable)",
    "cucumber": "Cucumber",
    "drumstick": "Drumstick",
    "duster-beans": "Duster Beans",
    "yam-suran": "Elephant Yam (Suran/Chena)",
    "field-pea": "Field Pea",
    "french-beans": "French Beans",
    "galgal-lemon": "Galgal (Lemon)",
    "garlic": "Garlic",
    "ginger": "Ginger",
    "grapes": "Grapes",
    "green-avare": "Green Avare",
    "green-chilli": "Green Chilli",
    "green-gram": "Green Gram / Moong (Whole)",
    "green-peas": "Green Peas",
    "indian-beans": "Indian Beans (Seam)",
    "kabuli-chana": "Kabuli Chana (White Chickpeas)",
    "lemon": "Lemon",
    "lime": "Lime",
    "little-gourd": "Little Gourd (Kundru)",
    "long-melon": "Long Melon (Kakri)",
    "mango": "Mango",
    "mushroom": "Mushroom",
    "onion": "Onion",
    "orange": "Orange",
    "paddy": "Paddy (Rice)",
    "papaya": "Papaya",
    "pepper-garbled": "Black Pepper (Garbled)",
    "pineapple": "Pineapple",
    "potato": "Potato",
    "pumpkin": "Pumpkin",
    "red-gram": "Red Gram / Arhar / Tur (Whole)",
    "ridge-gourd": "Ridge Gourd",
    "rubber": "Rubber",
    "snake-gourd": "Snake Gourd",
    "sweet-potato": "Sweet Potato",
    "tapioca": "Tapioca",
    "tomato": "Tomato",
    "watermelon": "Watermelon",
    "yam-ratalu": "Yam (Ratalu)",
    "alsandikai": "Alsandikai (Long Beans)",
    "amla": "Amla (Indian Gooseberry)",

    "papaya-raw": "Papaya (Raw)",
}

CATEGORY_OVERRIDES = {
    "amaranthus": "Vegetables", "amaranthus-red": "Vegetables", "ashgourd": "Vegetables",
    "beetroot": "Vegetables", "bhindi": "Vegetables", "bitter-gourd": "Vegetables",
    "bottle-gourd": "Vegetables", "brinjal": "Vegetables", "cabbage": "Vegetables",
    "capsicum": "Vegetables", "carrot": "Vegetables", "cauliflower": "Vegetables",
    "cluster-beans": "Vegetables", "colacasia": "Vegetables", "cowpea-veg": "Vegetables",
    "cucumber": "Vegetables", "drumstick": "Vegetables", "duster-beans": "Vegetables",
    "french-beans": "Vegetables", "green-avare": "Vegetables", "green-peas": "Vegetables",
    "indian-beans": "Vegetables", "little-gourd": "Vegetables", "onion": "Vegetables",
    "papaya-raw": "Vegetables", "potato": "Vegetables", "pumpkin": "Vegetables",
    "ridge-gourd": "Vegetables", "snake-gourd": "Vegetables", "sweet-potato": "Vegetables",
    "tapioca": "Vegetables", "tomato": "Vegetables", "yam-ratalu": "Vegetables",
    "yam-suran": "Vegetables", "alsandikai": "Vegetables", "mushroom": "Vegetables",
    "apple": "Fruits", "banana": "Fruits", "banana-green": "Fruits", "grapes": "Fruits",
    "lemon": "Fruits", "lime": "Fruits", "mango": "Fruits",
    "orange": "Fruits", "papaya": "Fruits", "pineapple": "Fruits", "sapota": "Fruits",
    "watermelon": "Fruits", "amla": "Fruits", "galgal-lemon": "Fruits", "long-melon": "Fruits",
    "field-pea": "Vegetables",
    "black-pepper": "Spices", "pepper-garbled": "Spices", "red-chilli": "Spices",
    "green-chilli": "Spices", "garlic": "Spices", "ginger": "Spices", "coriander": "Spices",
    "bengal-gram": "Grains & Pulses", "black-gram": "Grains & Pulses", "cowpea": "Grains & Pulses",
    "green-gram": "Grains & Pulses", "kabuli-chana": "Grains & Pulses", "red-gram": "Grains & Pulses",
    "paddy": "Grains & Pulses",
    "arecanut": "Plantation Crops", "coconut": "Plantation Crops", "coconut-seed": "Plantation Crops",
    "coconut-oil": "Plantation Crops", "coffee": "Plantation Crops", "copra": "Plantation Crops",
    "rubber": "Plantation Crops",
}

# DMI's standard mandi price basis is per quintal. Egg is handled as the
# known app-specific exception. Unit is never guessed by the LLM.
UNIT_OVERRIDES = {"egg": "per 100 pieces"}
DEFAULT_UNIT = "per quintal"

COMMODITY_TO_PRODUCT = {
    commodity: product_id
    for product_id, commodities in TARGET_PRODUCTS.items()
    for commodity in commodities
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warning] Could not read {path}: {exc}")
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp.replace(path)


def load_existing_manifest() -> dict:
    data = load_json(MANIFEST_PATH)
    products = data.get("products", [])
    if not isinstance(products, list):
        return {}
    return {
        item.get("id"): item
        for item in products
        if isinstance(item, dict) and item.get("id")
    }


def slugify_commodity(raw_commodity: str) -> str:
    s = raw_commodity.strip().lower()
    keep = []
    for ch in s:
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -/()":
            keep.append("-")
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "unknown"


def discover_new_commodities(raw_records: list) -> dict:
    new_products = {}
    seen_raw = set(COMMODITY_TO_PRODUCT)

    for record in raw_records:
        commodity = (record.get("Commodity") or "").strip()
        if not commodity or commodity in seen_raw:
            continue

        product_id = slugify_commodity(commodity)

        if product_id not in TARGET_PRODUCTS:
            TARGET_PRODUCTS[product_id] = [commodity]
            PRODUCT_NAMES[product_id] = commodity
            new_products[product_id] = commodity
        elif commodity not in TARGET_PRODUCTS[product_id]:
            TARGET_PRODUCTS[product_id].append(commodity)

        COMMODITY_TO_PRODUCT[commodity] = product_id
        seen_raw.add(commodity)

    return new_products


def get_unit(product_id: str) -> str:
    return UNIT_OVERRIDES.get(product_id, DEFAULT_UNIT)


def parse_arrival_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), "%d/%m/%Y").date().isoformat()


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def make_row_id(product_id: str, market: str, date_iso: str) -> str:
    digest = hashlib.sha1(f"{product_id}-{market}-{date_iso}".encode()).hexdigest()[:10]
    return f"{product_id}-{digest}"


def load_existing_rows(product_id: str) -> list:
    data = load_json(PRICES_DIR / f"{product_id}.json")
    rows = data.get("history", [])
    return rows if isinstance(rows, list) else []


def fetch_kerala_csv() -> list:
    if not API_KEY:
        raise RuntimeError("DATA_GOV_IN_API_KEY is not configured.")

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format": "csv",
                    "limit": "all",
                    "filters[state]": STATE,
                },
                headers={"User-Agent": "agri-price-tracker/1.0"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
            return list(csv.DictReader(io.StringIO(response.text)))
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"[fetch] {exc}; retrying in {backoff}s")
            time.sleep(backoff)

    raise RuntimeError(f"Gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def classify_with_nvidia(product_name: str) -> str | None:
    if not NVIDIA_API_KEY:
        print(f"[ai] NVIDIA_API_KEY missing; category remains null for {product_name}")
        return None

    prompt = (
        "Classify this agricultural commodity into exactly one category: "
        "Vegetables, Fruits, Spices, Grains & Pulses, Plantation Crops. "
        f"Commodity: {product_name}. Respond with only the category name."
    )

    payload = {
        "model": NVIDIA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                NVIDIA_BASE_URL,
                headers=headers,
                json=payload,
                timeout=NVIDIA_TIMEOUT_SEC,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"].strip()
            normalized = text.lower()
            for category in DISPLAYABLE_CATEGORIES:
                if normalized == category.lower() or category.lower() in normalized:
                    return category
            print(f"[ai] Invalid category for {product_name}: {text!r}")
            return None
        except Exception as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"[ai] {exc}; retrying in {backoff}s")
            time.sleep(backoff)

    print(f"[ai] Classification failed for {product_name}: {last_error}")
    return None


def get_category(product_id: str, product_name: str, old_entry: dict) -> str | None:
    if product_id in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[product_id]

    old_category = old_entry.get("category") if isinstance(old_entry, dict) else None
    if old_category in DISPLAYABLE_CATEGORIES:
        return old_category

    return classify_with_nvidia(product_name)


def main() -> None:
    if not API_KEY:
        print("Missing DATA_GOV_IN_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching {STATE} mandi prices...")
    raw_records = fetch_kerala_csv()
    if not raw_records:
        raise RuntimeError("No records fetched - aborting.")

    existing_manifest = load_existing_manifest()
    newly_discovered = discover_new_commodities(raw_records)

    if newly_discovered:
        print(f"[discover] Auto-registered {len(newly_discovered)} new commodities")
        for product_id, name in newly_discovered.items():
            print(f"  {product_id}: {name}")

    new_rows_by_product = defaultdict(list)

    for record in raw_records:
        commodity = (record.get("Commodity") or "").strip()
        product_id = COMMODITY_TO_PRODUCT.get(commodity)
        if not product_id:
            continue

        try:
            date_iso = parse_arrival_date(record.get("Arrival_Date", ""))
        except ValueError:
            continue

        market = (record.get("Market") or "").strip()
        unit = get_unit(product_id)

        new_rows_by_product[product_id].append({
            "id": make_row_id(product_id, market, date_iso),
            "market": market,
            "district": (record.get("District") or "").strip(),
            "date": date_iso,
            "min_price": to_float(record.get("Min_x0020_Price")),
            "max_price": to_float(record.get("Max_x0020_Price")),
            "modal_price": to_float(record.get("Modal_x0020_Price")),
            "unit": unit,
            "source": "Agmarknet / data.gov.in",
        })

    cutoff = (
        datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)
    ).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest_products = []

    for product_id, product_name in PRODUCT_NAMES.items():
        old_entry = existing_manifest.get(product_id, {})
        category = get_category(product_id, product_name, old_entry)

        existing_rows = load_existing_rows(product_id)
        merged_by_id = {
            row.get("id"): row
            for row in existing_rows
            if isinstance(row, dict)
            and row.get("id")
            and row.get("date", "") >= cutoff
        }

        for row in new_rows_by_product.get(product_id, []):
            merged_by_id[row["id"]] = row

        merged = sorted(
            merged_by_id.values(),
            key=lambda row: row.get("date", ""),
            reverse=True,
        )

        product_unit = next(
            (row.get("unit") for row in merged if row.get("unit")),
            get_unit(product_id),
        )

        save_json(PRICES_DIR / f"{product_id}.json", {
            "product": product_id,
            "product_name": product_name,
            "generated_at": now_iso,
            "state": STATE,
            "unit": product_unit,
            "history": merged,
        })

        dates = sorted(
            {row["date"] for row in merged if row.get("date")},
            reverse=True,
        )
        today_date = dates[0] if dates else None
        yesterday_date = dates[1] if len(dates) > 1 else None

        def average_for(date_value):
            if not date_value:
                return None
            values = [
                row["modal_price"]
                for row in merged
                if row.get("date") == date_value
                and row.get("modal_price") is not None
            ]
            return round(sum(values) / len(values), 2) if values else None

        today_avg = average_for(today_date)
        yesterday_avg = average_for(yesterday_date)

        change = None
        pct_change = None
        if today_avg is not None and yesterday_avg not in (None, 0):
            change = round(today_avg - yesterday_avg, 2)
            pct_change = round((change / yesterday_avg) * 100, 1)

        # Image is manually maintained in manifest.json. New products start
        # with null and are never auto-populated by the pipeline.
        image_url = old_entry.get("image_url") if isinstance(old_entry, dict) else None

        manifest_products.append({
            "id": product_id,
            "name": product_name,
            "category": category,
            "image_url": image_url or None,
            "unit": product_unit,
            "today_date": today_date,
            "today_avg_price": today_avg,
            "yesterday_date": yesterday_date,
            "yesterday_avg_price": yesterday_avg,
            "change": change,
            "pct_change": pct_change,
            "market_count": (
                sum(
                    1
                    for row in merged
                    if row.get("date") == today_date
                    and row.get("modal_price") is not None
                )
                if today_date
                else 0
            ),
            "history_days": len(dates),
        })

    manifest_products.sort(
        key=lambda item: (
            item.get("category") is None,
            item.get("category") or "",
            item.get("name") or "",
        )
    )

    save_json(MANIFEST_PATH, {
        "generated_at": now_iso,
        "source": "Agmarknet / data.gov.in, Government of India",
        "state": STATE,
        "products": manifest_products,
    })

    print("========================================")
    print("PIPELINE COMPLETE")
    print("========================================")
    print(f"Products in manifest : {len(manifest_products)}")
    print(f"With today's price  : {sum(1 for p in manifest_products if p['today_avg_price'] is not None)}")
    print(f"With manual image   : {sum(1 for p in manifest_products if p['image_url'])}")
    print(f"Uncategorized       : {sum(1 for p in manifest_products if p['category'] is None)}")


if __name__ == "__main__":
    main()
