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
    EUR = "EUR"
    GBP = "GBP"


class ExpenseCategory(Enum):
    """Enumeration for expense categories."""

    LIVING = "Living"
    EDUCATION = "Education"
    HEALTH = "Health"
    RETIREMENT = "Retirement"
    LEISURE = "Leisure"
    OTHER = "Other"


class RecurrencePeriod(Enum):
    """Enumeration for recurrence periods."""

    NONE = "None"
    MONTHLY = "Monthly"
    YEARLY = "Yearly"
    EVERY_5_YEARS = "Every 5 Years"


@dataclass
class Investment:
    """A data class representing a financial investment."""

    investment_name: str
    ticker: str
    currency: Currency
    five_year_annualised_return: Optional[Decimal] = None
    ten_year_annualised_return: Optional[Decimal] = None


@dataclass
class Transaction:
    """A data class representing a financial transaction for an investment."""

    buy_date: Optional[datetime.date] = None
    buy_quantity: Optional[Decimal] = None
    buy_rate: Optional[Decimal] = None
    description: Optional[str] = None
    sell_date: Optional[datetime.date] = None
    sell_quantity: Optional[Decimal] = None
    sell_rate: Optional[Decimal] = None
    gain_from_sale: Optional[Decimal] = None
    gain_date: Optional[datetime.date] = None
    gain_amount: Optional[Decimal] = None


@dataclass
class Expense:
    """A data class representing a future or recurring expense."""

    name: str
    amount: Decimal
    currency: Currency
    date: datetime.date
    category: ExpenseCategory
    is_recurring: bool = False
    recurrence_period: RecurrencePeriod = RecurrencePeriod.NONE
    end_date: Optional[datetime.date] = None


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
                currency=Currency(item["currency"]),
                five_year_annualised_return=(
                    Decimal(item["five_year_annualised_return"])
                    if item.get("five_year_annualised_return") is not None
                    else None
                ),
                ten_year_annualised_return=(
                    Decimal(item["ten_year_annualised_return"])
                    if item.get("ten_year_annualised_return") is not None
                    else None
                ),
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
                    buy_date=(
                        datetime.date.fromisoformat(item["buy_date"])
                        if item.get("buy_date")
                        else None
                    ),
                    buy_quantity=(
                        Decimal(item["buy_quantity"])
                        if item.get("buy_quantity")
                        else None
                    ),
                    buy_rate=(
                        Decimal(item["buy_rate"]) if item.get("buy_rate") else None
                    ),
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
                    gain_date=(
                        datetime.date.fromisoformat(item["gain_date"])
                        if item.get("gain_date")
                        else None
                    ),
                    gain_amount=(
                        Decimal(item["gain_amount"])
                        if item.get("gain_amount") is not None
                        else None
                    ),
                )
                for item in transactions_list_data
            ]
        return transactions_data
    except FileNotFoundError:
        return {}  # Return empty dict if file doesn't exist


def save_expenses_to_json(filepath: str, expenses: List[Expense]):
    """Saves a list of Expense objects to a JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        list_of_dicts = [asdict(e) for e in expenses]
        json.dump(list_of_dicts, f, cls=_InvestmentJSONEncoder, indent=4)


def load_expenses_from_json(filepath: str) -> List[Expense]:
    """Loads a list of Expense objects from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        return [
            Expense(
                name=item["name"],
                amount=Decimal(item["amount"]),
                currency=Currency(item["currency"]),
                date=datetime.date.fromisoformat(item["date"]),
                category=ExpenseCategory(item["category"]),
                is_recurring=item.get("is_recurring", False),
                recurrence_period=RecurrencePeriod(
                    item.get("recurrence_period", "None")
                ),
                end_date=(
                    datetime.date.fromisoformat(item["end_date"])
                    if item.get("end_date")
                    else None
                ),
            )
            for item in data
        ]
    except FileNotFoundError:
        return []


def calculate_transaction_totals(transactions: List[Transaction]) -> Dict[str, Decimal]:
    """
    Calculates summary totals for a list of transactions.

    Returns:
        A dictionary containing 'total_buy_quantity' and 'total_buy_amount'.
        A dictionary containing total quantities and amounts for buys, sells,
        and gains.
    """
    total_buy_quantity = Decimal(0)
    total_buy_amount = Decimal(0)
    total_sell_quantity = Decimal(0)
    total_sell_amount = Decimal(0)
    total_gain_amount = Decimal(0)
    total_gain_from_sale = Decimal(0)
    net_buy_amount = Decimal(0)

    for tx in transactions:
        if tx.buy_quantity is not None and tx.buy_rate is not None:
            total_buy_quantity += tx.buy_quantity
            total_buy_amount += tx.buy_quantity * tx.buy_rate
            if tx.sell_quantity is not None:
                net_buy_amount += (tx.buy_quantity - tx.sell_quantity) * tx.buy_rate
            else:
                net_buy_amount += tx.buy_quantity * tx.buy_rate
        if tx.sell_quantity is not None and tx.sell_rate is not None:
            total_sell_quantity += tx.sell_quantity
            total_sell_amount += tx.sell_quantity * tx.sell_rate
        if tx.gain_from_sale is not None:
            total_gain_from_sale += tx.gain_from_sale
        if tx.gain_amount is not None:
            total_gain_amount += tx.gain_amount

    return {
        "total_buy_quantity": total_buy_quantity,
        "total_buy_amount": total_buy_amount,
        "total_sell_quantity": total_sell_quantity,
        "total_sell_amount": total_sell_amount,
        "total_gain_amount": total_gain_amount,
        "total_gain_from_sale": total_gain_from_sale,
        "net_buy_amount": net_buy_amount,
    }
