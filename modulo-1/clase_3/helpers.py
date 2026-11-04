"""Shared helpers for the clase_3 notebooks: ONE network function (the only try/except) plus two tiny formatters."""
import time

import requests


def fetch(method, url, params=None, body=None, headers=None, tries=4, as_text=False):
    """Send one HTTP request and return the parsed JSON (or the raw text if as_text=True).
    Retries with a growing pause on network errors, HTTP 429 (rate limit) and 5xx.
    Any other HTTP error is final. On failure prints one [ERROR] line and returns None (never fake data)."""
    wait = 5
    for attempt in range(1, tries + 1):
        problem = ''
        try:
            r = requests.request(method, url, params=params, json=body, headers=headers, timeout=30)
            if r.status_code == 200:
                if as_text:
                    return r.text
                return r.json()
            problem = f"HTTP {r.status_code}"
            if r.status_code != 429 and r.status_code < 500:
                print(f"[ERROR] {url} -> {problem}")      # a 4xx other than 429: retrying will not help
                return None
        except (requests.RequestException, ValueError) as exc:    # network failure or a body that is not JSON
            problem = type(exc).__name__
        print(f"[WARNING] {url} -> {problem} (attempt {attempt}/{tries})")
        if attempt < tries:
            time.sleep(wait)
            wait = wait * 2
    print(f"[ERROR] giving up on {url}")
    return None


def get_json(url, params=None, headers=None):
    """GET and parse JSON. Returns None if it still fails after the retries."""
    return fetch('GET', url, params=params, headers=headers)


def get_text(url, params=None, headers=None):
    """GET and return the body as text (for CSV endpoints). Returns None on failure."""
    return fetch('GET', url, params=params, headers=headers, as_text=True)


def post_json(url, body):
    """POST a JSON body and parse the JSON reply. Returns None on failure."""
    return fetch('POST', url, body=body)


def dollar_axis(x, pos):
    """Matplotlib tick formatter: 12345 -> '$12,345'."""
    return f"${x:,.0f}"


def fmt_musd(v):
    """12_345_678 -> '$12.3M'."""
    return f"${v / 1e6:,.1f}M"
