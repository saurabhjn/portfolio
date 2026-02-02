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
        # Convert Decimals to float for compatibility
        float_values = [float(v) for v in values]
        result = pyxirr_calc(dates, float_values)

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
def get_windowed_cash_flow_components(
    transactions: List[Transaction],
    start_date: datetime.date,
    end_date: datetime.date,
    start_rate: Optional[Decimal],
) -> Tuple[Decimal, List[Tuple[datetime.date, Decimal]]]:
    """
    Returns the (starting_value, list_of_flows_within_window).
    Used for calculating windowed XIRR on a single asset or a segment.
    """
    qty_at_start = Decimal(0)
    val_at_start = Decimal(0)
    window_cash_flows = []

    for tx in transactions:
        tx_date = tx.buy_date or tx.sell_date or tx.gain_date
        if not tx_date:
            continue

        # 1. Handle Buys
        if tx.buy_rate is not None:
            amt = (tx.buy_quantity * tx.buy_rate) if (tx.buy_quantity is not None and tx.buy_quantity > 0) else tx.buy_rate
            if tx_date < start_date:
                val_at_start += amt
                if tx.buy_quantity: qty_at_start += tx.buy_quantity
            elif tx_date <= end_date:
                window_cash_flows.append((tx_date, -amt))

        # 2. Handle Sells/Payouts
        if tx.sell_rate is not None:
            amt = (tx.sell_quantity * tx.sell_rate) if (tx.sell_quantity is not None and tx.sell_quantity > 0) else tx.sell_rate
            if tx_date < start_date:
                val_at_start -= amt
                if tx.sell_quantity: qty_at_start -= tx.sell_quantity
            elif tx_date <= end_date:
                window_cash_flows.append((tx_date, amt))

        # 3. Handle Gains (Income/Dividends)
        if tx.gain_amount is not None:
            if tx_date < start_date:
                val_at_start += tx.gain_amount
            elif tx_date <= end_date:
                window_cash_flows.append((tx_date, tx.gain_amount))

    if start_rate is not None:
        calculated_start_value = qty_at_start * start_rate
    else:
        calculated_start_value = val_at_start

    return calculated_start_value, window_cash_flows


def calculate_historical_investment_xirr(
    transactions: List[Transaction],
    start_date: datetime.date,
    end_date: datetime.date,
    start_rate: Optional[Decimal],
    end_market_value: Optional[Decimal],
) -> Optional[Decimal]:
    """
    Calculates the XIRR for a specific time window.
    """
    start_value, window_flows = get_windowed_cash_flow_components(
        transactions, start_date, end_date, start_rate
    )

    if start_value <= 0 and not window_flows:
        return None

    cash_flows = []
    if start_value > 0:
        cash_flows.append((start_date, -start_value))
    
    cash_flows.extend(window_flows)

    if end_market_value is not None and end_market_value > 0:
        cash_flows.append((end_date, end_market_value))

    if len(cash_flows) >= 2:
        total_sum = sum(v for d, v in cash_flows)
        if abs(total_sum) < Decimal("0.01"):
            return Decimal(0)

    return calculate_xirr_from_cash_flows(cash_flows)
