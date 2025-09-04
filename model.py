import json
import os
from dataclasses import dataclass, asdict, field
from enum import Enum
from decimal import Decimal
from typing import List, Dict, Optional
import datetime


class Currency(Enum):
    """Enumeration for supported currencies."""

    USD = "USD"
    INR = "INR"


@dataclass
class Investment:
    """A data class representing a financial investment."""

    investment_name: str
    ticker: str
    five_year_annualised_return: Decimal
    ten_year_annualised_return: Decimal
    currency: Currency


@dataclass
class Transaction:
    """A data class representing a financial transaction for an investment."""

    investment_name: str  # Link to the Investment object by name
    buy_date: datetime.date
    buy_quantity: Decimal
    buy_rate: Decimal
    description: Optional[str] = None
    sell_date: Optional[datetime.date] = None
    sell_quantity: Optional[Decimal] = None
    sell_rate: Optional[Decimal] = None
    gain_from_sale: Optional[Decimal] = None


class _InvestmentJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder to handle Decimal, Enum, and datetime.date types for serialization.
    """

    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, datetime.date):
            return o.isoformat()  # Convert date to ISO 8601 string (YYYY-MM-DD)
        return super().default(o)


def save_investments_to_json(filepath: str, investments: List[Investment]):
    """Saves a list of Investment objects to a JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        # Convert list of dataclass objects to list of dicts for serialization
        list_of_dicts = [asdict(inv) for inv in investments]
        json.dump(list_of_dicts, f, cls=_InvestmentJSONEncoder, indent=4)


def load_investments_from_json(filepath: str) -> List[Investment]:
    """Loads a list of Investment objects from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Reconstruct Investment objects from the loaded data
        return [
            Investment(
                investment_name=item["investment_name"],
                ticker=item["ticker"],
                five_year_annualised_return=Decimal(
                    item["five_year_annualised_return"]
                ),
                ten_year_annualised_return=Decimal(item["ten_year_annualised_return"]),
                currency=Currency(item["currency"]),
            )
            for item in data
        ]
    except FileNotFoundError:
        # If the file doesn't exist, it's not an error; just return an empty list.
        return []


def save_transactions_to_json(
    filepath: str, transactions_data: Dict[str, List[Transaction]]
):
    """Saves a dictionary of lists of Transaction objects to a JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        # Convert dict of lists of dataclass objects to dict of lists of dicts
        serializable_data = {}
        for inv_name, transactions in transactions_data.items():
            serializable_data[inv_name] = [asdict(tx) for tx in transactions]
        json.dump(serializable_data, f, cls=_InvestmentJSONEncoder, indent=4)


def load_transactions_from_json(filepath: str) -> Dict[str, List[Transaction]]:
    """Loads a dictionary of lists of Transaction objects from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Reconstruct Transaction objects from the loaded data
        transactions_data = {}
        for inv_name, transactions_list_data in data.items():
            transactions_data[inv_name] = [
                Transaction(
                    investment_name=item["investment_name"],
                    buy_date=datetime.date.fromisoformat(item["buy_date"]),
                    buy_quantity=Decimal(item["buy_quantity"]),
                    buy_rate=Decimal(item["buy_rate"]),
                    description=item.get("description"),
                    sell_date=(
                        datetime.date.fromisoformat(item["sell_date"])
                        if item.get("sell_date")
                        else None
                    ),
                    sell_quantity=(
                        Decimal(item["sell_quantity"])
                        if item.get("sell_quantity") is not None
                        else None
                    ),
                    sell_rate=(
                        Decimal(item["sell_rate"])
                        if item.get("sell_rate") is not None
                        else None
                    ),
                    gain_from_sale=(
                        Decimal(item["gain_from_sale"])
                        if item.get("gain_from_sale") is not None
                        else None
                    ),
                )
                for item in transactions_list_data
            ]
        return transactions_data
    except FileNotFoundError:
        return {}  # Return empty dict if file doesn't exist
