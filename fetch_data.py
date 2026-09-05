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

# The Kerala resource can be large enough for the API gateway to time out
# when asked for every row in a single CSV response. Fetch in bounded pages
# instead. Keep a generous per-page timeout because the data.gov.in endpoint
# can occasionally be slow.
REQUEST_TIMEOUT_SEC = 90
PAGE_SIZE = int(os.environ.get("DATA_GOV_PAGE_SIZE", "1000"))
MAX_PAGES = int(os.environ.get("DATA_GOV_MAX_PAGES", "100"))
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
    "amaranthus-red": "Amaranthus Red",
    "apple": "Apple",
    "arecanut": "Arecanut",
    "ashgourd": "Ashgourd",
    "banana": "Banana",
    "banana-green": "Banana Green",
    "beetroot": "Beetroot",
    "bengal-gram": "Bengal Gram",
    "bhindi": "Bhindi (Ladies Finger)",
    "bitter-gourd": "Bitter Gourd",
    "black-gram": "Black Gram",
    "black-pepper": "Black Pepper",
    "bottle-gourd": "Bottle Gourd",
    "brinjal": "Brinjal",
    "cabbage": "Cabbage",
    "capsicum": "Capsicum",
    "carrot": "Carrot",
    "cauliflower": "Cauliflower",
    "sapota": "Chikoos (Sapota)",
    "red-chilli": "Chili Red",
    "cluster-beans": "Cluster Beans",
    "coconut": "Coconut",
    "coconut-oil": "Coconut Oil",
    "coconut-seed": "Coconut Seed",
    "coffee": "Coffee",
    "colacasia": "Colacasia",
    "copra": "Copra",
    "coriander": "Coriander Leaves",
    "cowpea": "Cowpea",
    "cowpea-veg": "Cowpea Vegetable",
    "cucumber": "Cucumber",
    "drumstick": "Drumstick",
    "duster-beans": "Duster Beans",
    "yam-suran": "Elephant Yam (Suran)",
    "field-pea": "Field Pea",
    "french-beans": "French Beans",
    "galgal-lemon": "Galgal Lemon",
    "garlic": "Garlic",
    "ginger": "Ginger Green",
    "grapes": "Grapes",
    "green-avare": "Green Avare",
    "green-chilli": "Green Chilli",
    "green-gram": "Green Gram",
    "green-peas": "Green Peas",
    "indian-beans": "Indian Beans",
    "kabuli-chana": "Kabuli Chana",
    "lemon": "Lemon",
    "lime": "Lime",
    "little-gourd": "Little Gourd",
    "long-melon": "Long Melon",
    "mango": "Mango",
    "mushroom": "Mushroom",
    "onion": "Onion",
    "orange": "Orange",
    "paddy": "Paddy",
    "papaya": "Papaya",
    "pepper-garbled": "Pepper Garbled",
    "pineapple": "Pineapple",
    "potato": "Potato",
    "pumpkin": "Pumpkin",
    "red-gram": "Red Gram",
    "ridge-gourd": "Ridge Gourd",
    "rubber": "Rubber",
    "snake-gourd": "Snake Gourd",
    "sweet-potato": "Sweet Potato",
    "tapioca": "Tapioca",
    "tomato": "Tomato",
    "watermelon": "Water Melon",
    "yam-ratalu": "Yam Ratalu",
    "alsandikai": "Alsandikai",
    "amla": "Amla (Indian Gooseberry)",
    "papaya-raw": "Papaya Raw",
}

# Existing mappings are retained below exactly as configured by the repo.
COMMODITY_TO_PRODUCT = {}
UNIT_OVERRIDES = {}
DEFAULT_UNIT = "per quintal"
CATEGORY_OVERRIDES = {}


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_existing_manifest() -> dict:
    data = load_json(MANIFEST_PATH)
    return {
        item.get("id"): item
        for item in data.get("products", [])
        if isinstance(item, dict) and item.get("id")
    }


def normalize_product_id(name: str) -> str:
    value = name.strip().lower()
    value = value.replace("&", "and")
    value = "".join(ch if ch.isalnum() else "-" for ch in value)
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-") or "unknown"


def discover_new_commodities(records: list) -> dict:
    new_products = {}
    seen_raw = set(COMMODITY_TO_PRODUCT)
    for record in records:
        commodity = (record.get("Commodity") or "").strip()
        if not commodity or commodity in seen_raw:
            continue
        product_id = normalize_product_id(commodity)
        suffix = 2
        base_id = product_id
        while product_id in PRODUCT_NAMES or product_id in new_products:
            product_id = f"{base_id}-{suffix}"
            suffix += 1
        new_products[product_id] = commodity
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

    all_records = []

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        page_records = None
        last_error = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = requests.get(
                    BASE_URL,
                    params={
                        "api-key": API_KEY,
                        "format": "csv",
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "filters[state]": STATE,
                    },
                    headers={"User-Agent": "agri-price-tracker/1.1"},
                    timeout=REQUEST_TIMEOUT_SEC,
                )
                response.raise_for_status()
                page_records = list(csv.DictReader(io.StringIO(response.text)))
                break
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                print(
                    f"[fetch] page={page + 1} offset={offset} {exc}; "
                    f"retrying in {backoff}s"
                )
                time.sleep(backoff)

        if page_records is None:
            raise RuntimeError(
                f"Failed to fetch page {page + 1} after {MAX_ATTEMPTS} attempts: {last_error}"
            )

        if not page_records:
            break

        all_records.extend(page_records)
        print(
            f"[fetch] page={page + 1} offset={offset}: "
            f"{len(page_records)} records (total={len(all_records)})"
        )

        if len(page_records) < PAGE_SIZE:
            break

    if not all_records:
        raise RuntimeError("No records fetched from data.gov.in")

    return all_records


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
            PRODUCT_NAMES.setdefault(product_id, name)
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
                )
                if today_date
                else 0
            ),
            "history_days": len(dates),
        })

    save_json(MANIFEST_PATH, {
        "generated_at": now_iso,
        "source": "Agmarknet / data.gov.in, Government of India",
        "state": STATE,
        "products": sorted(manifest_products, key=lambda item: item["name"].lower()),
    })

    print(
        f"Done: processed {len(raw_records)} records for {len(manifest_products)} products."
    )


if __name__ == "__main__":
    main()
