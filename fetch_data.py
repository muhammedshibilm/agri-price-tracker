

import os
import sys
import requests

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"


def fetch_all_kerala_records() -> list:
    """Paginates through the API collecting every Kerala ('Keralam') record
    available right now. The dataset paginates in pages of ~10-100 depending
    on the key tier, so we loop with offset until a page comes back short."""
    all_records = []
    offset = 0
    page_size = 1000

    while True:
        resp = requests.get(
            BASE_URL,
            params={
                "api-key": API_KEY,
                "format": "json",
                "offset": offset,
                "limit": page_size,
                "filters[state]": "Keralam",
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"Request failed: {resp.status_code} {resp.text}", file=sys.stderr)
            break

        data = resp.json()
        records = data.get("records", [])
        all_records.extend(records)
        print(f"  fetched offset={offset}, got {len(records)} records "
              f"(total so far: {len(all_records)})")

        if len(records) < page_size:
            break
        offset += page_size

    return all_records


def main():
    if not API_KEY:
        print("Missing DATA_GOV_IN_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    print("Fetching all current Kerala records from Agmarknet dataset...")
    records = fetch_all_kerala_records()

    if not records:
        print("No records returned for state=Keralam. Check the filter value "
              "or try again later - the dataset updates daily and today's "
              "batch may not be published yet.")
        return

    print(f"\nTotal Kerala records: {len(records)}\n")

    commodities = sorted({r.get("commodity", "") for r in records})
    markets = sorted({r.get("market", "") for r in records})
    districts = sorted({r.get("district", "") for r in records})

    print(f"Distinct commodities found ({len(commodities)}):")
    for c in commodities:
        print(f"  - {c}")

    print(f"\nDistinct districts found ({len(districts)}):")
    for d in districts:
        print(f"  - {d}")

    print(f"\nDistinct markets found ({len(markets)}):")
    for m in markets:
        print(f"  - {m}")

    print("\nSample record:")
    print(records[0])


if __name__ == "__main__":
    main()
