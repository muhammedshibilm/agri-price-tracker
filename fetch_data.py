"""
fetch_data.py
--------------
Daily pipeline for the Agri Price Tracker app.

What it does, in order:
1. Fetches today's price for each tracked commodity (see
   `fetch_price_from_source()` below).
2. Validates the new price against yesterday's (rejects garbage/outlier data).
3. Appends it to a per-commodity CSV history file under data/history/.
4. Computes a next-day prediction using a per-commodity backtested EWMA.
5. Tracks how accurate yesterday's prediction actually was.
6. Writes everything to data/data.json for the Flutter app to consume.

Run manually with:  python fetch_data.py
Run daily via the GitHub Action in .github/workflows/daily-update.yml
"""

import csv
import json
import os
import time
from datetime import datetime, timezone

import requests

# api.data.gov.in is known to be slow/flaky from datacenter IPs (which is
# exactly what a GitHub Actions runner is) — sometimes it just stalls
# instead of erroring. A generic User-Agent seems to make this worse, so
# we set a normal-looking one, use a generous timeout, and retry a couple
# of times before giving up.
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; agri-price-tracker/1.0; "
                  "+https://github.com/muhammedshibilm/agri-price-tracker)"
})
AGMARKNET_TIMEOUT_SECONDS = 60
AGMARKNET_MAX_RETRIES = 3
AGMARKNET_RETRY_BACKOFF_SECONDS = 5

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

# Add/remove commodities here. `unit_label` is just for display in the app.
#
# `source` tells fetch_price_from_source() how to fetch this commodity:
#   - "agmarknet": pulled from data.gov.in's Agmarknet variety-wise daily
#     market prices resource. Needs agmarknet_commodity/state_candidates/
#     district/market.
#   - anything else: not wired up yet, will raise NotImplementedError.
COMMODITIES = {
    "Coconut": {
        "unit_label": "per_kg_rs",
        "source": "agmarknet",
        "agmarknet_commodity": "Coconut",
        # Agmarknet's exact spelling for Kerala isn't 100% confirmed from
        # here (my test queries all hit a cached response and couldn't
        # tell "Kerala" vs "Keralam" apart). Try both, in order, and log
        # whichever one actually returns records. Run the snippet in the
        # chat writeup once to confirm, then trim this list to just the
        # correct spelling.
        "state_candidates": ["Kerala", "Keralam"],
        # Leave district/market as None to average across all matching
        # markets reporting that day, or set them to pin to one mandi.
        "district": None,
        "market": None,
    },
    "Paddy": {
        "unit_label": "per_kg_rs",
        "source": "agmarknet",
        # NOTE: verify this against the actual value Agmarknet uses —
        # it's sometimes listed as "Paddy(Dhan)(Common)" rather than "Paddy".
        "agmarknet_commodity": "Paddy",
        "state_candidates": ["Kerala", "Keralam"],
        "district": None,
        "market": None,
    },
    "Egg_NECC": {
        "unit_label": "per_piece_rs",
        # Not wired up: NECC egg rates aren't in the Agmarknet dataset.
        # You'll need a different source (e.g. necc.in) for this one.
        "source": None,
    },
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "data", "history")
DATA_JSON_PATH = os.path.join(BASE_DIR, "data", "data.json")

# Reject a new price if it jumps more than this % vs. yesterday — almost
# always means a scrape/parsing error, not a real market move.
MAX_ALLOWED_DAILY_CHANGE_PCT = 50.0

# data.gov.in resource ID for "Variety-wise Daily Market Prices" (Agmarknet).
# Double-check this on data.gov.in against the dataset you actually
# registered for — resource IDs can differ by dataset version.
AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"

os.makedirs(HISTORY_DIR, exist_ok=True)


# --------------------------------------------------------------------------
# STEP 1: FETCH TODAY'S PRICE
# --------------------------------------------------------------------------

def _query_agmarknet(api_key: str, commodity: str, state: str, district=None, market=None):
    """
    One call to the Agmarknet resource, with retries — this API frequently
    just stalls (TCP connects fine, no response) rather than erroring
    cleanly, especially from cloud/CI IP ranges. Returns the records list
    (may be empty). Re-raises the last error if every attempt fails.
    """
    url = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": 50,
        "filters[commodity]": commodity,
        "filters[state]": state,
    }
    if district:
        params["filters[district]"] = district
    if market:
        params["filters[market]"] = market

    last_error = None
    for attempt in range(1, AGMARKNET_MAX_RETRIES + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=AGMARKNET_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json().get("records", [])
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"    attempt {attempt}/{AGMARKNET_MAX_RETRIES} for "
                  f"'{commodity}'/'{state}' failed ({e!r}); "
                  f"{'retrying' if attempt < AGMARKNET_MAX_RETRIES else 'giving up'}")
            if attempt < AGMARKNET_MAX_RETRIES:
                time.sleep(AGMARKNET_RETRY_BACKOFF_SECONDS * attempt)

    raise last_error


def _fetch_from_agmarknet(meta: dict) -> float:
    """
    Queries data.gov.in's Agmarknet resource for a commodity's modal price
    and returns the average modal price across whatever markets reported
    today (or the most recent day the dataset has, if today isn't posted
    yet — Agmarknet is often ~1 day behind).

    Tries each spelling in `state_candidates` in order and uses the first
    one that actually returns records, so a state-name mismatch doesn't
    silently produce zero data forever.
    """
    api_key = os.environ.get("DATA_GOV_IN_API_KEY")
    if not api_key:
        raise RuntimeError("DATA_GOV_IN_API_KEY is not set in the environment")

    commodity = meta["agmarknet_commodity"]
    district = meta.get("district")
    market = meta.get("market")

    records = []
    matched_state = None
    for state in meta["state_candidates"]:
        records = _query_agmarknet(api_key, commodity, state, district, market)
        if records:
            matched_state = state
            break

    if not records:
        raise RuntimeError(
            f"Agmarknet returned no records for '{commodity}' under any of "
            f"{meta['state_candidates']}. Either none of those markets "
            f"reported this commodity today, or the state/commodity string "
            f"doesn't match what Agmarknet uses — check with a filterless "
            f"query."
        )

    if matched_state != meta["state_candidates"][0]:
        print(f"    NOTE: '{meta['state_candidates'][0]}' returned nothing; "
              f"'{matched_state}' is the spelling that actually works. "
              f"Consider trimming state_candidates to just that value.")

    # Records carry a "modal_price" (Rs per quintal) per market. Average
    # across markets reporting today, then convert quintal -> kg.
    modal_prices = []
    for r in records:
        try:
            modal_prices.append(float(r["modal_price"]))
        except (KeyError, TypeError, ValueError):
            continue

    if not modal_prices:
        raise RuntimeError("Agmarknet records had no usable modal_price field")

    avg_price_per_quintal = sum(modal_prices) / len(modal_prices)
    return round(avg_price_per_quintal / 100.0, 2)  # Rs/quintal -> Rs/kg


def fetch_price_from_source(commodity_name: str):
    """
    Returns a single float (price), or raises if the fetch failed — never
    returns 0 or a guess, since that would silently corrupt history.
    """
    meta = COMMODITIES[commodity_name]
    source = meta.get("source")

    if source == "agmarknet":
        return _fetch_from_agmarknet(meta)

    raise NotImplementedError(
        f"No data source configured for '{commodity_name}'. "
        "Set 'source' in COMMODITIES and add a fetch branch for it."
    )


# --------------------------------------------------------------------------
# STEP 2: LOAD / VALIDATE / SAVE HISTORY
# --------------------------------------------------------------------------

def history_path(commodity_name: str) -> str:
    return os.path.join(HISTORY_DIR, f"{commodity_name}.csv")


def load_history(commodity_name: str):
    """Returns list of (date_str, price) tuples, oldest first."""
    path = history_path(commodity_name)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    return [(r[0], float(r[1])) for r in rows if len(r) == 2]


def validate_price(commodity_name: str, new_price: float, history) -> tuple[bool, str]:
    """Returns (is_valid, reason_if_invalid)."""
    if new_price is None:
        return False, "fetch returned no data"
    if new_price <= 0:
        return False, f"non-positive price ({new_price})"
    if history:
        last_price = history[-1][1]
        if last_price > 0:
            pct_change = abs(new_price - last_price) / last_price * 100
            if pct_change > MAX_ALLOWED_DAILY_CHANGE_PCT:
                return False, (
                    f"price jumped {pct_change:.1f}% vs yesterday "
                    f"({last_price} -> {new_price}), likely bad data"
                )
    return True, ""


def append_history(commodity_name: str, date_str: str, price: float):
    with open(history_path(commodity_name), "a", newline="") as f:
        csv.writer(f).writerow([date_str, price])


# --------------------------------------------------------------------------
# STEP 3: PREDICTION (per-commodity backtested EWMA)
# --------------------------------------------------------------------------

def ewma_predict(prices: list[float], alpha: float) -> float:
    """Exponentially weighted moving average -> next value estimate."""
    ewma = prices[0]
    for p in prices[1:]:
        ewma = (alpha * p) + ((1 - alpha) * ewma)
    return ewma


def backtest_alpha_error(prices: list[float], alpha: float) -> float:
    """
    Walk-forward test: for each day t, predict day t+1 using only data up
    to day t, and measure the average absolute error against what actually
    happened. Lower is better.
    """
    if len(prices) < 4:
        return float("inf")
    errors = []
    for t in range(3, len(prices) - 1):
        window = prices[: t + 1]
        pred = ewma_predict(window, alpha)
        actual_next = prices[t + 1]
        errors.append(abs(pred - actual_next))
    return sum(errors) / len(errors) if errors else float("inf")


def choose_best_alpha(prices: list[float], candidates=(0.2, 0.3, 0.4, 0.5, 0.6, 0.7)):
    best_alpha, best_error = 0.3, float("inf")
    for a in candidates:
        err = backtest_alpha_error(prices, a)
        if err < best_error:
            best_alpha, best_error = a, err
    return best_alpha, best_error


def std_dev(prices: list[float]) -> float:
    if len(prices) < 2:
        return 0.0
    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    mean_diff = sum(diffs) / len(diffs)
    variance = sum((d - mean_diff) ** 2 for d in diffs) / len(diffs)
    return variance ** 0.5


def predict_next_day_price(history: list[tuple[str, float]]):
    """
    Takes full (date, price) history for one commodity and returns a
    prediction dict, including a confidence range and the model's own
    backtested error so the app can show real accuracy, not just a number.
    """
    prices = [p for _, p in history]

    if len(prices) < 4:
        return {
            "today_price": prices[-1] if prices else 0.0,
            "predicted_tomorrow_price": prices[-1] if prices else 0.0,
            "predicted_range": {"low": None, "high": None},
            "trend": "STABLE",
            "predicted_change_rs": 0.0,
            "confidence": "LOW (insufficient history, need 4+ days)",
            "model_alpha": None,
            "backtested_mean_abs_error_rs": None,
        }

    alpha, backtest_mae = choose_best_alpha(prices)
    predicted = round(ewma_predict(prices, alpha), 2)
    last_price = prices[-1]
    diff = round(predicted - last_price, 2)
    volatility = std_dev(prices)

    if diff > 0.5:
        trend = "UPWARD"
    elif diff < -0.5:
        trend = "DOWNWARD"
    else:
        trend = "STABLE"

    if backtest_mae == float("inf"):
        confidence = "LOW (insufficient history for backtest)"
    elif backtest_mae < volatility * 0.5:
        confidence = "HIGH"
    elif backtest_mae < volatility:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "today_price": last_price,
        "predicted_tomorrow_price": predicted,
        "predicted_range": {
            "low": round(predicted - volatility, 2),
            "high": round(predicted + volatility, 2),
        },
        "trend": trend,
        "predicted_change_rs": diff,
        "confidence": confidence,
        "model_alpha": alpha,
        "backtested_mean_abs_error_rs": round(backtest_mae, 2) if backtest_mae != float("inf") else None,
    }


# --------------------------------------------------------------------------
# STEP 4: ACCURACY TRACKING (was yesterday's prediction right?)
# --------------------------------------------------------------------------

def evaluate_yesterday_prediction(old_data_json: dict, commodity_name: str, todays_actual_price: float):
    """
    Looks at the prediction data.json had for this commodity *before* this
    run, compares it to today's actual price, and returns a performance
    record. Returns None if there was no prior prediction to check.
    """
    try:
        prev = old_data_json["market_commodities"][commodity_name]["prediction"]
        predicted_price = prev["predicted_tomorrow_price"]
    except (KeyError, TypeError):
        return None

    error = abs(predicted_price - todays_actual_price)
    correct_direction = None
    if "predicted_change_rs" in prev:
        predicted_up = prev["predicted_change_rs"] > 0
        predicted_down = prev["predicted_change_rs"] < 0
        actual_change = todays_actual_price - prev.get("today_price", todays_actual_price)
        if predicted_up:
            correct_direction = actual_change > 0
        elif predicted_down:
            correct_direction = actual_change < 0
        else:
            correct_direction = abs(actual_change) <= 0.5

    return {
        "predicted_price": predicted_price,
        "actual_price": todays_actual_price,
        "absolute_error_rs": round(error, 2),
        "direction_correct": correct_direction,
    }


def update_rolling_accuracy(old_perf: dict, new_eval: dict, window: int = 7):
    """Keeps a rolling list of the last `window` evaluations for the accuracy %."""
    history = old_perf.get("recent_evaluations", []) if old_perf else []
    history.append(new_eval)
    history = history[-window:]

    correct = [e["direction_correct"] for e in history if e["direction_correct"] is not None]
    accuracy_pct = round(100 * sum(correct) / len(correct), 1) if correct else None
    mae_values = [e["absolute_error_rs"] for e in history]
    mean_abs_error = round(sum(mae_values) / len(mae_values), 2) if mae_values else None

    return {
        "last_n_day_direction_accuracy_pct": accuracy_pct,
        "last_n_day_mean_absolute_error_rs": mean_abs_error,
        "predictions_evaluated": len(history),
        "recent_evaluations": history,
    }


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def load_previous_data_json():
    if os.path.exists(DATA_JSON_PATH):
        with open(DATA_JSON_PATH) as f:
            return json.load(f)
    return {}


def main():
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old_data = load_previous_data_json()

    output = {
        "updated_at": today_str,
        "market_commodities": {},
    }

    for name, meta in COMMODITIES.items():
        print(f"--- {name} ---")
        history = load_history(name)

        try:
            new_price = fetch_price_from_source(name)
        except NotImplementedError as e:
            print(f"  SKIPPED: {e}")
            continue
        except Exception as e:
            print(f"  ERROR fetching price: {e}")
            continue

        is_valid, reason = validate_price(name, new_price, history)
        if not is_valid:
            print(f"  REJECTED new price {new_price}: {reason}")
            print("  Keeping yesterday's data.json entry for this commodity unchanged.")
            if name in old_data.get("market_commodities", {}):
                output["market_commodities"][name] = old_data["market_commodities"][name]
                output["market_commodities"][name]["data_freshness"] = "stale (fetch failed validation today)"
            continue

        # Accuracy check on YESTERDAY's prediction, before we touch history
        eval_result = evaluate_yesterday_prediction(old_data, name, new_price)
        old_perf = old_data.get("market_commodities", {}).get(name, {}).get("model_performance")
        performance = update_rolling_accuracy(old_perf, eval_result) if eval_result else (old_perf or {})

        # Now append today's price and predict tomorrow's
        append_history(name, today_str, new_price)
        history.append((today_str, new_price))
        prediction = predict_next_day_price(history)

        output["market_commodities"][name] = {
            f"current_price_{meta['unit_label']}": new_price,
            "prediction": prediction,
            "model_performance": performance,
            "data_freshness": "current",
        }
        print(f"  price={new_price}  predicted_tomorrow={prediction['predicted_tomorrow_price']}  "
              f"trend={prediction['trend']}  confidence={prediction['confidence']}")

    with open(DATA_JSON_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {DATA_JSON_PATH}")


if __name__ == "__main__":
    main()
