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
        hist = stock.history(start=start, end=end)
        
        if not hist.empty:
            closest = hist.iloc[-1]['Close']
            price = Decimal(str(closest))
            rate_cache[cache_key] = (datetime.datetime.now(), price)
            save_rate_cache(RATE_CACHE_FILE, rate_cache)
            return price
        return None
    except Exception as e:
        return None


def get_historical_nav(ticker: str, date: datetime.date) -> Optional[Decimal]:
    """Get historical NAV for Indian mutual fund using scheme code."""
    from api_calls import rate_cache, save_rate_cache, RATE_CACHE_FILE
    
    cache_key = f"HIST_{ticker}_{date.isoformat()}"
    
    if cache_key in rate_cache:
        return rate_cache[cache_key][1]
    
    # MFAPI doesn't support historical data, return None
    return None

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
        cost_basis = Decimal(0)
        total_gains = Decimal(0)
        
        for transaction in transactions:
            if transaction.buy_date and transaction.buy_date <= target_date:
                if transaction.buy_quantity and transaction.buy_rate:
                    holdings += transaction.buy_quantity
                    cost_basis += transaction.buy_quantity * transaction.buy_rate
            
            if transaction.sell_date and transaction.sell_date <= target_date:
                if transaction.sell_quantity:
                    holdings -= transaction.sell_quantity
                    if transaction.buy_rate:
                        cost_basis -= transaction.sell_quantity * transaction.buy_rate
            
            if transaction.gain_date and transaction.gain_date <= target_date:
                if transaction.gain_amount:
                    total_gains += transaction.gain_amount
        
        if holdings > 0 or total_gains > 0:
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
            
            market_value = (holdings * rate if rate else cost_basis) + total_gains
            
            if investment.currency == Currency.USD:
                total_usd += market_value
                cost_basis_usd += cost_basis
            else:
                total_inr += market_value
                cost_basis_inr += cost_basis
    
    return total_usd, total_inr, cost_basis_usd, cost_basis_inr


def calculate_no_goog_sale_value(
    investments: List[Investment],
    transactions_data: Dict[str, List[Transaction]],
    target_date: datetime.date,
    current_rates: Dict[str, Decimal],
    is_today: bool = False
) -> Tuple[Decimal, Decimal]:
    """Calculate portfolio value as if GOOG was never sold."""
    goog_investment = None
    for inv in investments:
        if 'GOOG' in inv.investment_name.upper():
            goog_investment = inv
            break
    
    if not goog_investment:
        return Decimal(0), Decimal(0)
    
    # Get GOOG sales up to target date
    goog_transactions = transactions_data.get(goog_investment.investment_name, [])
    goog_sales = [(t.sell_date, t.sell_quantity * t.sell_rate) 
                  for t in goog_transactions 
                  if t.sell_date and t.sell_date <= target_date and t.sell_quantity and t.sell_rate]
    
    if not goog_sales:
        return Decimal(0), Decimal(0)
    
    # Find purchases within 30 days after each GOOG sale
    exclude_transactions = {}  # {inv_name: [transaction_indices]}
    
    for sale_date, sale_amount in goog_sales:
        window_start = sale_date
        window_end = sale_date + datetime.timedelta(days=30)
        remaining_amount = sale_amount
        
        for inv in investments:
            if inv.investment_name == goog_investment.investment_name:
                continue
            
            txs = transactions_data.get(inv.investment_name, [])
            for idx, tx in enumerate(txs):
                if (tx.buy_date and window_start <= tx.buy_date <= window_end and 
                    tx.buy_quantity and tx.buy_rate):
                    buy_amount = tx.buy_quantity * tx.buy_rate
                    # If purchase is within window and we have remaining sale proceeds
                    if remaining_amount > 0 and buy_amount <= remaining_amount * Decimal('1.1'):
                        if inv.investment_name not in exclude_transactions:
                            exclude_transactions[inv.investment_name] = []
                        exclude_transactions[inv.investment_name].append(idx)
                        remaining_amount -= buy_amount
    
    # Calculate GOOG value if never sold
    goog_holdings = Decimal(0)
    for tx in goog_transactions:
        if tx.buy_date and tx.buy_date <= target_date and tx.buy_quantity:
            goog_holdings += tx.buy_quantity
    
    if is_today:
        goog_rate = current_rates.get(goog_investment.investment_name)
    elif goog_investment.ticker:
        goog_rate = get_historical_stock_price(goog_investment.ticker, target_date)
    else:
        goog_rate = None
    
    hypothetical_goog_value = goog_holdings * goog_rate if goog_rate and goog_holdings > 0 else Decimal(0)
    
    # Calculate portfolio excluding GOOG-funded purchases
    total_usd = Decimal(0)
    total_inr = Decimal(0)
    
    for investment in investments:
        if investment.investment_name == goog_investment.investment_name:
            continue
            
        transactions = transactions_data.get(investment.investment_name, [])
        excluded_indices = exclude_transactions.get(investment.investment_name, [])
        
        holdings = Decimal(0)
        for idx, tx in enumerate(transactions):
            if idx in excluded_indices:
                continue
            if tx.buy_date and tx.buy_date <= target_date and tx.buy_quantity:
                holdings += tx.buy_quantity
            if tx.sell_date and tx.sell_date <= target_date and tx.sell_quantity:
                holdings -= tx.sell_quantity
        
        if holdings > 0:
            if is_today:
                rate = current_rates.get(investment.investment_name)
            elif investment.ticker:
                if investment.ticker.isdigit() and len(investment.ticker) == 6:
                    rate = get_historical_nav(investment.ticker, target_date)
                else:
                    rate = get_historical_stock_price(investment.ticker, target_date)
            else:
                rate = None
            
            if rate:
                value = holdings * rate
                if investment.currency == Currency.USD:
                    total_usd += value
                else:
                    total_inr += value
    
    total_usd += hypothetical_goog_value
    return total_usd, total_inr


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
            if t.sell_date:
                all_dates.append(t.sell_date)
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
    
    for date in sorted_dates:
        is_today = (date == end_date)
        total_usd, total_inr, cost_usd, cost_inr = calculate_portfolio_value_on_date(
            investments, transactions_data, date, current_rates, is_today
        )
        
        no_goog_usd, no_goog_inr = calculate_no_goog_sale_value(
            investments, transactions_data, date, current_rates, is_today
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
    
    return snapshots


def prepare_chart_data(snapshots: List[PortfolioSnapshot], usd_to_inr_rate: Decimal) -> Dict:
    """Prepare data for Chart.js visualization."""
    
    chart_data = {
        'total_value': [],
        'invested_amount': [],
        'no_goog_sale': []
    }
    
    # Use same historical rate as home page (March 15, 2024)
    purchase_rate = get_historical_usd_inr_rate(datetime.date(2024, 3, 15))
    if not purchase_rate:
        purchase_rate = Decimal('82.83')
    
    for i, snapshot in enumerate(snapshots):
        # Use current rate for today, historical for past dates
        if snapshot.date == datetime.date.today():
            historical_rate = usd_to_inr_rate
        else:
            historical_rate = get_historical_usd_inr_rate(snapshot.date)
            if not historical_rate:
                historical_rate = usd_to_inr_rate
        
        total_inr = snapshot.total_value_inr + (snapshot.total_value_usd * historical_rate)
        cost_inr = snapshot.cost_basis_inr + (snapshot.cost_basis_usd * purchase_rate)
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