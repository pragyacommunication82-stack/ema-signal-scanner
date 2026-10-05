# EMA Signal Scanner

Mobile/laptop web scanner for Delta Exchange India public market data.

Rules: 5M EMA 7/17/50/200, 15M 50/200 trend + slope filter, confirmed candle/body rule, SL from previous two candles, RR 1:2, signal-only.

Gold is auto-discovered from Delta's live product list when a live XAU/Gold product is available.

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
