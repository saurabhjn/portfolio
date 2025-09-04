import os
import json
import datetime
from decimal import Decimal
from typing import Optional

import requests


def load_api_key(config_path: str) -> Optional[str]:
    """Loads the Alpha Vantage API key from a JSON config file."""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            key = config.get("ALPHA_VANTAGE_API_KEY")
            if not key:
                print(f"Warning: 'ALPHA_VANTAGE_API_KEY' not found in {config_path}")
            return key
    except FileNotFoundError:
        print(f"Warning: Config file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        print(f"Warning: Could not decode JSON from {config_path}")
        return None


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
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
RATE_CACHE_FILE = os.path.join(DATA_DIR, "rate_cache.json")

ALPHA_VANTAGE_API_KEY = load_api_key(CONFIG_FILE)

# In-memory cache for stock rates: {ticker: (timestamp, rate)}
rate_cache = load_rate_cache(RATE_CACHE_FILE)


def get_current_rate(ticker: str) -> Optional[Decimal]:
    """
    Fetches the latest price for a given ticker from various sources,
    with a 30-minute cache.
    """
    if not ticker:
        return None

    now = datetime.datetime.now()

    # Check for a fresh cache hit first
    if ticker in rate_cache:
        cached_time, cached_rate = rate_cache[ticker]
        if now - cached_time < datetime.timedelta(minutes=30):
            print(f"Cache hit for {ticker}. Returning cached rate.")
            return cached_rate

    print(f"Cache miss or stale for {ticker}. Fetching from source.")
    rate = None

    if ticker.startswith("IN"):  # For Indian Mutual Funds (ISINs)
        try:
            url = f"https://mf.captnemo.in/nav/{ticker}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            nav_str = data.get("nav")
            if nav_str is not None:
                rate = Decimal(str(nav_str))
                print(f"captnemo.in success for {ticker}: rate {rate}")
        except Exception as e:
            print(f"captnemo.in failed for {ticker}: {e}")
            rate = None

    else:  # Fallback to Alpha Vantage for other tickers
        if not ALPHA_VANTAGE_API_KEY:
            print(f"API key is not configured. Skipping API call for {ticker}.")
            return None

        params = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": ticker,
            "interval": "5min",
            "apikey": ALPHA_VANTAGE_API_KEY,
        }
        try:
            response = requests.get(
                "https://www.alphavantage.co/query", params=params, timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if "Error Message" in data or "Note" in data:
                print(
                    f"API Error for {ticker}: {data.get('Error Message') or data.get('Note')}"
                )
                return None

            meta_data = data.get("Meta Data")
            time_series = data.get("Time Series (5min)")

            if meta_data and time_series:
                last_refreshed_key = meta_data.get("3. Last Refreshed")
                latest_data = time_series.get(last_refreshed_key)
                close_price_str = latest_data.get("4. close") if latest_data else None

                if close_price_str:
                    rate = Decimal(close_price_str)

        except requests.exceptions.RequestException as e:
            print(f"Request failed for {ticker}: {e}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Failed to parse data for {ticker}: {e}")

    # If we got a new rate, update cache and return it
    if rate is not None:
        rate_cache[ticker] = (now, rate)
        save_rate_cache(RATE_CACHE_FILE, rate_cache)
        return rate

    # If API call failed, fall back to any existing cached value (even stale)
    if ticker in rate_cache:
        print(f"API call failed for {ticker}. Returning stale cached rate.")
        return rate_cache[ticker][1]

    # If API failed and no cache ever existed, return None
    return None
