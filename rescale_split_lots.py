#!/usr/bin/env python3
"""Rescale one investment's lots to post-split basis.

Usage:
    python rescale_split_lots.py "<investment name>" <factor> <split date YYYY-MM-DD>
    python rescale_split_lots.py "<investment name>" <factor> <split date> --dry-run

Every lot with a quantity gets quantity * factor and rate / factor; every sale
with a quantity gets the same. Gain entries and quantity-less entries are
untouched. A quantity-less marker entry is appended to the lots. Price history
from Yahoo is split-adjusted, and the graph values lots correctly only in
post-split basis.

The password is read from data/.password if that file exists, otherwise
prompted for. A timestamped backup of data/transactions.enc is written first.
"""
import getpass
import json
import os
import shutil
import sys
from datetime import date, datetime
from decimal import Decimal

from encryption import decrypt_data, encrypt_data

DATA_DIR = "data"
ENCRYPTED_TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.enc")
PASSWORD_FILE = os.path.join(DATA_DIR, ".password")


def _scale(value: str, factor: Decimal, multiply: bool) -> str:
    d = Decimal(value)
    return str(d * factor if multiply else d / factor)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    if len(args) != 3:
        print(__doc__)
        return 2
    name, factor, split_date = args[0], Decimal(args[1]), date.fromisoformat(args[2])

    if os.path.exists(PASSWORD_FILE):
        password = open(PASSWORD_FILE).read().strip()
    else:
        password = getpass.getpass("Data password: ")

    with open(ENCRYPTED_TRANSACTIONS_FILE) as f:
        data = decrypt_data(json.load(f), password)

    if name not in data:
        print(f"no investment named {name!r}; known names:")
        for n in data:
            print("  ", n)
        return 1

    lots = data[name]
    changed = 0
    for lot in lots:
        if lot.get("buy_quantity") and lot.get("buy_rate"):
            before = (lot["buy_quantity"], lot["buy_rate"])
            lot["buy_quantity"] = _scale(lot["buy_quantity"], factor, True)
            lot["buy_rate"] = _scale(lot["buy_rate"], factor, False)
            print(f"lot {lot.get('buy_date')}: qty {before[0]} -> {lot['buy_quantity']}, rate {before[1]} -> {lot['buy_rate']}")
            changed += 1
        for sale in lot.get("sales", []):
            if sale.get("sell_quantity") and sale.get("sell_rate"):
                before = (sale["sell_quantity"], sale["sell_rate"])
                sale["sell_quantity"] = _scale(sale["sell_quantity"], factor, True)
                sale["sell_rate"] = _scale(sale["sell_rate"], factor, False)
                print(f"  sale {sale.get('sell_date')}: qty {before[0]} -> {sale['sell_quantity']}, rate {before[1]} -> {sale['sell_rate']}")
                changed += 1

    lots.append({
        "buy_date": split_date.isoformat(),
        "buy_quantity": None,
        "buy_rate": None,
        "description": f"{factor.normalize()}:1 stock split {split_date.strftime('%m/%d/%Y')} (applied by rescaling lots to post-split basis)",
        "sales": [],
        "gain_date": None,
        "gain_amount": None,
    })
    print(f"{changed} quantities/rates rescaled for {name!r}")

    if dry_run:
        print("dry run: nothing written")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{ENCRYPTED_TRANSACTIONS_FILE}.bak.split.{stamp}"
    shutil.copy2(ENCRYPTED_TRANSACTIONS_FILE, backup)
    with open(ENCRYPTED_TRANSACTIONS_FILE, "w") as f:
        json.dump(encrypt_data(data, password), f)
    print(f"backup: {backup}")
    print(f"written: {ENCRYPTED_TRANSACTIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
