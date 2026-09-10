import datetime
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import requests
import json
import yfinance as yf

from model import Transaction, Investment, Currency


@dataclass
class PortfolioSnapshot:
    date: datetime.date
    total_value_usd: Decimal
    total_value_inr: Decimal
    cost_basis_usd: Decimal
    cost_basis_inr: Decimal
    new_investment_usd: Decimal = Decimal(0)
    new_investment_inr: Decimal = Decimal(0)
    is_new_investment: bool = False
    event_description: str = ""
    no_goog_sale_usd: Decimal = Decimal(0)
    no_goog_sale_inr: Decimal = Decimal(0)


def get_historical_stock_price(ticker: str, date: datetime.date) -> Optional[Decimal]:
    """Get historical stock price using Yahoo Finance with caching."""
    from api_calls import rate_cache, save_rate_cache, RATE_CACHE_FILE
    
    cache_key = f"HIST_{ticker}_{date.isoformat()}"
    
    if cache_key in rate_cache:
        return rate_cache[cache_key][1]
    
    try:
        stock = yf.Ticker(ticker)
        start = date - datetime.timedelta(days=7)
        end = date + datetime.timedelta(days=1)
        # auto_adjust=False returns the close of the day. The default
        # subtracts later dividends from every past price.
        hist = stock.history(start=start, end=end, auto_adjust=False)
        
        if not hist.empty:
            closest = hist.iloc[-1]['Close']
            price = Decimal(str(closest))
            rate_cache[cache_key] = (datetime.datetime.now(), price)
            save_rate_cache(RATE_CACHE_FILE, rate_cache)
            return price
        return None
    except Exception as e:
        return None


def get_last_known_price(ticker: str, date: datetime.date,
                         max_lookback_days: int = 400) -> Optional[Decimal]:
    """Most recent cached price for ``ticker`` on or before ``date``.

    Used to carry a price forward when a live/historical lookup fails (e.g. API
    throttling). Reads only the existing cache — never hits the network — so a
    missing point holds the line flat instead of collapsing to cost basis.
    """
    from api_calls import rate_cache

    for i in range(max_lookback_days + 1):
        d = date - datetime.timedelta(days=i)
        key = f"HIST_{ticker}_{d.isoformat()}"
        if key in rate_cache:
            return rate_cache[key][1]
    return None


# Full NAV history per scheme code, downloaded once per process and again
# after MF_HISTORY_MAX_AGE: {scheme_code: (fetched_at, sorted [(date, nav)])}.
# A None series means the download failed.
_mf_nav_history: Dict[str, Tuple[datetime.datetime, Optional[List[Tuple[datetime.date, Decimal]]]]] = {}
MF_HISTORY_MAX_AGE = datetime.timedelta(hours=12)


def _load_mf_nav_history(scheme_code: str) -> Optional[List[Tuple[datetime.date, Decimal]]]:
    """Download the complete NAV series for a scheme code from mfapi.in."""
    cached = _mf_nav_history.get(scheme_code)
    if cached and datetime.datetime.now() - cached[0] < MF_HISTORY_MAX_AGE:
        return cached[1]
    series = None
    try:
        response = requests.get(f"https://api.mfapi.in/mf/{scheme_code}", timeout=20)
        response.raise_for_status()
        rows = response.json().get("data") or []
        parsed = []
        for entry in rows:
            try:
                d = datetime.datetime.strptime(entry["date"], "%d-%m-%Y").date()
                parsed.append((d, Decimal(str(entry["nav"]))))
            except (KeyError, ValueError, TypeError):
                continue
        if parsed:
            parsed.sort()
            series = parsed
    except Exception:
        series = None
    _mf_nav_history[scheme_code] = (datetime.datetime.now(), series)
    return series


def get_historical_nav(ticker: str, date: datetime.date) -> Optional[Decimal]:
    """NAV of an Indian mutual fund (6-digit scheme code) on or before ``date``.

    A cached value comes from rate_cache. On a miss the full NAV series is
    downloaded once per process and the value is stored in rate_cache in
    memory; ``flush_rate_cache`` writes the cache to disk.
    """
    from api_calls import rate_cache

    cache_key = f"HIST_{ticker}_{date.isoformat()}"

    if cache_key in rate_cache:
        return rate_cache[cache_key][1]

    series = _load_mf_nav_history(ticker)
    if not series:
        return None

    # Last NAV dated on or before the target date (funds do not publish a
    # NAV on weekends and holidays).
    import bisect
    idx = bisect.bisect_right(series, (date, Decimal("Infinity")))
    if idx == 0:
        return None
    nav_date, nav = series[idx - 1]
    if (date - nav_date).days > 10:
        return None
    rate_cache[cache_key] = (datetime.datetime.now(), nav)
    return nav


def flush_rate_cache() -> None:
    """Write the in-memory rate cache to disk."""
    from api_calls import rate_cache, save_rate_cache, RATE_CACHE_FILE
    save_rate_cache(RATE_CACHE_FILE, rate_cache)

def get_historical_usd_inr_rate(date: datetime.date) -> Optional[Decimal]:
    """Get historical USD to INR rate with caching."""
    from api_calls import rate_cache, save_rate_cache, RATE_CACHE_FILE
    import sys
    
    cache_key = f"USD_INR_RATE_{date.isoformat()}"
    
    if cache_key in rate_cache:
        return rate_cache[cache_key][1]
    
    try:
        url = f"https://api.frankfurter.app/{date.strftime('%Y-%m-%d')}?from=USD&to=INR"
        response = requests.get(url, timeout=10)
        data = response.json()
        rate = data.get("rates", {}).get("INR")
        if rate:
            rate_decimal = Decimal(str(rate))
            rate_cache[cache_key] = (datetime.datetime.now(), rate_decimal)
            save_rate_cache(RATE_CACHE_FILE, rate_cache)
            return rate_decimal
        return None
    except Exception as e:
        return None


def _is_reinvested_dividend(transaction: Transaction) -> bool:
    """True for a dividend gain entry whose cash became a "Reinvest Shares" lot.

    The lot already holds that money as units valued at market. Counting the
    gain entry as well counts the dividend twice.
    """
    return "reinvest" in (transaction.description or "").lower()


def calculate_portfolio_value_on_date(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    target_date: datetime.date,
    current_rates: Dict[str, Decimal],
    is_today: bool = False,
    exclude_investments: set = None
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Calculate portfolio value using historical prices."""
    total_usd = Decimal(0)
    total_inr = Decimal(0)
    cost_basis_usd = Decimal(0)
    cost_basis_inr = Decimal(0)
    
    if exclude_investments is None:
        exclude_investments = set()
    
    for investment in investments:
        if investment.investment_name in exclude_investments:
            continue
            
        transactions = transactions_data.get(investment.investment_name, [])
        
        holdings = Decimal(0)
        cost_basis = Decimal(0)   # money in the position at cost (units + flat)
        flat_amount = Decimal(0)  # quantity-less payments minus quantity-less payouts
        total_gains = Decimal(0)
        
        for transaction in transactions:
            if transaction.buy_date and transaction.buy_date <= target_date:
                if transaction.buy_quantity and transaction.buy_rate:
                    holdings += transaction.buy_quantity
                    cost_basis += transaction.buy_quantity * transaction.buy_rate
                elif transaction.buy_rate:
                    # A payment that does not carry units (cash balance,
                    # premium, capital call). The index page treats it the
                    # same way.
                    flat_amount += transaction.buy_rate
                    cost_basis += transaction.buy_rate

            for sale in transaction.sales:
                if not (sale.sell_date and sale.sell_date <= target_date):
                    continue
                if sale.sell_quantity:
                    holdings -= sale.sell_quantity
                    if transaction.buy_rate:
                        cost_basis -= sale.sell_quantity * transaction.buy_rate
                elif sale.sell_rate:
                    # A payout or return of capital that does not carry units.
                    flat_amount -= sale.sell_rate
                    cost_basis -= sale.sell_rate

            if transaction.gain_date and transaction.gain_date <= target_date:
                if transaction.gain_amount and not _is_reinvested_dividend(transaction):
                    total_gains += transaction.gain_amount
        
        if holdings > 0 or flat_amount != 0 or total_gains > 0:
            if is_today:
                rate = current_rates.get(investment.investment_name)
            elif investment.ticker:
                # Check if it's a mutual fund (6-digit scheme code)
                if investment.ticker.isdigit() and len(investment.ticker) == 6:
                    rate = get_historical_nav(investment.ticker, target_date)
                else:
                    rate = get_historical_stock_price(investment.ticker, target_date)
            else:
                rate = None

            # If the live/historical lookup came back empty (typically API
            # throttling), carry the last known price forward rather than
            # collapsing this day to cost basis, which paints a false spike.
            if rate is None and investment.ticker:
                rate = get_last_known_price(investment.ticker, target_date)

            if rate:
                market_value = holdings * rate + flat_amount + total_gains
            else:
                market_value = cost_basis + total_gains
            
            if investment.currency == Currency.USD:
                total_usd += market_value
                cost_basis_usd += cost_basis
            else:
                total_inr += market_value
                cost_basis_inr += cost_basis
    
    return total_usd, total_inr, cost_basis_usd, cost_basis_inr


REINVEST_WINDOW_DAYS = 45


def _price_on(investment: Investment, date: datetime.date,
              current_rates: Dict[str, Decimal], is_today: bool) -> Optional[Decimal]:
    """Price of a ticker investment on ``date`` (today's live rate when is_today)."""
    if is_today:
        rate = current_rates.get(investment.investment_name)
        if rate:
            return rate
    if not investment.ticker:
        return None
    if investment.ticker.isdigit() and len(investment.ticker) == 6:
        rate = get_historical_nav(investment.ticker, date)
    else:
        rate = get_historical_stock_price(investment.ticker, date)
    if rate is None:
        rate = get_last_known_price(investment.ticker, date)
    return rate


def _attribute_sale_proceeds(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    sold_investment: Investment,
) -> List[dict]:
    """Where the proceeds of each sale of ``sold_investment`` went.

    Every sale is matched to the ordinary purchases (not dividend
    reinvestments) of other same-currency ticker investments made within
    REINVEST_WINDOW_DAYS after the sale, earliest purchase first. Each
    purchase absorbs proceeds up to its own amount and is funded only once.
    Proceeds left unmatched count as cash at par.

    Returns one record per sale:
      {"date", "shares", "proceeds",
       "legs": [(investment, purchase_date, amount)], "cash"}
    """
    buys = []  # [investment, date, amount, remaining]
    for inv in investments:
        if inv is sold_investment or not inv.ticker or inv.currency != sold_investment.currency:
            continue
        for tx in transactions_data.get(inv.investment_name, []):
            if not (tx.buy_date and tx.buy_quantity and tx.buy_rate):
                continue
            if "reinvest" in (tx.description or "").lower():
                continue
            amount = tx.buy_quantity * tx.buy_rate
            buys.append([inv, tx.buy_date, amount, amount])
    buys.sort(key=lambda b: b[1])

    sales = []
    for tx in transactions_data.get(sold_investment.investment_name, []):
        for sale in tx.sales:
            if sale.sell_date and sale.sell_quantity and sale.sell_rate:
                sales.append((sale.sell_date, sale.sell_quantity, sale.sell_quantity * sale.sell_rate))
    sales.sort()

    records = []
    for sell_date, shares, proceeds in sales:
        window_end = sell_date + datetime.timedelta(days=REINVEST_WINDOW_DAYS)
        candidates = [b for b in buys if sell_date <= b[1] <= window_end and b[3] > 0]
        legs = []
        remaining = proceeds
        for b in candidates:
            if remaining <= 0:
                break
            take = min(b[3], remaining)
            b[3] -= take
            remaining -= take
            legs.append((b[0], b[1], take))
        records.append({
            "date": sell_date,
            "shares": shares,
            "proceeds": proceeds,
            "legs": legs,
            "cash": remaining,
        })
    return records


def calculate_no_goog_sale_value(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    target_date: datetime.date,
    current_rates: Dict[str, Decimal],
    is_today: bool = False,
    actual_usd: Optional[Decimal] = None,
    actual_inr: Optional[Decimal] = None,
    proceeds_records: Optional[List[dict]] = None,
) -> Tuple[Decimal, Decimal]:
    """Portfolio value as if the sold-down holding had never been sold.

    Returns the *full* portfolio value (not a delta) split into (usd, inr).
    For every sale on or before ``target_date`` the line

      * adds back the sold shares at this date's price, and
      * removes what the sale proceeds became: the purchases they funded
        (see ``_attribute_sale_proceeds``), valued at this date's price of
        the fund bought, plus any unabsorbed proceeds at par.

    The actual portfolio already holds those funded purchases. Without the
    removal the line counts the same money twice: once as shares and once as
    the units they bought. When no sale has happened yet, or a price is
    missing, this equals the actual portfolio value.

    ``actual_usd``/``actual_inr`` may be passed in to reuse an already-computed
    portfolio value for this date. ``proceeds_records`` may be passed in to
    reuse the sale-to-purchase matching across dates.
    """
    if actual_usd is None or actual_inr is None:
        actual_usd, actual_inr, _, _ = calculate_portfolio_value_on_date(
            investments, transactions_data, target_date, current_rates, is_today
        )

    goog_investment = None
    for inv in investments:
        if 'GOOG' in inv.investment_name.upper():
            goog_investment = inv
            break

    if not goog_investment:
        return actual_usd, actual_inr

    if proceeds_records is None:
        proceeds_records = _attribute_sale_proceeds(investments, transactions_data, goog_investment)

    past_sales = [r for r in proceeds_records if r["date"] <= target_date]
    if not past_sales:
        return actual_usd, actual_inr

    goog_rate = _price_on(goog_investment, target_date, current_rates, is_today)
    if not goog_rate:
        return actual_usd, actual_inr

    adjustment = Decimal(0)
    for rec in past_sales:
        adjustment += rec["shares"] * goog_rate
        adjustment -= rec["cash"]
        for inv, buy_date, amount in rec["legs"]:
            price_then = _price_on(inv, buy_date, current_rates, False)
            price_now = _price_on(inv, target_date, current_rates, is_today)
            if price_then and price_now:
                adjustment -= amount * price_now / price_then
            else:
                adjustment -= amount

    if goog_investment.currency == Currency.USD:
        return actual_usd + adjustment, actual_inr
    return actual_usd, actual_inr + adjustment


def detect_new_investments(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    date: datetime.date,
    threshold_days: int = 7
) -> Tuple[Decimal, Decimal, bool, str]:
    """Detect if there were significant new investments around a date."""
    new_usd = Decimal(0)
    new_inr = Decimal(0)
    events = []
    
    for investment in investments:
        transactions = transactions_data.get(investment.investment_name, [])
        for transaction in transactions:
            if (transaction.buy_date and 
                abs((transaction.buy_date - date).days) <= threshold_days and
                transaction.buy_quantity and transaction.buy_rate and
                transaction.description != "Reinvest Shares" and
                transaction.description != "Reinvest Dividend"):
                
                investment_amount = transaction.buy_quantity * transaction.buy_rate
                events.append(f"{investment.investment_name}: {transaction.description or 'Buy'}")
                
                if investment.currency == Currency.USD:
                    new_usd += investment_amount
                else:
                    new_inr += investment_amount
    
    is_significant = new_usd > 5000 or new_inr > 50000
    event_desc = "; ".join(events[:3]) if events else "Portfolio Growth"
    
    return new_usd, new_inr, is_significant, event_desc


def generate_portfolio_timeline(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    current_rates: Dict[str, Decimal],
    start_date: datetime.date = None,
    end_date: datetime.date = None
) -> List[PortfolioSnapshot]:
    """Generate a timeline of portfolio values with investment detection."""
    
    # Find all transaction dates
    all_dates = []
    for transactions in transactions_data.values():
        for t in transactions:
            if t.buy_date:
                all_dates.append(t.buy_date)
            for s in t.sales:
                if s.sell_date:
                    all_dates.append(s.sell_date)
            if t.gain_date:
                all_dates.append(t.gain_date)
    
    if not all_dates:
        return []  # No transactions, no graph
    
    if not start_date:
        start_date = max(min(all_dates), datetime.date(2022, 9, 1))  # Start from Sep 2022
    
    if not end_date:
        end_date = datetime.date.today()
    

    
    # Generate daily snapshots
    sorted_dates = []
    current_date = start_date
    while current_date <= end_date:
        sorted_dates.append(current_date)
        current_date += datetime.timedelta(days=1)
    
    # Generate snapshots
    snapshots = []

    goog_investment = next((inv for inv in investments if 'GOOG' in inv.investment_name.upper()), None)
    proceeds_records = (
        _attribute_sale_proceeds(investments, transactions_data, goog_investment)
        if goog_investment else []
    )
    
    for date in sorted_dates:
        is_today = (date == end_date)
        total_usd, total_inr, cost_usd, cost_inr = calculate_portfolio_value_on_date(
            investments, transactions_data, date, current_rates, is_today
        )
        
        no_goog_usd, no_goog_inr = calculate_no_goog_sale_value(
            investments, transactions_data, date, current_rates, is_today,
            actual_usd=total_usd, actual_inr=total_inr,
            proceeds_records=proceeds_records,
        )
        
        new_usd, new_inr, is_new_investment, event_desc = detect_new_investments(
            investments, transactions_data, date
        )
        
        snapshot = PortfolioSnapshot(
            date=date,
            total_value_usd=total_usd,
            total_value_inr=total_inr,
            cost_basis_usd=cost_usd,
            cost_basis_inr=cost_inr,
            new_investment_usd=new_usd,
            new_investment_inr=new_inr,
            is_new_investment=is_new_investment,
            event_description=event_desc,
            no_goog_sale_usd=no_goog_usd,
            no_goog_sale_inr=no_goog_inr
        )
        snapshots.append(snapshot)

    # NAV lookups only update the cache in memory; write it once here.
    flush_rate_cache()
    
    return snapshots


def prepare_chart_data(snapshots: List[PortfolioSnapshot], usd_to_inr_rate: Decimal) -> Dict:
    """Prepare data for Chart.js visualization."""
    
    chart_data = {
        'total_value': [],
        'invested_amount': [],
        'no_goog_sale': []
    }
    
    for i, snapshot in enumerate(snapshots):
        # Use current rate for today, historical for past dates. The same rate
        # is applied to value, cost basis, and the counterfactual line so the lines
        # stay FX-consistent and their gap reflects real gains, not FX drift.
        if snapshot.date == datetime.date.today():
            historical_rate = usd_to_inr_rate
        else:
            historical_rate = get_historical_usd_inr_rate(snapshot.date)
            if not historical_rate:
                historical_rate = usd_to_inr_rate

        total_inr = snapshot.total_value_inr + (snapshot.total_value_usd * historical_rate)
        cost_inr = snapshot.cost_basis_inr + (snapshot.cost_basis_usd * historical_rate)
        no_goog_inr = snapshot.no_goog_sale_inr + (snapshot.no_goog_sale_usd * historical_rate)
        
        chart_data['total_value'].append({
            'x': snapshot.date.strftime('%Y-%m-%d'),
            'y': float(total_inr),
            'event': snapshot.event_description
        })
        
        chart_data['invested_amount'].append({
            'x': snapshot.date.strftime('%Y-%m-%d'),
            'y': float(cost_inr),
            'event': snapshot.event_description
        })
        
        chart_data['no_goog_sale'].append({
            'x': snapshot.date.strftime('%Y-%m-%d'),
            'y': float(no_goog_inr),
            'event': snapshot.event_description
        })
    
    return chart_data