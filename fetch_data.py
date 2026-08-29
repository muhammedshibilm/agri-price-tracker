"""
Phase 1 discovery script - CSV version.

We confirmed via direct browser download that format=csv works reliably for
this resource, while format=json was returning intermittent 502s. This
switches the fetch to CSV and parses it with the standard library, no new
dependencies needed.
"""

import os
import sys
import csv
import io
import time
import requests

API_KEY = os.environ.get("DATA_GOV_IN_API_KEY")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

REQUEST_TIMEOUT_SEC = 90
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 5


def fetch_kerala_csv() -> list:
    """Fetches all current Kerala records in one CSV request (no pagination
    needed - limit=all works reliably in CSV format for this resource)."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format": "csv",
                    "limit": "all",
                    "filters[state]": "Keralam",
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
                print(f"  [diagnostic] status={resp.status_code} "
                      f"body_len={len(resp.text)}")
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            reader = csv.DictReader(io.StringIO(resp.text))
            records = list(reader)
            return records

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Timeout/connection error, attempt {attempt}/{MAX_ATTEMPTS}; "
                  f"retrying in {backoff}s")
            time.sleep(backoff)
        except RuntimeError as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                break
            backoff = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  Error ({e}); retrying in {backoff}s")
            time.sleep(backoff)

    raise RuntimeError(f"Gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def main():
    if not API_KEY:
        print("Missing DATA_GOV_IN_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    print("Fetching all current Kerala records (CSV format) from Agmarknet dataset...")
    try:
        records = fetch_kerala_csv()
    except RuntimeError as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)

    if not records:
        print("No records returned. The dataset may not have published today's "
              "batch yet, or the filter didn't match anything.")
        return

    print(f"\nTotal Kerala records: {len(records)}\n")

    commodities = sorted({r.get("Commodity", "") for r in records})
    markets = sorted({r.get("Market", "") for r in records})
    districts = sorted({r.get("District", "") for r in records})

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
