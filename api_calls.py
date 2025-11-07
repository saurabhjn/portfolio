import os
import json
import datetime
from decimal import Decimal
from typing import Optional

import requests
import yfinance as yf


def save_rate_cache(cache_path: str, cache: dict):
    """Saves the rate cache to a JSON file."""
    serializable_cache = {}
    for ticker, (timestamp, rate) in cache.items():
        serializable_cache[ticker] = {
            "timestamp": timestamp.isoformat(),
            "rate": str(rate),
        }
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(serializable_cache, f, indent=4)


def load_rate_cache(cache_path: str) -> dict:
    """Loads the rate cache from a JSON file."""
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        reconstructed_cache = {}
        for ticker, value in data.items():
            timestamp = datetime.datetime.fromisoformat(value["timestamp"])
            rate = Decimal(value["rate"])
            reconstructed_cache[ticker] = (timestamp, rate)
        return reconstructed_cache
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return {}


DATA_DIR = "data"
RATE_CACHE_FILE = os.path.join(DATA_DIR, "rate_cache.json")

# In-memory cache for stock rates: {ticker: (timestamp, rate)}
rate_cache = load_rate_cache(RATE_CACHE_FILE)


def get_current_rate(ticker: str, force_refresh: bool = False) -> Optional[Decimal]:
    """Fetches the latest price for a given ticker from various sources."""
    if not ticker:
        return None

    if ticker.startswith("IN"):  # For Indian Mutual Funds (ISINs)
        try:
            url = f"https://mf.captnemo.in/nav/{ticker}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            nav_str = data.get("nav")
            if nav_str is not None:
                return Decimal(str(nav_str))
        except Exception as e:
            print(f"captnemo.in failed for {ticker}: {e}")
    else:  # Use yfinance for US stocks
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                return Decimal(str(hist['Close'].iloc[-1]))
        except Exception as e:
            print(f"yfinance failed for {ticker}: {e}")
    
    return None


def get_historical_usd_to_inr_rate(date: datetime.date) -> Optional[Decimal]:
    """
    Fetches the USD to INR conversion rate for a specific date.
    Results are cached permanently.
    """
    date_str = date.isoformat()
    ticker = f"USD_INR_RATE_{date_str}"

    # For historical rates, if it's in the cache, it's permanent.
    if ticker in rate_cache:
        # The rate for a past date never changes, so we can ignore the timestamp.
        print(f"Permanent cache hit for {ticker}. Returning cached rate.")
        return rate_cache[ticker][1]

    print(f"Cache miss for {ticker}. Fetching from source.")
    rate = None
    try:
        # Using frankfurter.app for free historical rates
        url = f"https://api.frankfurter.app/{date_str}?from=USD&to=INR"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        inr_rate = data.get("rates", {}).get("INR")
        if inr_rate:
            rate = Decimal(str(inr_rate))
            print(f"frankfurter.app success for {ticker}: rate {rate}")
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"frankfurter.app failed for {ticker}: {e}")
        rate = None

    # If we got a new rate, update cache and return it
    if rate is not None:
        # Store with current timestamp, but we'll ignore it on subsequent reads
        rate_cache[ticker] = (datetime.datetime.now(), rate)
        save_rate_cache(RATE_CACHE_FILE, rate_cache)
        return rate

    # If API failed and no cache ever existed, return None
    return None


def get_usd_to_inr_rate() -> Optional[Decimal]:
    """Fetches the latest USD to INR conversion rate."""
    try:
        response = requests.get(
            "https://api.exchangerate-api.com/v4/latest/USD", timeout=5
        )
        response.raise_for_status()
        data = response.json()
        inr_rate = data.get("rates", {}).get("INR")
        if inr_rate:
            return Decimal(str(inr_rate))
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"exchangerate-api failed for USD->INR: {e}")
    
    return None
