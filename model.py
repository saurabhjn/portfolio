import json
from dataclasses import dataclass, asdict
from enum import Enum
from decimal import Decimal
from typing import List


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


class _InvestmentJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder to handle Decimal and Enum types for serialization.
    """
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, Enum):
            return o.value
        return super().default(o)


def save_investments_to_json(filepath: str, investments: List[Investment]):
    """Saves a list of Investment objects to a JSON file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        # Convert list of dataclass objects to list of dicts for serialization
        list_of_dicts = [asdict(inv) for inv in investments]
        json.dump(list_of_dicts, f, cls=_InvestmentJSONEncoder, indent=4)


def load_investments_from_json(filepath: str) -> List[Investment]:
    """Loads a list of Investment objects from a JSON file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Reconstruct Investment objects from the loaded data
        return [
            Investment(
                investment_name=item['investment_name'],
                ticker=item['ticker'],
                five_year_annualised_return=Decimal(item['five_year_annualised_return']),
                ten_year_annualised_return=Decimal(item['ten_year_annualised_return']),
                currency=Currency(item['currency'])
            ) for item in data
        ]
    except FileNotFoundError:
        # If the file doesn't exist, it's not an error; just return an empty list.
        return []
