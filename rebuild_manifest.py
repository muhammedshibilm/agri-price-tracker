import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_PATH = DATA_DIR / "manifest.json"

DEFAULT_UNIT = "per quintal"
UNIT_OVERRIDES = {"egg": "per 100 pieces"}

# Old filenames that represent the same product as the canonical app id.
LEGACY_ALIASES = {
    "ash-gourd": "ashgourd",
    "beans": "french-beans",
    "pepper": "black-pepper",
}

CATEGORY_OVERRIDES = {
    # Vegetables
    "alsandikai": "Vegetables",
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
    "field-pea": "Vegetables",
    "french-beans": "Vegetables",
    "green-avare": "Vegetables",
    "green-peas": "Vegetables",
    "indian-beans": "Vegetables",
    "little-gourd": "Vegetables",
    "mint-pudina": "Vegetables",
    "mushroom": "Vegetables",
    "onion": "Vegetables",
    "papaya-raw": "Vegetables",
    "potato": "Vegetables",
    "pumpkin": "Vegetables",
    "raddish": "Vegetables",
    "ridge-gourd": "Vegetables",
    "seemebadnekai": "Vegetables",
    "snake-gourd": "Vegetables",
    "spinach": "Vegetables",
    "sweet-potato": "Vegetables",
    "tapioca": "Vegetables",
    "tomato": "Vegetables",
    "yam-ratalu": "Vegetables",
    "yam-suran": "Vegetables",

    # Fruits
    "amla": "Fruits",
    "apple": "Fruits",
    "banana": "Fruits",
    "banana-green": "Fruits",
    "galgal-lemon": "Fruits",
    "grapes": "Fruits",
    "lemon": "Fruits",
    "lime": "Fruits",
    "long-melon": "Fruits",
    "mango": "Fruits",
    "mango-raw-ripe": "Fruits",
    "orange": "Fruits",
    "papaya": "Fruits",
    "pineapple": "Fruits",
    "sapota": "Fruits",
    "watermelon": "Fruits",

    # Grains & pulses
    "bengal-gram": "Grains & Pulses",
    "black-gram": "Grains & Pulses",
    "cowpea": "Grains & Pulses",
    "green-gram": "Grains & Pulses",
    "kabuli-chana": "Grains & Pulses",
    "paddy": "Grains & Pulses",
    "red-gram": "Grains & Pulses",
    "rice": "Grains & Pulses",

    # Spices
    "black-pepper": "Spices",
    "pepper-garbled": "Spices",
    "coriander": "Spices",
    "garlic": "Spices",
    "ginger": "Spices",
    "ginger-dry": "Spices",
    "green-chilli": "Spices",
    "red-chilli": "Spices",

    # Plantation crops
    "arecanut": "Plantation Crops",
    "cashewnuts": "Plantation Crops",
    "coconut": "Plantation Crops",
    "coconut-oil": "Plantation Crops",
    "coconut-seed": "Plantation Crops",
    "coffee": "Plantation Crops",
    "copra": "Plantation Crops",
    "rubber": "Plantation Crops",
    "tender-coconut": "Plantation Crops",
}


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warning] Could not read {path}: {exc}")
        return {}


def save_json(path: Path, data: dict):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp.replace(path)


def load_manifest():
    data = load_json(MANIFEST_PATH)
    products = data.get("products", [])
    if not isinstance(products, list):
        return {}
    return {
        item["id"]: item
        for item in products
        if isinstance(item, dict) and item.get("id")
    }


def canonical_id(product_id: str) -> str:
    return LEGACY_ALIASES.get(product_id, product_id)


def average_for(rows, date_value):
    values = [
        row.get("modal_price")
        for row in rows
        if row.get("date") == date_value and row.get("modal_price") is not None
    ]
    return round(sum(values) / len(values), 2) if values else None


def main():
    existing_manifest = load_manifest()
    rows_by_product = defaultdict(dict)
    names = {}

    # The prices directory is the source of truth for which products exist.
    # Empty history files are intentionally excluded from the app catalog.
    for path in sorted(PRICES_DIR.glob("*.json")):
        product_id = path.stem
        data = load_json(path)
        history = data.get("history", [])
        if not isinstance(history, list) or not history:
            print(f"[skip] {path.name}: empty history")
            continue

        target = canonical_id(product_id)
        if data.get("product_name"):
            names.setdefault(target, data["product_name"])

        for row in history:
            if not isinstance(row, dict) or not row.get("id") or not row.get("date"):
                continue
            row = dict(row)
            # Keep IDs stable when merging an old alias file into its canonical file.
            if product_id != target and str(row["id"]).startswith(product_id + "-"):
                row["id"] = target + str(row["id"])[len(product_id):]
            rows_by_product[target][row["id"]] = row

    manifest_products = []

    for product_id, row_map in rows_by_product.items():
        rows = sorted(
            row_map.values(),
            key=lambda row: row.get("date", ""),
            reverse=True,
        )
        old_entry = existing_manifest.get(product_id, {})
        product_name = (
            old_entry.get("name")
            or names.get(product_id)
            or product_id.replace("-", " ").title()
        )
        category = CATEGORY_OVERRIDES.get(product_id) or old_entry.get("category")

        unit = next(
            (row.get("unit") for row in rows if row.get("unit")),
            old_entry.get("unit") or UNIT_OVERRIDES.get(product_id, DEFAULT_UNIT),
        )
        if product_id in UNIT_OVERRIDES:
            unit = UNIT_OVERRIDES[product_id]

        dates = sorted({row["date"] for row in rows if row.get("date")}, reverse=True)
        today_date = dates[0] if dates else None
        yesterday_date = dates[1] if len(dates) > 1 else None
        today_avg = average_for(rows, today_date)
        yesterday_avg = average_for(rows, yesterday_date)

        change = None
        pct_change = None
        if today_avg is not None and yesterday_avg not in (None, 0):
            change = round(today_avg - yesterday_avg, 2)
            pct_change = round((change / yesterday_avg) * 100, 1)

        manifest_products.append({
            "id": product_id,
            "name": product_name,
            "category": category,
            "image_url": old_entry.get("image_url") or None,
            "unit": unit,
            "today_date": today_date,
            "today_avg_price": today_avg,
            "yesterday_date": yesterday_date,
            "yesterday_avg_price": yesterday_avg,
            "change": change,
            "pct_change": pct_change,
            "market_count": sum(
                1
                for row in rows
                if row.get("date") == today_date and row.get("modal_price") is not None
            ) if today_date else 0,
            "history_days": len(dates),
        })

    manifest_products.sort(
        key=lambda item: (
            item.get("category") is None,
            item.get("category") or "",
            item.get("name") or "",
        )
    )

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_json(MANIFEST_PATH, {
        "generated_at": now_iso,
        "source": "Agmarknet / data.gov.in, Government of India",
        "state": "Keralam",
        "products": manifest_products,
    })

    print("========================================")
    print("MANIFEST REBUILD COMPLETE")
    print("========================================")
    print(f"Products in manifest : {len(manifest_products)}")
    print(f"With price data      : {sum(1 for p in manifest_products if p['today_avg_price'] is not None)}")
    print(f"With manual image    : {sum(1 for p in manifest_products if p['image_url'])}")
    print(f"Uncategorized        : {sum(1 for p in manifest_products if p['category'] is None)}")
    print(f"Merged legacy files  : {', '.join(f'{k}->{v}' for k, v in LEGACY_ALIASES.items())}")


if __name__ == "__main__":
    main()
