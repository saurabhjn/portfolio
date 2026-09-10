#!/usr/bin/env python
"""Rewrite cached historical stock prices with real (dividend-unadjusted) closes.

Entries were cached from yfinance with its default auto_adjust=True, which
lowers every past price by the dividends paid after it. This script downloads
each ticker's daily closes once (auto_adjust=False, still split-adjusted) and
replaces every HIST_<ticker>_<date> entry with the last close on or before
that date. Mutual fund scheme codes (6 digits) and FX entries are untouched.

A timestamped backup of data/rate_cache.json is written first.
"""
import datetime
import shutil
import sys
from decimal import Decimal

import yfinance as yf

from api_calls import RATE_CACHE_FILE, load_rate_cache, save_rate_cache


def main() -> int:
    cache = load_rate_cache(RATE_CACHE_FILE)
    tickers = {}
    for key in cache:
        if not key.startswith("HIST_"):
            continue
        ticker, date_str = key[5:].rsplit("_", 1)
        if ticker.isdigit() and len(ticker) == 6:
            continue
        tickers.setdefault(ticker, []).append(datetime.date.fromisoformat(date_str))

    if not tickers:
        print("no stock entries in cache")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{RATE_CACHE_FILE}.bak.unadjusted_refresh.{stamp}"
    shutil.copy2(RATE_CACHE_FILE, backup)
    print(f"backup written: {backup}")

    now = datetime.datetime.now()
    changed = 0
    for ticker, dates in tickers.items():
        start = min(dates) - datetime.timedelta(days=10)
        end = max(dates) + datetime.timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
        if hist.empty:
            print(f"{ticker}: no data returned, entries left as they were")
            continue
        closes = [(d.date(), Decimal(str(c))) for d, c in hist["Close"].items()]
        closes.sort()
        for date in dates:
            last = None
            for d, c in closes:
                if d <= date:
                    last = c
                else:
                    break
            if last is None:
                continue
            key = f"HIST_{ticker}_{date.isoformat()}"
            if cache[key][1] != last:
                changed += 1
            cache[key] = (now, last)
        print(f"{ticker}: {len(dates)} dates refreshed")

    save_rate_cache(RATE_CACHE_FILE, cache)
    print(f"done: {changed} entries changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
