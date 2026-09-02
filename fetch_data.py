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

from enrich_metadata import enrich_new_products, load_metadata, load_images

DATA_DIR = Path(__file__).parent / "data"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_PATH = DATA_DIR / "manifest.json"

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

REQUEST_TIMEOUT_SEC = 90
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5
RETENTION_DAYS = 7  # app only shows a 7-day trend, so no need to keep more

# Categories that actually count as "agriculture, farmer/market relevant"
# for this app. Anything Gemini classifies outside this set (e.g. "Other")
# is held back from the manifest - it still gets fetched/priced/stored,
# it just won't show up in the app until it's re-classified correctly.
DISPLAYABLE_CATEGORIES = {
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
}

# Kerala-only for now. (API's state filter value is "Keralam", not "Kerala".)
STATE = "Keralam"

# Maps our app's product id -> exact commodity name string the API uses.
# Verified against a live pull on 2026-08-29. Do not guess-add entries here -
# if a commodity isn't in a real CSV pull, it won't match and will just sit
# as null in the manifest.
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
    "mango-raw-ripe": ["Mango(Raw-Ripe)"],
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
    "egg": ["Egg"],
    "papaya-raw": ["Papaya(Raw)"],
}

# Clean display names for the app UI (raw AGMARKNET strings are kept only
# in TARGET_PRODUCTS for exact-match filtering).
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
    "mango-raw-ripe": "Mango (Raw/Ripe)",
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
    "egg": "Egg",
    "papaya-raw": "Papaya (Raw)",
}

COMMODITY_TO_PRODUCT = {
    commodity: product_id
    for product_id, commodities in TARGET_PRODUCTS.items()
    for commodity in commodities
}


def slugify_commodity(raw_commodity: str) -> str:
    """Turn a raw AGMARKNET commodity string into a stable product id.
    Deterministic - same commodity string always yields the same id, so we
    don't need a separate persisted registry just to keep ids stable."""
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
    """Find commodities in this pull that aren't in TARGET_PRODUCTS yet and
    auto-register them (id + raw-name mapping). Returns a dict of
    {product_id: display_name} for the newly discovered ones only.

    Registering a commodity here is what makes the rest of the pipeline
    (price storage, category classification, image lookup, manifest
    generation) automatically pick it up - nothing else needs to be
    touched by hand for a brand-new commodity to eventually show up in
    the app."""
    new_products = {}
    seen_raw = {c for commodities in TARGET_PRODUCTS.values() for c in commodities}

    for r in raw_records:
        commodity = (r.get("Commodity") or "").strip()
        if not commodity or commodity in seen_raw:
            continue
        product_id = slugify_commodity(commodity)
        if product_id in TARGET_PRODUCTS or product_id in new_products:
            # already known under this id (or duplicate raw string this pull) - just map it
            TARGET_PRODUCTS.setdefault(product_id, [])
            if commodity not in TARGET_PRODUCTS[product_id]:
                TARGET_PRODUCTS[product_id].append(commodity)
            COMMODITY_TO_PRODUCT[commodity] = product_id
            seen_raw.add(commodity)
            continue

        TARGET_PRODUCTS[product_id] = [commodity]
        COMMODITY_TO_PRODUCT[commodity] = product_id
        PRODUCT_NAMES[product_id] = commodity  # cleaned up later by Gemini's classification pass if needed
        new_products[product_id] = commodity
        seen_raw.add(commodity)

    return new_products

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

    # Auto-register any commodity the API returns that we've never seen
    # before - no more manual hardcoding. It gets an id + gets queued for
    # AI classification below; it will only show up in the app once it's
    # classified into a real agriculture category.
    newly_discovered = discover_new_commodities(raw_records)
    if newly_discovered:
        print(f"\n(info) Auto-registered {len(newly_discovered)} new commodity/ies: "
              f"{list(newly_discovered.values())}")

    new_rows_by_product = defaultdict(list)
    unmatched_commodities = set()

    for r in raw_records:
        commodity = (r.get("Commodity") or "").strip()
        product_id = COMMODITY_TO_PRODUCT.get(commodity)
        if not product_id:
            # Shouldn't normally happen now that discover_new_commodities
            # runs first, but kept as a safety net (e.g. blank commodity).
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
        # This is the signal to watch: if AGMARKNET renames/adds a commodity,
        # it'll show up here instead of failing silently.
        print(f"\n(info) {len(unmatched_commodities)} commodities in today's pull "
              f"aren't mapped to a product - ignored: {sorted(unmatched_commodities)}")

    # Classify anything missing category/image metadata - this covers both
    # brand-new auto-discovered commodities AND any of the original
    # hardcoded ones that haven't been classified yet. Runs BEFORE the
    # manifest is built so we can filter by category below. Also looks up
    # a Wikimedia Commons image for any product that doesn't have one yet.
    enrich_new_products(PRODUCT_NAMES)
    metadata = load_metadata()
    images = load_images()

    manifest_products = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    skipped_categories = []
    skipped_stale = []

    for product_id, product_name in PRODUCT_NAMES.items():
        entry = metadata.get(product_id)
        # If it HAS been classified and landed outside real agriculture
        # categories (e.g. "Other"), leave it out of the app entirely.
        # If it hasn't been classified yet, we still include it for now so
        # existing products never vanish just because enrichment is pending
        # or Gemini/Wikimedia had a hiccup this run.
        if entry is not None and entry.get("category") not in DISPLAYABLE_CATEGORIES:
            skipped_categories.append((product_id, entry.get("category")))
            continue
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

        # A commodity that AGMARKNET has stopped reporting on will have its
        # price history naturally age out of the RETENTION_DAYS window over
        # time (nothing new comes in to replace what expires). Once there's
        # no data left at all within that window, drop it from the manifest
        # instead of showing an entry with permanently null prices. The
        # per-product .json file above is still written (empty history), so
        # nothing is destroyed - it just stops appearing in the app until
        # the commodity reappears in a future pull.
        if not merged:
            skipped_stale.append(product_id)
            continue

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
            "category": entry.get("category") if entry else None,
            "image_url": images.get(product_id) or (entry.get("image_url") if entry else None),
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
    with_image = len([p for p in manifest_products if p["image_url"]])
    print(f"\nWrote {len(manifest_products)} displayable product files "
          f"({covered} have today's data, {with_image} have an image) and manifest.json")
    if skipped_categories:
        print(f"(info) {len(skipped_categories)} product(s) classified outside "
              f"farmer/market categories and left out of the app: {skipped_categories}")
    if skipped_stale:
        print(f"(info) {len(skipped_stale)} product(s) had no price data within the last "
              f"{RETENTION_DAYS} days and were dropped from the manifest: {skipped_stale}")


if __name__ == "__main__":
    main()
