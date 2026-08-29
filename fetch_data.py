
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
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

REQUEST_TIMEOUT_SEC = 90
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5
RETENTION_DAYS = 60

# Kerala-only for now. (API's state filter value is "Keralam", not "Kerala".)
STATE = "Keralam"

# Maps our app's product id -> list of exact commodity name(s) the API uses.
# NOTE: verify these against a live pull before relying on them - AGMARKNET
# commodity strings are exact-match and vary slightly (spacing, parentheses).
# See the diagnostic snippet below the script for how to check.
TARGET_PRODUCTS = {
    # Vegetables
    "tomato": ["Tomato"],
    "onion": ["Onion"],
    "potato": ["Potato"],
    "carrot": ["Carrot"],
    "cabbage": ["Cabbage"],
    "cauliflower": ["Cauliflower"],
    "beans": ["French Beans(Frasbean)"],
    "cluster-beans": ["Cluster beans"],
    "brinjal": ["Brinjal"],
    "bhindi": ["Bhindi(Ladies Finger)"],
    "cucumber": ["Cucumbar(Kheera)"],
    "bottle-gourd": ["Bottle gourd"],
    "bitter-gourd": ["Bitter gourd"],
    "ridge-gourd": ["Ridgeguard(Tori)"],
    "pumpkin": ["Pumpkin"],
    "ash-gourd": ["Ashgourd"],
    "drumstick": ["Drumstick"],
    "beetroot": ["Beetroot"],
    "green-chilli": ["Green Chilli"],
    "capsicum": ["Capsicum"],
    "ginger": ["Ginger(Green)"],
    "garlic": ["Garlic"],
    "spinach": ["Spinach"],
    "amaranthus": ["Amaranthus"],
    "colocasia": ["Colocosia"],

    # Fruits
    "banana": ["Banana"],
    "banana-green": ["Banana - Green"],
    "pineapple": ["Pineapple"],
    "papaya": ["Papaya"],
    "mango": ["Mango"],
    "watermelon": ["Water Melon"],
    "muskmelon": ["Muskmelon"],
    "orange": ["Orange"],
    "sweet-lime": ["Mousambi(Sweet Lime)"],
    "lemon": ["Lemon"],
    "grapes": ["Grapes"],
    "apple": ["Apple"],
    "pomegranate": ["Pomegranate"],
    "guava": ["Guava"],
    "jackfruit": ["Jack Fruit"],

    # Tubers / roots
    "tapioca": ["Tapioca"],
    "sweet-potato": ["Sweet Potato"],
    "yam": ["Elephant Yam (Suran)"],

    # Others / plantation crops
    "coconut": ["Coconut"],
    "arecanut": ["Arecanut(Betelnut/Supari)"],
    "coffee": ["Coffee"],
    "pepper": ["Pepper garbled"],
    "cardamom": ["Cardamom"],
    "turmeric": ["Turmeric"],
    "groundnut": ["Groundnut"],
    "rice": ["Paddy(Dhan)(Common)"],
    "wheat": ["Wheat"],
}

PRODUCT_NAMES = {
    "tomato": "Tomato",
    "onion": "Onion",
    "potato": "Potato",
    "carrot": "Carrot",
    "cabbage": "Cabbage",
    "cauliflower": "Cauliflower",
    "beans": "French Beans",
    "cluster-beans": "Cluster Beans",
    "brinjal": "Brinjal",
    "bhindi": "Bhindi (Ladies Finger)",
    "cucumber": "Cucumber",
    "bottle-gourd": "Bottle Gourd",
    "bitter-gourd": "Bitter Gourd",
    "ridge-gourd": "Ridge Gourd",
    "pumpkin": "Pumpkin",
    "ash-gourd": "Ash Gourd",
    "drumstick": "Drumstick",
    "beetroot": "Beetroot",
    "green-chilli": "Green Chilli",
    "capsicum": "Capsicum",
    "ginger": "Ginger",
    "garlic": "Garlic",
    "spinach": "Spinach",
    "amaranthus": "Amaranthus (Cheera)",
    "colocasia": "Colocasia (Chembu)",
    "banana": "Banana",
    "banana-green": "Banana (Green)",
    "pineapple": "Pineapple",
    "papaya": "Papaya",
    "mango": "Mango",
    "watermelon": "Watermelon",
    "muskmelon": "Muskmelon",
    "orange": "Orange",
    "sweet-lime": "Sweet Lime (Mosambi)",
    "lemon": "Lemon",
    "grapes": "Grapes",
    "apple": "Apple",
    "pomegranate": "Pomegranate",
    "guava": "Guava",
    "jackfruit": "Jackfruit",
    "tapioca": "Tapioca",
    "sweet-potato": "Sweet Potato",
    "yam": "Elephant Yam (Chena)",
    "coconut": "Coconut",
    "arecanut": "Arecanut",
    "coffee": "Coffee",
    "pepper": "Black Pepper",
    "cardamom": "Cardamom",
    "turmeric": "Turmeric",
    "groundnut": "Groundnut",
    "rice": "Rice (Paddy)",
    "wheat": "Wheat",
}

COMMODITY_TO_PRODUCT = {
    commodity: product_id
    for product_id, commodities in TARGET_PRODUCTS.items()
    for commodity in commodities
}


def fetch_kerala_csv() -> list:
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format": "csv",
                    "limit": "all",
                    "filters[state]": STATE,
                },
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
                timeout=REQUEST_TIMEOUT_SEC,
            )
            if not resp.ok:
                print(f"  [diagnostic] status={resp.status_code} body_len={len(resp.text)}")
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            reader = csv.DictReader(io.StringIO(resp.text))
            return list(reader)

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Timeout, attempt {attempt}/{MAX_ATTEMPTS}; retrying in {backoff}s")
            time.sleep(backoff)
        except RuntimeError as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Error ({e}); retrying in {backoff}s")
            time.sleep(backoff)

    raise RuntimeError(f"Gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def parse_arrival_date(raw: str) -> str:
    """API gives DD/MM/YYYY - convert to ISO YYYY-MM-DD for consistent sorting/comparison."""
    dt = datetime.strptime(raw.strip(), "%d/%m/%Y")
    return dt.date().isoformat()


def make_row_id(product_id: str, market: str, date_iso: str) -> str:
    h = hashlib.sha1(f"{product_id}-{market}-{date_iso}".encode()).hexdigest()[:10]
    return f"{product_id}-{h}"


def load_existing_rows(product_id: str) -> list:
    path = PRICES_DIR / f"{product_id}.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f).get("history", [])


def main():
    if not API_KEY:
        print("Missing DATA_GOV_IN_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching {STATE} mandi prices...")
    raw_records = fetch_kerala_csv()
    print(f"Fetched {len(raw_records)} raw {STATE} records")

    if not raw_records:
        print("No records fetched - aborting without overwriting existing data.", file=sys.stderr)
        sys.exit(1)

    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()

    new_rows_by_product = defaultdict(list)
    unmatched_commodities = set()

    for r in raw_records:
        commodity = (r.get("Commodity") or "").strip()
        product_id = COMMODITY_TO_PRODUCT.get(commodity)
        if not product_id:
            unmatched_commodities.add(commodity)
            continue

        try:
            date_iso = parse_arrival_date(r.get("Arrival_Date", ""))
        except ValueError:
            continue

        def to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        market = (r.get("Market") or "").strip()
        row = {
            "id": make_row_id(product_id, market, date_iso),
            "market": market,
            "district": (r.get("District") or "").strip(),
            "date": date_iso,
            "min_price": to_float(r.get("Min_x0020_Price")),
            "max_price": to_float(r.get("Max_x0020_Price")),
            "modal_price": to_float(r.get("Modal_x0020_Price")),
            "unit": "per quintal",
            "source": "Agmarknet / data.gov.in",
        }
        new_rows_by_product[product_id].append(row)

    if unmatched_commodities:
        print(f"\n(info) {len(unmatched_commodities)} commodities in today's pull "
              f"aren't mapped to a product yet - ignored: {sorted(unmatched_commodities)[:15]}...")

    manifest_products = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for product_id, product_name in PRODUCT_NAMES.items():
        existing = load_existing_rows(product_id)
        existing_ids = {row["id"] for row in existing}

        merged = [row for row in existing if row["date"] >= cutoff]
        for row in new_rows_by_product.get(product_id, []):
            if row["id"] not in existing_ids:
                merged.append(row)
                existing_ids.add(row["id"])

        merged.sort(key=lambda r: r["date"], reverse=True)

        with open(PRICES_DIR / f"{product_id}.json", "w") as f:
            json.dump(
                {
                    "product": product_id,
                    "product_name": product_name,
                    "generated_at": now_iso,
                    "state": STATE,
                    "history": merged,
                },
                f, indent=2, ensure_ascii=False,
            )

        dates_available = sorted({row["date"] for row in merged}, reverse=True)
        today_avg = yesterday_avg = None
        today_date = yesterday_date = None
        market_count = 0

        if dates_available:
            today_date = dates_available[0]
            today_rows = [r for r in merged if r["date"] == today_date and r["modal_price"] is not None]
            if today_rows:
                today_avg = round(sum(r["modal_price"] for r in today_rows) / len(today_rows), 2)
                market_count = len(today_rows)

        if len(dates_available) > 1:
            yesterday_date = dates_available[1]
            yesterday_rows = [r for r in merged if r["date"] == yesterday_date and r["modal_price"] is not None]
            if yesterday_rows:
                yesterday_avg = round(sum(r["modal_price"] for r in yesterday_rows) / len(yesterday_rows), 2)

        change = None
        pct_change = None
        if today_avg is not None and yesterday_avg is not None and yesterday_avg != 0:
            change = round(today_avg - yesterday_avg, 2)
            pct_change = round((change / yesterday_avg) * 100, 1)

        manifest_products.append({
            "id": product_id,
            "name": product_name,
            "unit": "per quintal",
            "today_date": today_date,
            "today_avg_price": today_avg,
            "yesterday_date": yesterday_date,
            "yesterday_avg_price": yesterday_avg,
            "change": change,
            "pct_change": pct_change,
            "market_count": market_count,
            "history_days": len(dates_available),
        })

    with open(MANIFEST_PATH, "w") as f:
        json.dump(
            {"generated_at": now_iso, "source": "Agmarknet / data.gov.in, Government of India",
             "state": STATE,
             "products": manifest_products},
            f, indent=2, ensure_ascii=False,
        )

    covered = len([p for p in manifest_products if p["today_avg_price"] is not None])
    print(f"\nWrote {len(PRODUCT_NAMES)} product files "
          f"({covered} have today's data) and manifest.json")


if __name__ == "__main__":
    main()
