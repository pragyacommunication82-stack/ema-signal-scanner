from flask import Flask, jsonify, render_template
import time
import requests
import pandas as pd

app = Flask(__name__)

API_URL = "https://api.india.delta.exchange/v2/history/candles"
PRODUCTS_URL = "https://api.india.delta.exchange/v2/products"

SYMBOLS = {
    "BTC": "BTCUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
}

RR = 2.0


# ---------------------------------------------------------
# COMMON HELPERS
# ---------------------------------------------------------

def candles(symbol, resolution, minutes):
    now = int(time.time())

    r = requests.get(
        API_URL,
        params={
            "resolution": resolution,
            "symbol": symbol,
            "start": now - minutes * 60,
            "end": now,
        },
        timeout=10,
    )

    r.raise_for_status()

    data = r.json().get("result", [])
    df = pd.DataFrame(data)

    if df.empty:
        return df

    df["time"] = pd.to_datetime(
        df["time"],
        unit="s",
        utc=True
    )

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    df = (
        df.sort_values("time")
        .drop_duplicates("time")
        .set_index("time")
    )

    return df


def closed(df, minutes):
    if df.empty:
        return df

    now = pd.Timestamp.now(tz="UTC")

    return df[
        df.index + pd.Timedelta(minutes=minutes) <= now
    ]


def add_emas(df):
    df = df.copy()

    for period in [7, 17, 50, 200]:
        df[f"ema{period}"] = (
            df["close"]
            .ewm(
                span=period,
                adjust=False
            )
            .mean()
        )

    return df


def body_range(candle):
    body_low = min(
        float(candle["open"]),
        float(candle["close"])
    )

    body_high = max(
        float(candle["open"]),
        float(candle["close"])
    )

    return body_low, body_high


def body_touches_ema(candle, ema):
    body_low, body_high = body_range(candle)

    return (
        body_low
        <= float(ema)
        <= body_high
    )


# ---------------------------------------------------------
# GOLD / SILVER AUTO DISCOVERY
# ---------------------------------------------------------

def get_products():
    try:
        r = requests.get(
            PRODUCTS_URL,
            timeout=10
        )

        r.raise_for_status()

        result = r.json().get("result", [])

        return result

    except Exception:
        return []


def find_metal_symbol(metal):
    products = get_products()

    candidates = []

    for product in products:

        symbol = str(
            product.get("symbol", "")
        ).upper()

        description = str(
            product.get("description", "")
        ).upper()

        contract_type = str(
            product.get("contract_type", "")
        ).upper()

        text = (
            symbol
            + " "
            + description
        )

        if metal == "GOLD":
            match = (
                "XAU" in text
                or "GOLD" in text
            )
        else:
            match = (
                "XAG" in text
                or "SILVER" in text
            )

        if not match:
            continue

        # Prefer perpetual products.
        score = 0

        if "PERPETUAL" in contract_type:
            score += 100

        if "PERP" in symbol:
            score += 50

        if "USD" in symbol:
            score += 10

        candidates.append(
            (score, symbol)
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0][1]


def build_symbols():
    symbols = dict(SYMBOLS)

    gold = find_metal_symbol("GOLD")
    silver = find_metal_symbol("SILVER")

    if gold:
        symbols["GOLD"] = gold

    if silver:
        symbols["SILVER"] = silver

    return symbols


# ---------------------------------------------------------
# HIGHER TIMEFRAME CHECKS
# ---------------------------------------------------------

def check_4h(symbol):
    try:
        df = candles(
            symbol,
            "4h",
            4 * 300
        )

        if df.empty:
            return False

        df = closed(df, 240)

        if len(df) < 210:
            return False

        df = add_emas(df)

        candle = df.iloc[-1]

        # BODY must touch EMA 7.
        # Wick touch DOES NOT count.
        return body_touches_ema(
            candle,
            candle["ema7"]
        )

    except Exception:
        return False


def check_1h(symbol):
    try:
        df = candles(
            symbol,
            "1h",
            60 * 300
        )

        if df.empty:
            return False

        df = closed(df, 60)

        if len(df) < 210:
            return False

        df = add_emas(df)

        candle = df.iloc[-1]

        # BOTH EMA 7 and EMA 17 compulsory.
        return (
            body_touches_ema(
                candle,
                candle["ema7"]
            )
            and
            body_touches_ema(
                candle,
                candle["ema17"]
            )
        )

    except Exception:
        return False


def check_15m(symbol):
    try:
        df = candles(
            symbol,
            "15m",
            15 * 300
        )

        if df.empty:
            return False

        df = closed(df, 15)

        if len(df) < 210:
            return False

        df = add_emas(df)

        candle = df.iloc[-1]

        # ALL THREE compulsory:
        # EMA 7 + EMA 17 + EMA 50
        return (
            body_touches_ema(
                candle,
                candle["ema7"]
            )
            and
            body_touches_ema(
                candle,
                candle["ema17"]
            )
            and
            body_touches_ema(
                candle,
                candle["ema50"]
            )
        )

    except Exception:
        return False


# ---------------------------------------------------------
# 5 MINUTE ENTRY SETUP
# ---------------------------------------------------------

def five_minute_setup(df):

    if len(df) < 3:
        return None

    df = add_emas(df)

    current = df.iloc[-1]
    previous = df.iloc[-2]
    before_previous = df.iloc[-3]

    # Current EMA cluster
    current_low_ema = min(
        current["ema7"],
        current["ema17"],
        current["ema50"],
        current["ema200"]
    )

    current_high_ema = max(
        current["ema7"],
        current["ema17"],
        current["ema50"],
        current["ema200"]
    )

    # Previous EMA cluster
    previous_low_ema = min(
        previous["ema7"],
        previous["ema17"],
        previous["ema50"],
        previous["ema200"]
    )

    previous_high_ema = max(
        previous["ema7"],
        previous["ema17"],
        previous["ema50"],
        previous["ema200"]
    )

    current_body_low, current_body_high = body_range(
        current
    )

    previous_body_low, previous_body_high = body_range(
        previous
    )

    # -----------------------------------------------------
    # LONG
    # -----------------------------------------------------

    green = (
        current["close"]
        > current["open"]
    )

    previous_body_below = (
        previous_body_high
        < previous_low_ema
    )

    current_body_crossed_up = (
        current_body_low
        <= current_high_ema
        and
        current_body_high
        > current_high_ema
        and
        current["close"]
        > current_high_ema
    )

    long_setup = (
        green
        and previous_body_below
        and current_body_crossed_up
    )

    if long_setup:

        entry = float(
            current["close"]
        )

        # SL = lowest low of previous 2 candles
        sl = float(
            min(
                previous["low"],
                before_previous["low"]
            )
        )

        risk = entry - sl

        if risk > 0:

            target = (
                entry
                + RR * risk
            )

            return {
                "signal": "LONG",
                "entry": entry,
                "sl": sl,
                "target": target,
                "rr": "1:2",
                "time": current.name.isoformat(),
                "reason": (
                    "5M bullish body cross "
                    "of EMA 7/17/50/200"
                ),
            }

    # -----------------------------------------------------
    # SHORT
    # -----------------------------------------------------

    red = (
        current["close"]
        < current["open"]
    )

    previous_body_above = (
        previous_body_low
        > previous_high_ema
    )

    current_body_crossed_down = (
        current_body_high
        >= current_low_ema
        and
        current_body_low
        < current_low_ema
        and
        current["close"]
        < current_low_ema
    )

    short_setup = (
        red
        and previous_body_above
        and current_body_crossed_down
    )

    if short_setup:

        entry = float(
            current["close"]
        )

        # SL = highest high of previous 2 candles
        sl = float(
            max(
                previous["high"],
                before_previous["high"]
            )
        )

        risk = sl - entry

        if risk > 0:

            target = (
                entry
                - RR * risk
            )

            return {
                "signal": "SHORT",
                "entry": entry,
                "sl": sl,
                "target": target,
                "rr": "1:2",
                "time": current.name.isoformat(),
                "reason": (
                    "5M bearish body cross "
                    "of EMA 7/17/50/200"
                ),
            }

    return None


# ---------------------------------------------------------
# COMPLETE SIGNAL
# ---------------------------------------------------------

def signal(asset, symbol):

    checks = {
        "4H": "FAIL",
        "1H": "FAIL",
        "15M": "FAIL",
        "5M": "FAIL",
    }

    current_price = None

    # -----------------------------------------------------
    # 4H
    # -----------------------------------------------------

    h4_ok = check_4h(symbol)

    checks["4H"] = (
        "PASS"
        if h4_ok
        else "FAIL"
    )

    # -----------------------------------------------------
    # 1H
    # -----------------------------------------------------

    h1_ok = check_1h(symbol)

    checks["1H"] = (
        "PASS"
        if h1_ok
        else "FAIL"
    )

    # -----------------------------------------------------
    # 15M
    # -----------------------------------------------------

    m15_ok = check_15m(symbol)

    checks["15M"] = (
        "PASS"
        if m15_ok
        else "FAIL"
    )

    # -----------------------------------------------------
    # 5M
    # -----------------------------------------------------

    try:

        # Keep current/open candle so its close acts
        # as the latest available live price.
        raw_5m = candles(
            symbol,
            "5m",
            5 * 300
        )

        if raw_5m.empty:
            return {
                "asset": asset,
                "symbol": symbol,
                "price": None,
                "signal": "ERROR",
                "checks": checks,
                "reason": "No 5M data available",
            }

        current_price = float(
            raw_5m.iloc[-1]["close"]
        )

        # Signal calculation only on CLOSED candles.
        df = closed(
            raw_5m,
            5
        )

        if len(df) < 3:

            return {
                "asset": asset,
                "symbol": symbol,
                "price": current_price,
                "signal": "WAIT",
                "checks": checks,
                "reason": "Waiting for 5M candle data",
            }

        setup = five_minute_setup(df)

        if setup:

            checks["5M"] = setup["signal"]

        else:

            checks["5M"] = "FAIL"

        # -------------------------------------------------
        # FINAL FILTER
        # -------------------------------------------------

        if (
            h4_ok
            and h1_ok
            and m15_ok
            and setup
        ):

            setup["asset"] = asset
            setup["symbol"] = symbol
            setup["price"] = current_price
            setup["checks"] = checks

            return setup

        # Find exactly what is blocking the trade.
        failed = []

        if not h4_ok:
            failed.append("4H")

        if not h1_ok:
            failed.append("1H")

        if not m15_ok:
            failed.append("15M")

        if not setup:
            failed.append("5M")

        reason = (
            "Waiting: "
            + " + ".join(failed)
        )

        return {
            "asset": asset,
            "symbol": symbol,
            "price": current_price,
            "signal": "WAIT",
            "checks": checks,
            "reason": reason,
            "time": (
                df.iloc[-1].name.isoformat()
            ),
        }

    except Exception as e:

        return {
            "asset": asset,
            "symbol": symbol,
            "price": current_price,
            "signal": "ERROR",
            "checks": checks,
            "reason": str(e),
        }


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template(
        "index.html"
    )


@app.route("/api/scan")
def api_scan():

    symbols = build_symbols()

    results = []

    for asset in [
        "BTC",
        "ETH",
        "SOL",
        "GOLD",
        "SILVER",
    ]:

        symbol = symbols.get(asset)

        if not symbol:

            results.append({
                "asset": asset,
                "symbol": "NOT_FOUND",
                "price": None,
                "signal": "UNAVAILABLE",
                "checks": {
                    "4H": "—",
                    "1H": "—",
                    "15M": "—",
                    "5M": "—",
                },
                "reason": (
                    f"{asset} product not found "
                    "on Delta"
                ),
            })

            continue

        results.append(
            signal(
                asset,
                symbol
            )
        )

    response = jsonify({
        "status": "ok",
        "updated": int(time.time()),
        "assets": results,
    })

    response.headers[
        "Cache-Control"
    ] = "no-store, no-cache, must-revalidate"

    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
