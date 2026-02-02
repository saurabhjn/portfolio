import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from pyxirr import xirr as pyxirr_calc

from model import Transaction


def generate_cash_flows_from_transactions(
    transactions: List[Transaction],
) -> List[Tuple[datetime.date, Decimal]]:
    """
    Generates a list of cash flow tuples from a list of transactions.

    Args:
        transactions: A list of Transaction objects.

    Returns:
        A list of (date, amount) tuples representing cash flows.
    """
    cash_flows = []
    for tx in transactions:
        if tx.buy_date and tx.buy_rate is not None:
            if tx.buy_quantity is not None and tx.buy_quantity > 0:
                cash_flows.append((tx.buy_date, -(tx.buy_quantity * tx.buy_rate)))
            else:
                cash_flows.append((tx.buy_date, -tx.buy_rate))
        if tx.sell_date and tx.sell_rate is not None:
            if tx.sell_quantity is not None and tx.sell_quantity > 0:
                cash_flows.append((tx.sell_date, tx.sell_quantity * tx.sell_rate))
            else:
                cash_flows.append((tx.sell_date, tx.sell_rate))
        if tx.gain_date and tx.gain_amount is not None:
            cash_flows.append((tx.gain_date, tx.gain_amount))
    return cash_flows


def calculate_xirr_from_cash_flows(
    cash_flows: List[Tuple[datetime.date, Decimal]]
) -> Optional[Decimal]:
    """
    Calculates the XIRR for a given list of cash flows.

    Args:
        cash_flows: A list of tuples, where each tuple is (date, amount).

    Returns:
        The XIRR as a percentage (Decimal), or None if calculation is not possible.
    """
    if len(cash_flows) < 2:
        return None

    # Sort by date and separate into two lists for the xirr function
    cash_flows.sort(key=lambda item: item[0])
    dates, values = zip(*cash_flows)

    # XIRR requires at least one positive and one negative cash flow
    if not (any(v > 0 for v in values) and any(v < 0 for v in values)):
        return None

    try:
        # Calculate XIRR; pyxirr returns a float
        result = pyxirr_calc(dates, values)

        # The library can return NaN or infinity on calculation errors
        if result is None or abs(result) == float("inf") or result != result:
            return None

        return Decimal(result) * 100  # Return as a percentage
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def calculate_investment_xirr(
    transactions: List[Transaction],
    current_value: Optional[Decimal],
    current_date: datetime.date,
) -> Optional[Decimal]:
    """Calculates the XIRR for a single investment's series of transactions."""

    # XIRR is not meaningful without historical transactions
    if not transactions and (current_value is None or current_value == 0):
        return None

    # Generate the historical cash flows from transaction records
    cash_flows = generate_cash_flows_from_transactions(transactions)

    # Add the current market value of holdings as the final positive cash flow.
    # The caller is responsible for calculating the correct current_value.
    # We only add it if there's a positive value to be realized.
    if current_value is not None and current_value > 0:
        cash_flows.append((current_date, current_value))

    return calculate_xirr_from_cash_flows(cash_flows)
