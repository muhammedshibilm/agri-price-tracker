import csv
import json
import os
from datetime import datetime, timezone

import requests


STATE_NAME = "Keralam"

DATA_GOV_IN_API_KEY = os.environ.get("DATA_GOV_IN_API_KEY", "")
AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_BASE_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "data", "history")
DATA_JSON_PATH = os.path.join(BASE_DIR, "data", "data.json")


MAX_ALLOWED_DAILY_CHANGE_PCT = 50.0


def slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in name)
    while "__" in keep:
        keep = keep.replace("__", "_")
    return keep.strip("_")

os.makedirs(HISTORY_DIR, exist_ok=True)


def fetch_all_kerala_prices():
    """
    Queries Agmarknet for every record reported today for Kerala, then
    groups by commodity and averages the modal price across all markets
    that reported it.

    Returns: dict {commodity_display_name: price_rs_per_kg}
    Raises RuntimeError if the API key is missing or the request fails.
    """
    if not DATA_GOV_IN_API_KEY:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not set. Get a free key at "
            "https://data.gov.in (My Account -> API Key) and set it "
            "as an environment variable / GitHub secret."
        )

    params = {
        "api-key": DATA_GOV_IN_API_KEY,
        "format": "json",
        "limit": 1000,  # generous — Kerala reports far fewer records/day than this
        "filters[state]": STATE_NAME,
    }
    resp = requests.get(AGMARKNET_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    records = resp.json().get("records", [])
    if not records:
        return {}

    # Keep only today's records (the feed can lag a day for some markets;
    # we take the most recent date actually present instead of assuming
    # "today" so the pipeline doesn't silently skip everything).
    def parse_date(r):
        d, m, y = r["arrival_date"].split("/")
        return (y, m, d)

    latest_date = max(parse_date(r) for r in records)
    todays_records = [r for r in records if parse_date(r) == latest_date]

    prices_by_commodity = {}
    for r in todays_records:
        commodity = r["commodity"]
        try:
            modal_price_per_quintal = float(r["modal_price"])
        except (KeyError, ValueError, TypeError):
            continue
        if modal_price_per_quintal <= 0:
            continue
        prices_by_commodity.setdefault(commodity, []).append(modal_price_per_quintal)

    return {
        commodity: round(sum(vals) / len(vals) / 100.0, 2)  # Rs/quintal -> Rs/kg
        for commodity, vals in prices_by_commodity.items()
    }


# --------------------------------------------------------------------------
# STEP 2: LOAD / VALIDATE / SAVE HISTORY
# --------------------------------------------------------------------------

def history_path(commodity_slug: str) -> str:
    return os.path.join(HISTORY_DIR, f"{commodity_slug}.csv")

def load_history(commodity_slug: str):
    """Returns list of (date_str, price) tuples, oldest first."""
    path = history_path(commodity_slug)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    return [(r[0], float(r[1])) for r in rows if len(r) == 2]

def validate_price(new_price: float, history) -> tuple[bool, str]:
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

def append_history(commodity_slug: str, date_str: str, price: float):
    with open(history_path(commodity_slug), "a", newline="") as f:
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

def evaluate_yesterday_prediction(old_data_json: dict, commodity_slug: str, todays_actual_price: float):
    """
    Looks at the prediction data.json had for this commodity *before* this
    run, compares it to today's actual price, and returns a performance
    record. Returns None if there was no prior prediction to check.
    """
    try:
        prev = old_data_json["market_commodities"][commodity_slug]["prediction"]
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

    try:
        todays_prices = fetch_all_kerala_prices()
    except Exception as e:
        print(f"ERROR fetching prices from Agmarknet: {e}")
        print("Keeping yesterday's data.json unchanged.")
        return

    if not todays_prices:
        print("No records returned for Kerala today — nothing to update.")
        return

    for commodity_name, new_price in sorted(todays_prices.items()):
        slug = slugify(commodity_name)
        print(f"--- {commodity_name} ---")

        history = load_history(slug)
        is_valid, reason = validate_price(new_price, history)

        if not is_valid:
            print(f" REJECTED new price {new_price}: {reason}")
            print(" Keeping yesterday's data.json entry for this commodity unchanged.")
            if slug in old_data.get("market_commodities", {}):
                output["market_commodities"][slug] = old_data["market_commodities"][slug]
                output["market_commodities"][slug]["data_freshness"] = "stale (fetch failed validation today)"
            continue

        # Accuracy check on YESTERDAY's prediction, before we touch history
        eval_result = evaluate_yesterday_prediction(old_data, slug, new_price)
        old_perf = old_data.get("market_commodities", {}).get(slug, {}).get("model_performance")
        performance = update_rolling_accuracy(old_perf, eval_result) if eval_result else (old_perf or {})

        # Now append today's price and predict tomorrow's
        append_history(slug, today_str, new_price)
        history.append((today_str, new_price))
        prediction = predict_next_day_price(history)

        output["market_commodities"][slug] = {
            "display_name": commodity_name,
            "current_price_per_kg_rs": new_price,
            "prediction": prediction,
            "model_performance": performance,
            "data_freshness": "current",
        }

        print(f" price={new_price} predicted_tomorrow={prediction['predicted_tomorrow_price']} "
              f"trend={prediction['trend']} confidence={prediction['confidence']}")

    with open(DATA_JSON_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {DATA_JSON_PATH} with {len(output['market_commodities'])} commodities.")

if __name__ == "__main__":
    main()
