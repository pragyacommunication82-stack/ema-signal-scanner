from flask import Flask, jsonify, render_template
import os, time, requests, pandas as pd

app = Flask(__name__)
API_URL = 'https://api.india.delta.exchange/v2/history/candles'
PRODUCTS_URL = 'https://api.india.delta.exchange/v2/products'
SYMBOLS = {'BTC': 'BTCUSD', 'ETH': 'ETHUSD', 'SOL': 'SOLUSD'}
RR = 2.0


def candles(symbol, resolution, minutes):
    now = int(time.time())
    r = requests.get(API_URL, params={'resolution': resolution, 'symbol': symbol,
                                      'start': now - minutes * 60, 'end': now}, timeout=10)
    r.raise_for_status()
    df = pd.DataFrame(r.json().get('result', []))
    if df.empty:
        return df
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.sort_values('time').drop_duplicates('time').set_index('time')


def closed(df, mins):
    if df.empty:
        return df
    now = pd.Timestamp.now(tz='UTC')
    return df[df.index + pd.Timedelta(minutes=mins) <= now]


def emas(df):
    df = df.copy()
    for n in (7, 17, 50, 200):
        df[f'ema{n}'] = df.close.ewm(span=n, adjust=False).mean()
    return df


def find_gold_symbol():
    # Discover a live Delta India product whose underlying/base asset is XAU.
    try:
        r = requests.get(PRODUCTS_URL, params={'states': 'live', 'page_size': 100}, timeout=10)
        r.raise_for_status()
        products = r.json().get('result', [])
        candidates = []
        for p in products:
            symbol = str(p.get('symbol', ''))
            text = ' '.join(str(p.get(k, '')) for k in
                            ('symbol', 'description', 'underlying_asset', 'contract_type', 'product_type')).upper()
            if 'XAU' in text or 'GOLD' in text:
                candidates.append(p)
        # Prefer perpetual futures containing XAU.
        for p in candidates:
            symbol = str(p.get('symbol', ''))
            ctype = str(p.get('contract_type', p.get('product_type', ''))).lower()
            if 'XAU' in symbol.upper() and 'perpetual' in ctype:
                return symbol
        for p in candidates:
            symbol = str(p.get('symbol', ''))
            if 'XAU' in symbol.upper():
                return symbol
    except Exception:
        pass
    return None


def trend(symbol):
    df = closed(candles(symbol, '15m', 15 * 300), 15)
    if len(df) < 210:
        return 'NO TRADE'
    df = emas(df)
    c, p = df.iloc[-1], df.iloc[-2]
    if c.ema50 > c.ema200 and c.ema50 > p.ema50:
        return 'BULLISH'
    if c.ema50 < c.ema200 and c.ema50 < p.ema50:
        return 'BEARISH'
    return 'NO TRADE'


def signal(symbol):
    tr = trend(symbol)
    df = closed(candles(symbol, '5m', 5 * 300), 5)
    if len(df) < 210:
        return {'trend': tr, 'signal': 'WAIT', 'reason': 'Not enough data'}
    df = emas(df)
    c, p1, p2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    hi = max(c.ema7, c.ema17, c.ema50, c.ema200)
    lo = min(c.ema7, c.ema17, c.ema50, c.ema200)
    mid = (c.open + c.close) / 2
    bull, bear = c.close > c.open, c.close < c.open
    long1 = tr == 'BULLISH' and bull and p1.close < min(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and c.close > hi and mid > hi
    long2 = tr == 'BULLISH' and bull and p1.close > max(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and p1.low <= min(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and c.close > hi and mid > hi
    short1 = tr == 'BEARISH' and bear and p1.close > max(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and c.close < lo and mid < lo
    short2 = tr == 'BEARISH' and bear and p1.close < min(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and p1.high >= max(p1.ema7, p1.ema17, p1.ema50, p1.ema200) and c.close < lo and mid < lo
    if long1 or long2:
        entry = float(c.close); sl = float(min(p1.low, p2.low)); risk = entry - sl
        if risk > 0:
            return {'trend': tr, 'signal': 'LONG', 'entry': entry, 'sl': sl, 'target': entry + RR * risk, 'time': c.name.isoformat()}
    if short1 or short2:
        entry = float(c.close); sl = float(max(p1.high, p2.high)); risk = sl - entry
        if risk > 0:
            return {'trend': tr, 'signal': 'SHORT', 'entry': entry, 'sl': sl, 'target': entry - RR * risk, 'time': c.name.isoformat()}
    return {'trend': tr, 'signal': 'WAIT', 'reason': 'No confirmed 5M EMA setup', 'time': c.name.isoformat()}


@app.get('/')
def home():
    return render_template('index.html')


@app.get('/api/scan')
def scan():
    assets = dict(SYMBOLS)
    gold = find_gold_symbol()
    if gold:
        assets['GOLD'] = gold
    out = {}
    for name, sym in assets.items():
        try:
            out[name] = signal(sym)
            out[name]['symbol'] = sym
        except Exception as e:
            out[name] = {'trend': 'ERROR', 'signal': 'ERROR', 'reason': str(e), 'symbol': sym}
    if not gold:
        out['GOLD'] = {'trend': 'N/A', 'signal': 'UNAVAILABLE', 'reason': 'No live XAU/Gold product found in Delta product list', 'symbol': 'Auto-discovery'}
    return jsonify({'updated': int(time.time()), 'assets': out})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    app.run(host='0.0.0.0', port=port)
