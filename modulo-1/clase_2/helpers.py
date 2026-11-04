"""Shared helpers for the Clase 2 notebooks (2a, 2b, 2c, 2d).

Every network call of the class goes through ONE small function (`request_text`), which is the only
place with a try/except: it retries a few times, backs off on HTTP 429, prints one clear line and then
raises (or returns None when the caller says the data is optional).
"""
import json
import time
from io import StringIO

import numpy as np
import pandas as pd
import requests

RETRIES = 5
# No custom User-Agent: FRED stalls requests that send one (they time out), the default works everywhere


def request_text(method, url, params=None, payload=None, required=True, timeout=60):
    """Send one HTTP request with retries. Returns the response body as text.

    - HTTP 429 (rate limit): wait 5, 10, 20 ... seconds and try again.
    - Other 4xx: the request itself is wrong, retrying cannot help -> stop.
    - Network errors and 5xx: short sleep, try again.
    After the last attempt: print ONE [ERROR] line, then raise (required=True) or return None.
    """
    last_error = ''
    for attempt in range(RETRIES):
        wait = 1.0 * (attempt + 1)
        # The one try/except of the course code: the network can fail in many ways, we only care that it failed
        try:
            response = requests.request(method, url, params=params, json=payload, timeout=timeout)
            if response.status_code == 429:
                wait = 5 * 2 ** attempt
                last_error = '429 Too Many Requests'
                print(f"[WARNING] Rate limited (429) by {url.split('/')[2]}. Waiting {wait}s...")
            elif 400 <= response.status_code < 500:
                last_error = f"HTTP {response.status_code}"
                break
            else:
                response.raise_for_status()
                return response.text
        except requests.RequestException as error:
            last_error = str(error)
        if attempt < RETRIES - 1:
            time.sleep(wait)
    message = f"{method} {url} failed: {last_error}"
    print(f"[ERROR] {message}")
    if required:
        raise RuntimeError(message)
    return None


def _parse_json(text):
    if text is None:
        return None
    return json.loads(text)


def get_json(url, params=None, required=True, timeout=60):
    """GET a JSON endpoint (with retries). Raises on failure unless required=False (then returns None)."""
    return _parse_json(request_text('GET', url, params=params, required=required, timeout=timeout))


def post_json(url, payload, required=True, timeout=30):
    """POST a JSON body (with retries). Raises on failure unless required=False (then returns None)."""
    return _parse_json(request_text('POST', url, payload=payload, required=required, timeout=timeout))


# ---------------------------------------------------------------------------------------------
# DefiLlama prices
# ---------------------------------------------------------------------------------------------
def fetch_price_history(coin_key, days=90):
    """Daily USD price history from the DefiLlama coins API. coin_key looks like 'coingecko:ethereum'.

    The API rejects requests above 500 points, so long histories are fetched in 365-day windows
    going backwards with the `end` parameter. Returns a DataFrame with columns timestamp, price, date
    (one row per UTC day). Raises if the API returns no prices.
    """
    window = 365
    n_windows = int(np.ceil(days / window))
    now = int(time.time())
    points = []
    for k in range(n_windows):
        end = now - k * window * 86400
        span = min(window, days - k * window)
        data = get_json(f"https://coins.llama.fi/chart/{coin_key}", {'span': span, 'period': '1d', 'end': end}, timeout=30)
        points.extend(data.get('coins', {}).get(coin_key, {}).get('prices', []))
        if k < n_windows - 1:
            time.sleep(0.3)          # be polite between pages (not after the last one)
    if not points:
        raise RuntimeError(f"DefiLlama returned no prices for {coin_key}")

    df = pd.DataFrame(points).rename(columns={'timestamp': 'ts'})
    df['timestamp'] = pd.to_datetime(df['ts'], unit='s')
    df['date'] = df['timestamp'].dt.normalize()
    df = df.sort_values('timestamp').drop_duplicates('date', keep='last')
    return df[['timestamp', 'price', 'date']].reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------------------------
def fetch_fred(series_id='DTB3', fallback=0.04):
    """Latest valid observation of a FRED series (percent per year) as (rate_as_decimal, observation_date).

    If FRED cannot be reached the fallback rate is returned and this function says so loudly.
    """
    text = request_text('GET', 'https://fred.stlouisfed.org/graph/fredgraph.csv', params={'id': series_id},
                        required=False, timeout=20)
    if text is None:
        print(f"[WARNING] Using FALLBACK risk-free rate {fallback:.2%} (hardcoded, NOT live FRED data)")
        return fallback, 'fallback constant'
    df = pd.read_csv(StringIO(text))
    df.columns = ['date', 'value']
    df['value'] = pd.to_numeric(df['value'], errors='coerce')   # FRED uses '.' for missing days
    df = df.dropna()
    return float(df['value'].iloc[-1]) / 100, str(df['date'].iloc[-1])


# ---------------------------------------------------------------------------------------------
# Formatters (named functions instead of lambdas)
# ---------------------------------------------------------------------------------------------
def fmt_musd(value):
    """12_345_678 -> '$12.3M'"""
    return f"${value / 1e6:,.1f}M"


def fmt_2dp(value):
    """1234.5 -> '1,234.50'"""
    return f"{value:,.2f}"


def fmt_1dp(value):
    """12.34 -> '12.3'"""
    return f"{value:.1f}"
