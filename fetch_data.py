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

from enrich_metadata import (
    enrich_new_products,
    load_metadata,
    load_images,
)


# ============================================================================
# PATHS
# ============================================================================

DATA_DIR = Path(__file__).parent / "data"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_PATH = DATA_DIR / "manifest.json"

METADATA_PATH = DATA_DIR / "metadata.json"


# ============================================================================
# API
# ============================================================================

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"

BASE_URL = (
    f"https://api.data.gov.in/resource/{RESOURCE_ID}"
)


# ============================================================================
# SETTINGS
# ============================================================================

REQUEST_TIMEOUT_SEC = 90
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5

RETENTION_DAYS = 7

STATE = "Keralam"


# ============================================================================
# DISPLAYABLE CATEGORIES
# ============================================================================

DISPLAYABLE_CATEGORIES = {
    "Vegetables",
    "Fruits",
    "Spices",
    "Grains & Pulses",
    "Plantation Crops",
}


# ============================================================================
# IMPORTANT: PRODUCT UNITS
# ============================================================================
#
# DO NOT blindly convert every commodity to kg.
#
# Your old code did:
#
#     "unit": "per quintal"
#
# for EVERYTHING.
#
# This is why Egg ended up being interpreted by the app as:
#
#     9 / 100 = 0.09/kg
#
# Egg is not a quintal commodity in the same sense as vegetables/grains.
#
# Keep special units here.
#
# IMPORTANT:
# The exact Egg source unit should be confirmed from the AGMARKNET
# record/schema. We deliberately do NOT convert Egg's 9.0 into kg here.
#

PRODUCT_UNITS = {
    "egg": "per 100 pieces",
}


DEFAULT_UNIT = "per quintal"


# ============================================================================
# KEEP YOUR EXISTING TARGET_PRODUCTS HERE
# ============================================================================
#
# Paste your existing TARGET_PRODUCTS dictionary below this comment.
#
# Example:
#
# TARGET_PRODUCTS = {
#     "amaranthus": ["Amaranthus"],
#     ...
#     "egg": ["Egg"],
# }
#

TARGET_PRODUCTS = {
    # KEEP YOUR EXISTING COMPLETE TARGET_PRODUCTS DICTIONARY HERE
}


# ============================================================================
# KEEP YOUR EXISTING PRODUCT_NAMES HERE
# ============================================================================

PRODUCT_NAMES = {
    # KEEP YOUR EXISTING COMPLETE PRODUCT_NAMES DICTIONARY HERE
}


# ============================================================================
# COMMODITY -> PRODUCT
# ============================================================================

COMMODITY_TO_PRODUCT = {
    commodity: product_id
    for product_id, commodities in TARGET_PRODUCTS.items()
    for commodity in commodities
}


# ============================================================================
# HELPERS
# ============================================================================

def get_product_unit(product_id: str) -> str:
    return PRODUCT_UNITS.get(
        product_id,
        DEFAULT_UNIT,
    )


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

    seen_raw = {
        commodity
        for commodities in TARGET_PRODUCTS.values()
        for commodity in commodities
    }

    for record in raw_records:

        commodity = (
            record.get("Commodity") or ""
        ).strip()

        if not commodity:
            continue

        if commodity in seen_raw:
            continue

        product_id = slugify_commodity(
            commodity
        )

        if (
            product_id in TARGET_PRODUCTS
            or product_id in new_products
        ):

            TARGET_PRODUCTS.setdefault(
                product_id,
                [],
            )

            if (
                commodity
                not in TARGET_PRODUCTS[product_id]
            ):
                TARGET_PRODUCTS[product_id].append(
                    commodity
                )

            COMMODITY_TO_PRODUCT[
                commodity
            ] = product_id

            seen_raw.add(commodity)

            continue

        TARGET_PRODUCTS[product_id] = [
            commodity
        ]

        COMMODITY_TO_PRODUCT[
            commodity
        ] = product_id

        PRODUCT_NAMES[product_id] = commodity

        new_products[product_id] = commodity

        seen_raw.add(commodity)

    return new_products


# ============================================================================
# FETCH
# ============================================================================

def fetch_kerala_csv() -> list:

    if not API_KEY:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not configured."
        )

    last_error = None

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        try:

            response = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format": "csv",
                    "limit": "all",
                    "filters[state]": STATE,
                },
                headers={
                    "User-Agent": (
                        "agri-price-tracker/1.0 "
                        "(https://github.com/"
                        "muhammedshibilm/"
                        "agri-price-tracker)"
                    ),
                },
                timeout=REQUEST_TIMEOUT_SEC,
            )

            if not response.ok:

                print(
                    f"[diagnostic] HTTP "
                    f"{response.status_code}"
                )

                raise RuntimeError(
                    response.text[:300]
                )

            reader = csv.DictReader(
                io.StringIO(
                    response.text
                )
            )

            return list(reader)

        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        ) as error:

            last_error = error

            if attempt == MAX_ATTEMPTS:
                break

            backoff = (
                BASE_BACKOFF_SEC
                * (2 ** (attempt - 1))
            )

            print(
                f"Network error; retrying in "
                f"{backoff}s..."
            )

            time.sleep(backoff)

        except Exception as error:

            last_error = error

            if attempt == MAX_ATTEMPTS:
                break

            backoff = (
                BASE_BACKOFF_SEC
                * (2 ** (attempt - 1))
            )

            print(
                f"API error: {error}; "
                f"retrying in {backoff}s..."
            )

            time.sleep(backoff)

    raise RuntimeError(
        f"Gave up after {MAX_ATTEMPTS} attempts: "
        f"{last_error}"
    )


# ============================================================================
# DATE
# ============================================================================

def parse_arrival_date(raw: str) -> str:

    dt = datetime.strptime(
        raw.strip(),
        "%d/%m/%Y",
    )

    return dt.date().isoformat()


# ============================================================================
# ROW ID
# ============================================================================

def make_row_id(
    product_id: str,
    market: str,
    date_iso: str,
) -> str:

    value = (
        f"{product_id}-"
        f"{market}-"
        f"{date_iso}"
    )

    digest = hashlib.sha1(
        value.encode()
    ).hexdigest()[:10]

    return (
        f"{product_id}-{digest}"
    )


# ============================================================================
# EXISTING HISTORY
# ============================================================================

def load_existing_rows(
    product_id: str,
) -> list:

    path = (
        PRICES_DIR
        / f"{product_id}.json"
    )

    if not path.exists():
        return []

    try:

        with open(
            path,
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        return data.get(
            "history",
            [],
        )

    except Exception:

        return []


# ============================================================================
# FLOAT
# ============================================================================

def to_float(value):

    try:
        return float(value)

    except (
        TypeError,
        ValueError,
    ):

        return None


# ============================================================================
# MAIN
# ============================================================================

def main():

    if not API_KEY:

        print(
            "Missing DATA_GOV_IN_API_KEY "
            "environment variable.",
            file=sys.stderr,
        )

        sys.exit(1)

    print(
        f"Fetching {STATE} mandi prices..."
    )

    raw_records = fetch_kerala_csv()

    print(
        f"Fetched {len(raw_records)} "
        f"raw {STATE} records"
    )

    if not raw_records:

        print(
            "No records fetched - "
            "aborting.",
            file=sys.stderr,
        )

        sys.exit(1)

    PRICES_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cutoff = (
        datetime.now(
            timezone.utc
        ).date()
        - timedelta(
            days=RETENTION_DAYS
        )
    ).isoformat()


    # ------------------------------------------------------------------------
    # DISCOVER NEW COMMODITIES
    # ------------------------------------------------------------------------

    newly_discovered = (
        discover_new_commodities(
            raw_records
        )
    )

    if newly_discovered:

        print(
            "\n[info] Auto-registered "
            f"{len(newly_discovered)} "
            "new commodities:"
        )

        for product_id, name in (
            newly_discovered.items()
        ):

            print(
                f"  {product_id}: {name}"
            )


    # ------------------------------------------------------------------------
    # BUILD PRICE ROWS
    # ------------------------------------------------------------------------

    new_rows_by_product = (
        defaultdict(list)
    )

    unmatched_commodities = set()

    for record in raw_records:

        commodity = (
            record.get("Commodity")
            or ""
        ).strip()

        product_id = (
            COMMODITY_TO_PRODUCT.get(
                commodity
            )
        )

        if not product_id:

            unmatched_commodities.add(
                commodity
            )

            continue

        try:

            date_iso = parse_arrival_date(
                record.get(
                    "Arrival_Date",
                    "",
                )
            )

        except ValueError:

            continue

        market = (
            record.get("Market")
            or ""
        ).strip()

        product_unit = get_product_unit(
            product_id
        )

        row = {
            "id": make_row_id(
                product_id,
                market,
                date_iso,
            ),

            "market": market,

            "district": (
                record.get("District")
                or ""
            ).strip(),

            "date": date_iso,

            "min_price": to_float(
                record.get(
                    "Min_x0020_Price"
                )
            ),

            "max_price": to_float(
                record.get(
                    "Max_x0020_Price"
                )
            ),

            "modal_price": to_float(
                record.get(
                    "Modal_x0020_Price"
                )
            ),

            "unit": product_unit,

            "source": (
                "Agmarknet / data.gov.in"
            ),
        }

        new_rows_by_product[
            product_id
        ].append(row)


    if unmatched_commodities:

        print(
            "\n[info] Unmatched commodities:"
        )

        for commodity in sorted(
            unmatched_commodities
        ):

            print(
                f"  - {commodity}"
            )


    # ------------------------------------------------------------------------
    # ENRICH METADATA
    # ------------------------------------------------------------------------

    print(
        "\nRunning metadata enrichment..."
    )

    enrich_new_products(
        PRODUCT_NAMES
    )

    metadata = load_metadata()
    images = load_images()


    # ------------------------------------------------------------------------
    # BUILD MANIFEST
    # ------------------------------------------------------------------------

    manifest_products = []

    skipped_categories = []
    skipped_stale = []

    now_iso = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


    for (
        product_id,
        product_name,
    ) in PRODUCT_NAMES.items():

        entry = metadata.get(
            product_id
        )

        category = (
            entry.get("category")
            if isinstance(
                entry,
                dict,
            )
            else None
        )


        # --------------------------------------------------------------------
        # CATEGORY FILTER
        # --------------------------------------------------------------------
        #
        # IMPORTANT:
        # null is no longer treated as displayable.
        #
        # This means Egg will not appear in your crop categories.
        #

        if category not in DISPLAYABLE_CATEGORIES:

            skipped_categories.append(
                (
                    product_id,
                    category,
                )
            )

            continue


        # --------------------------------------------------------------------
        # LOAD EXISTING HISTORY
        # --------------------------------------------------------------------

        existing = load_existing_rows(
            product_id
        )

        existing_ids = {
            row["id"]
            for row in existing
            if row.get("id")
        }


        # Keep only recent history.
        merged = [
            row
            for row in existing
            if row.get(
                "date",
                ""
            ) >= cutoff
        ]


        # Add today's records.
        for row in (
            new_rows_by_product.get(
                product_id,
                [],
            )
        ):

            if row["id"] not in existing_ids:

                merged.append(row)

                existing_ids.add(
                    row["id"]
                )


        merged.sort(
            key=lambda row: row.get(
                "date",
                ""
            ),
            reverse=True,
        )


        # --------------------------------------------------------------------
        # SAVE PRODUCT HISTORY
        # --------------------------------------------------------------------

        product_file = (
            PRICES_DIR
            / f"{product_id}.json"
        )

        with open(
            product_file,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                {
                    "product": product_id,
                    "product_name": product_name,
                    "generated_at": now_iso,
                    "state": STATE,
                    "unit": get_product_unit(
                        product_id
                    ),
                    "history": merged,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )


        # --------------------------------------------------------------------
        # STALE PRODUCT
        # --------------------------------------------------------------------

        if not merged:

            skipped_stale.append(
                product_id
            )

            continue


        # --------------------------------------------------------------------
        # PRICE CALCULATION
        # --------------------------------------------------------------------

        dates_available = sorted(
            {
                row["date"]
                for row in merged
                if row.get("date")
            },
            reverse=True,
        )


        today_date = None
        yesterday_date = None

        today_avg = None
        yesterday_avg = None

        market_count = 0


        # Today's price
        if dates_available:

            today_date = (
                dates_available[0]
            )

            today_rows = [
                row
                for row in merged
                if (
                    row.get("date")
                    == today_date
                    and row.get(
                        "modal_price"
                    )
                    is not None
                )
            ]

            if today_rows:

                today_avg = round(
                    sum(
                        row["modal_price"]
                        for row in today_rows
                    )
                    / len(today_rows),
                    2,
                )

                market_count = (
                    len(today_rows)
                )


        # Yesterday's price
        if len(dates_available) > 1:

            yesterday_date = (
                dates_available[1]
            )

            yesterday_rows = [
                row
                for row in merged
                if (
                    row.get("date")
                    == yesterday_date
                    and row.get(
                        "modal_price"
                    )
                    is not None
                )
            ]

            if yesterday_rows:

                yesterday_avg = round(
                    sum(
                        row["modal_price"]
                        for row in yesterday_rows
                    )
                    / len(yesterday_rows),
                    2,
                )


        # --------------------------------------------------------------------
        # CHANGE
        # --------------------------------------------------------------------

        change = None
        pct_change = None

        if (
            today_avg is not None
            and yesterday_avg is not None
            and yesterday_avg != 0
        ):

            change = round(
                today_avg
                - yesterday_avg,
                2,
            )

            pct_change = round(
                (
                    change
                    / yesterday_avg
                )
                * 100,
                1,
            )


        # --------------------------------------------------------------------
        # MANIFEST ENTRY
        # --------------------------------------------------------------------

        manifest_products.append(
            {
                "id": product_id,

                "name": product_name,

                "category": category,

                "image_url": (
                    images.get(product_id)
                    or (
                        entry.get(
                            "image_url"
                        )
                        if isinstance(
                            entry,
                            dict,
                        )
                        else None
                    )
                ),

                "unit": get_product_unit(
                    product_id
                ),

                "today_date": today_date,

                "today_avg_price": today_avg,

                "yesterday_date": (
                    yesterday_date
                ),

                "yesterday_avg_price": (
                    yesterday_avg
                ),

                "change": change,

                "pct_change": pct_change,

                "market_count": market_count,

                "history_days": len(
                    dates_available
                ),
            }
        )


    # ------------------------------------------------------------------------
    # WRITE MANIFEST
    # ------------------------------------------------------------------------

    with open(
        MANIFEST_PATH,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            {
                "generated_at": now_iso,

                "source": (
                    "Agmarknet / data.gov.in, "
                    "Government of India"
                ),

                "state": STATE,

                "products": manifest_products,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )


    # ------------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------------

    covered = sum(
        1
        for product in manifest_products
        if product["today_avg_price"]
        is not None
    )

    with_image = sum(
        1
        for product in manifest_products
        if product["image_url"]
    )


    print("\n========================================")
    print("ENRICHMENT COMPLETE")
    print("========================================")

    print(
        f"Displayable products : "
        f"{len(manifest_products)}"
    )

    print(
        f"With today's price   : "
        f"{covered}"
    )

    print(
        f"With image           : "
        f"{with_image}"
    )

    if skipped_categories:

        print(
            "\nExcluded categories:"
        )

        for product_id, category in (
            skipped_categories
        ):

            print(
                f"  {product_id}: "
                f"{category}"
            )

    if skipped_stale:

        print(
            "\nStale products:"
        )

        for product_id in skipped_stale:

            print(
                f"  {product_id}"
            )


if __name__ == "__main__":
    main()
