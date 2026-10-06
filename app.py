from flask import Flask, jsonify, render_template
import os
import time
import requests
import pandas as pd

app = Flask(__name__)

API_URL = "https://api.india.delta.exchange/v2/history/candles"
PRODUCTS_URL = "https://api.india.delta.exchange/v2/products"

SYMBOLS = {
    "BTC": "BTCUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD"
}

RR = 2.0


# =========================================================
# GET CANDLES
# =========================================================

def candles(symbol, resolution, minutes):

    now = int(time.time())

    r = requests.get(
        API_URL,
        params={
            "resolution": resolution,
            "symbol": symbol,
            "start": now - minutes * 60,
            "end": now
        },
        timeout=10
    )

    r.raise_for_status()

    df = pd.DataFrame(
        r.json().get("result", [])
    )

    if df.empty:
        return df

    df["time"] = pd.to_datetime(
        df["time"],
        unit="s",
        utc=True
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    return (
        df
        .sort_values("time")
        .drop_duplicates("time")
        .set_index("time")
    )


# =========================================================
# ONLY CLOSED CANDLES
# =========================================================

def closed(df, minutes):

    if df.empty:
        return df

    now = pd.Timestamp.now(tz="UTC")

    return df[
        df.index + pd.Timedelta(minutes=minutes)
        <= now
    ]


# =========================================================
# EMA
# =========================================================

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


# =========================================================
# CANDLE BODY
#
# WICK IS NOT COUNTED
# =========================================================

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


# =========================================================
# BODY TOUCH EMA
# =========================================================

def body_touches_ema(candle, ema):

    body_low, body_high = body_range(candle)

    return (
        body_low <= float(ema)
        <= body_high
    )


# =========================================================
# 4H CONDITION
#
# BODY TOUCH EMA 7
# =========================================================

def check_4h(symbol):

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

    return body_touches_ema(
        candle,
        candle["ema7"]
    )


# =========================================================
# 1H CONDITION
#
# BODY TOUCH EMA 7 + EMA 17
# =========================================================

def check_1h(symbol):

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


# =========================================================
# 15M CONDITION
#
# BODY TOUCH:
# EMA 7 + EMA 17 + EMA 50
#
# ALL THREE COMPULSORY
# =========================================================

def check_15m(symbol):

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


# =========================================================
# 5M SETUP
#
# LONG:
# Previous candle body below EMA cluster
# Current green candle body crosses EMA 7/17/50/200
#
# SHORT:
# Previous candle body above EMA cluster
# Current red candle body crosses EMA 7/17/50/200
#
# SL = PREVIOUS TWO CANDLES
# TARGET = 1:2
# =========================================================

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

    # =====================================================
    # LONG
    # =====================================================

    green = (
        current["close"]
        >
        current["open"]
    )

    previous_body_below = (
        previous_body_high
        <
        previous_low_ema
    )

    current_body_crossed_up = (
        current_body_low
        <=
        current_high_ema
        and
        current_body_high
        >
        current_high_ema
        and
        current["close"]
        >
        current_high_ema
    )

    long_setup = (
        green
        and
        previous_body_below
        and
        current_body_crossed_up
    )

    # =====================================================
    # SHORT
    # =====================================================

    red = (
        current["close"]
        <
        current["open"]
    )

    previous_body_above = (
        previous_body_low
        >
        previous_high_ema
    )

    current_body_crossed_down = (
        current_body_high
        >=
        current_low_ema
        and
        current_body_low
        <
        current_low_ema
        and
        current["close"]
        <
        current_low_ema
    )

    short_setup = (
        red
        and
        previous_body_above
        and
        current_body_crossed_down
    )

    # =====================================================
    # LONG SIGNAL
    # =====================================================

    if long_setup:

        entry = float(
            current["close"]
        )

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
                +
                RR * risk
            )

            return {
                "signal": "LONG",
                "entry": entry,
                "sl": sl,
                "target": target,
                "rr": "1:2",
                "time": current.name.isoformat(),
                "reason":
                    "5M bullish body cross of EMA 7/17/50/200"
            }

    # =====================================================
    # SHORT SIGNAL
    # =====================================================

    if short_setup:

        entry = float(
            current["close"]
        )

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
                -
                RR * risk
            )

            return {
                "signal": "SHORT",
                "entry": entry,
                "sl": sl,
                "target": target,
                "rr": "1:2",
                "time": current.name.isoformat(),
                "reason":
                    "5M bearish body cross of EMA 7/17/50/200"
            }

    return None


# =========================================================
# GOLD AUTO DISCOVERY
# =========================================================

def find_gold_symbol():

    try:

        r = requests.get(
            PRODUCTS_URL,
            params={
                "states": "live",
                "page_size": 100
            },
            timeout=10
        )

        r.raise_for_status()

        products = r.json().get(
            "result",
            []
        )

        candidates = []

        for p in products:

            text = " ".join(
                str(
                    p.get(k, "")
                )
                for k in [
                    "symbol",
                    "description",
                    "underlying_asset",
                    "contract_type",
                    "product_type"
                ]
            ).upper()

            if (
                "XAU" in text
                or
                "GOLD" in text
            ):
                candidates.append(p)

        # Prefer perpetual XAU
        for p in candidates:

            symbol = str(
                p.get("symbol", "")
            )

            contract_type = str(
                p.get(
                    "contract_type",
                    p.get(
                        "product_type",
                        ""
                    )
                )
            ).lower()

            if (
                "XAU" in symbol.upper()
                and
                "perpetual" in contract_type
            ):
                return symbol

        # Any XAU symbol
        for p in candidates:

            symbol = str(
                p.get("symbol", "")
            )

            if "XAU" in symbol.upper():
                return symbol

    except Exception:
        pass

    return None


# =========================================================
# SILVER AUTO DISCOVERY
# =========================================================

def find_silver_symbol():

    try:

        r = requests.get(
            PRODUCTS_URL,
            params={
                "states": "live",
                "page_size": 100
            },
            timeout=10
        )

        r.raise_for_status()

        products = r.json().get(
            "result",
            []
        )

        candidates = []

        for p in products:

            text = " ".join(
                str(
                    p.get(k, "")
                )
                for k in [
                    "symbol",
                    "description",
                    "underlying_asset",
                    "contract_type",
                    "product_type"
                ]
            ).upper()

            if (
                "XAG" in text
                or
                "SILVER" in text
            ):
                candidates.append(p)

        # Prefer perpetual XAG
        for p in candidates:

            symbol = str(
                p.get("symbol", "")
            )

            contract_type = str(
                p.get(
                    "contract_type",
                    p.get(
                        "product_type",
                        ""
                    )
                )
            ).lower()

            if (
                "XAG" in symbol.upper()
                and
                "perpetual" in contract_type
            ):
                return symbol

        # Any XAG symbol
        for p in candidates:

            symbol = str(
                p.get("symbol", "")
            )

            if "XAG" in symbol.upper():
                return symbol

    except Exception:
        pass

    return None


# =========================================================
# FINAL SIGNAL
# =========================================================

def signal(symbol):

    # Higher timeframe conditions
    h4_ok = check_4h(symbol)
    h1_ok = check_1h(symbol)
    m15_ok = check_15m(symbol)

    # 5M
    df = candles(
        symbol,
        "5m",
        5 * 300
    )

    checks = {
        "4H": "PASS" if h4_ok else "FAIL",
        "1H": "PASS" if h1_ok else "FAIL",
        "15M": "PASS" if m15_ok else "FAIL",
        "5M": "WAIT"
    }

    if df.empty:

        checks["5M"] = "FAIL"

        return {
            "signal": "WAIT",
            "reason": "No 5M data",
            "checks": checks
        }

    df = closed(df, 5)

    if len(df) < 210:

        checks["5M"] = "FAIL"

        return {
            "signal": "WAIT",
            "reason": "Not enough 5M data",
            "checks": checks
        }

    df = add_emas(df)

    setup = five_minute_setup(df)

    if setup:

        checks["5M"] = setup["signal"]

    else:

        checks["5M"] = "FAIL"

    # =====================================================
    # COMPLETE SETUP
    # =====================================================

    if (
        h4_ok
        and
        h1_ok
        and
        m15_ok
        and
        setup
    ):

        setup["checks"] = checks

        return setup

    # =====================================================
    # WAIT REASON
    # =====================================================

    failed = []

    if not h4_ok:
        failed.append("4H")

    if not h1_ok:
        failed.append("1H")

    if not m15_ok:
        failed.append("15M")

    if not setup:
        failed.append("5M")

    return {
        "signal": "WAIT",
        "reason":
            "Waiting: "
            +
            " + ".join(failed),
        "checks": checks,
        "time":
            df.iloc[-1].name.isoformat()
    }


# =========================================================
# HOME PAGE
# =========================================================

@app.get("/")
def home():

    return render_template(
        "index.html"
    )


# =========================================================
# SCAN API
# =========================================================

@app.get("/api/scan")
def scan():

    # Main assets
    assets = dict(SYMBOLS)

    # Gold
    gold = find_gold_symbol()

    if gold:
        assets["GOLD"] = gold

    # Silver
    silver = find_silver_symbol()

    if silver:
        assets["SILVER"] = silver

    output = {}

    # =====================================================
    # SCAN EVERY ASSET
    # =====================================================

    for name, symbol in assets.items():

        try:

            output[name] = signal(symbol)

            output[name]["symbol"] = symbol

        except Exception as e:

            output[name] = {
                "signal": "ERROR",
                "reason": str(e),
                "symbol": symbol
            }

    # =====================================================
    # GOLD UNAVAILABLE
    # =====================================================

    if not gold:

        output["GOLD"] = {
            "signal": "UNAVAILABLE",
            "reason":
                "No live Gold/XAU product found",
            "symbol":
                "Auto-discovery"
        }

    # =====================================================
    # SILVER UNAVAILABLE
    # =====================================================

    if not silver:

        output["SILVER"] = {
            "signal": "UNAVAILABLE",
            "reason":
                "No live Silver/XAG product found",
            "symbol":
                "Auto-discovery"
        }

    return jsonify({
        "updated": int(time.time()),
        "assets": output
    })


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
