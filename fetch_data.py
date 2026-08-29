"""
Phase 1 discovery script - run this FIRST, before building the real collector.

Purpose: the data.gov.in mandi dataset's exact commodity/market naming isn't
documented anywhere reliable, so instead of guessing (which risks silently
missing products), this pulls today's Kerala records and prints what's
actually there - distinct commodities, distinct markets, and a sample row.
"""

import os
import sys
import time
import requests

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

# Government API is often slow to respond, especially from GitHub-hosted
# runners (US/EU datacenters) hitting a server in India. Give it real room,
# and request smaller pages so no single request has to do too much work.
REQUEST_TIMEOUT_SEC = 90
PAGE_SIZE = 200
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5


def fetch_page(offset: int) -> list:
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format": "json",
                    "offset": offset,
                    "limit": PAGE_SIZE,
                    "filters[state]": "Keralam",
                },
                timeout=REQUEST_TIMEOUT_SEC,
            )
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json().get("records", [])

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Timeout/connection error on offset={offset}, "
                  f"attempt {attempt}/{MAX_ATTEMPTS}; retrying in {backoff}s")
            time.sleep(backoff)
        except RuntimeError as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Error on offset={offset} ({e}); retrying in {backoff}s")
            time.sleep(backoff)

    raise RuntimeError(f"Gave up on offset={offset} after {MAX_ATTEMPTS} attempts: {last_error}")


def fetch_all_kerala_records() -> list:
    all_records = []
    offset = 0

    while True:
        records = fetch_page(offset)
        all_records.extend(records)
        print(f"  fetched offset={offset}, got {len(records)} records "
              f"(total so far: {len(all_records)})")

        if len(records) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_records


def main():
    if not API_KEY:
        print("Missing DATA_GOV_IN_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    print("Fetching all current Kerala records from Agmarknet dataset...")
    try:
        records = fetch_all_kerala_records()
    except RuntimeError as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)

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
