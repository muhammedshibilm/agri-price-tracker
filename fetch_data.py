"""
fetch_data.py
--------------
Daily pipeline for the Agri Price Tracker app.

What it does, in order:
1. Fetches today's price for each tracked commodity (PLUG IN YOUR REAL SOURCE
   in `fetch_price_from_source()` below — see the notes there).
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
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

# Add/remove commodities here. `unit_label` is just for display in the app.
COMMODITIES = {
    "Coconut": {"unit_label": "per_kg_rs"},
    "Paddy": {"unit_label": "per_kg_rs"},
    "Egg_NECC": {"unit_label": "per_piece_rs"},
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "data", "history")
DATA_JSON_PATH = os.path.join(BASE_DIR, "data", "data.json")

# Reject a new price if it jumps more than this % vs. yesterday — almost
# always means a scrape/parsing error, not a real market move.
MAX_ALLOWED_DAILY_CHANGE_PCT = 50.0

os.makedirs(HISTORY_DIR, exist_ok=True)


# --------------------------------------------------------------------------
# STEP 1: FETCH TODAY'S PRICE
# --------------------------------------------------------------------------

def fetch_price_from_source(commodity_name: str):
    """
    *** REPLACE THIS FUNCTION WITH YOUR REAL DATA SOURCE ***

    This is the one function you need to customize. Options, roughly in
    order of how much I'd trust them:

    1. Official open-data API (best) — e.g. data.gov.in / Agmarknet API if
       you can get access, or a state agri-marketing board API.
    2. A licensed/paid market-data provider.
    3. Scraping a public site — only if permitted by that site's Terms of
       Service. Respect robots.txt, don't hammer the server, and cache
       aggressively (you only need ONE fetch per commodity per day).

    Return a single float (price), or None if the fetch failed — never
    return 0 or a guess, since that will silently corrupt your history.
    """
    raise NotImplementedError(
        f"Wire up a real price source for '{commodity_name}' here. "
        "Returning None for now so the pipeline fails loudly instead of "
        "writing fake data."
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
