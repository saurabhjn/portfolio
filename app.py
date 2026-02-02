import os
import json
from typing import List, Optional
from functools import wraps

from flask import Flask, render_template, redirect, url_for, flash, request, g, session
import datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal, ROUND_DOWN
from flask_bootstrap import Bootstrap5
from model import (
    Investment,
    Currency,
    Transaction,
    Expense,
    ExpenseCategory,
    RecurrencePeriod,
    load_investments_from_json,
    save_investments_to_json,
    load_transactions_from_json,
    save_transactions_to_json,
    load_expenses_from_json,
    save_expenses_to_json,
    calculate_transaction_totals,
)

from api_calls import (
    get_current_rate,
    get_historical_rate,
    get_usd_to_inr_rate,
    get_historical_usd_to_inr_rate,
    get_rate,
    get_exchange_rates,
    rate_cache,
)
from form import InvestmentForm, TransactionForm, ExpenseForm
from xirr import (
    calculate_investment_xirr,
    calculate_historical_investment_xirr,
    get_windowed_cash_flow_components,
    calculate_xirr_from_cash_flows,
    generate_cash_flows_from_transactions,
)
from portfolio_graph import (
    generate_portfolio_timeline,
    prepare_chart_data,
)
from encryption import encrypt_data, decrypt_data

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "a-default-secret-key-for-dev")

bootstrap = Bootstrap5(app)


@app.context_processor
def inject_now():
    return {'now': datetime.datetime.utcnow}


DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "investments.json")
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")
EXPENSES_FILE = os.path.join(DATA_DIR, "expenses.json")
ENCRYPTED_DATA_FILE = os.path.join(DATA_DIR, "investments.enc")
ENCRYPTED_TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.enc")
ENCRYPTED_EXPENSES_FILE = os.path.join(DATA_DIR, "expenses.enc")

investments = []
transactions_data = {}
expenses = []

def require_unlock(f):
    """Decorator to require data to be unlocked."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('unlocked') or not investments:
            session.clear()
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def load_encrypted_data(password: str):
    """Load and decrypt data files."""
    global investments, transactions_data, expenses
    try:
        with open(ENCRYPTED_DATA_FILE, "r") as f:
            inv_data = decrypt_data(json.load(f), password)
        with open(ENCRYPTED_TRANSACTIONS_FILE, "r") as f:
            trans_data = decrypt_data(json.load(f), password)
        
        exp_data = []
        if os.path.exists(ENCRYPTED_EXPENSES_FILE):
            with open(ENCRYPTED_EXPENSES_FILE, "r") as f:
                exp_data = decrypt_data(json.load(f), password)

        # Save to temp files and use existing load functions
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(inv_data, tf)
            temp_inv = tf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(trans_data, tf)
            temp_trans = tf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(exp_data, tf)
            temp_exp = tf.name

        investments = load_investments_from_json(temp_inv)
        transactions_data = load_transactions_from_json(temp_trans)
        expenses = load_expenses_from_json(temp_exp)

        os.unlink(temp_inv)
        os.unlink(temp_trans)
        os.unlink(temp_exp)
        return True
    except Exception:
        return False

def save_encrypted_data():
    """Save and encrypt data files."""
    password = session.get("password")
    if not password:
        return

    # Save to temp files first
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        temp_inv = tf.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        temp_trans = tf.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        temp_exp = tf.name

    save_investments_to_json(temp_inv, investments)
    save_transactions_to_json(temp_trans, transactions_data)
    save_expenses_to_json(temp_exp, expenses)

    with open(temp_inv, "r") as f:
        inv_data = json.load(f)
    with open(temp_trans, "r") as f:
        trans_data = json.load(f)
    with open(temp_exp, "r") as f:
        exp_data = json.load(f)

    with open(ENCRYPTED_DATA_FILE, "w") as f:
        json.dump(encrypt_data(inv_data, password), f)
    with open(ENCRYPTED_TRANSACTIONS_FILE, "w") as f:
        json.dump(encrypt_data(trans_data, password), f)
    with open(ENCRYPTED_EXPENSES_FILE, "w") as f:
        json.dump(encrypt_data(exp_data, password), f)

    os.unlink(temp_inv)
    os.unlink(temp_trans)
    os.unlink(temp_exp)


def _calculate_investment_metrics(
    investment: Investment, transactions: List[Transaction]
) -> dict:
    """
    Calculates key financial metrics for a single investment based on its transactions.

    Args:
        investment: The Investment object.
        transactions: A list of Transaction objects for the investment.

    Returns:
        A dictionary containing calculated metrics like 'purchase_value',
        'current_value', 'gain', 'current_rate', etc.
    """
    totals = calculate_transaction_totals(transactions)
    remaining_quantity = totals["total_buy_quantity"] - totals["total_sell_quantity"]
    purchase_value = totals["net_buy_amount"]

    current_rate = (
        get_current_rate(investment.ticker, g.get("force_refresh", False)) if investment.ticker else None
    )

    total_gain_amount = totals.get("total_gain_amount", Decimal(0))
    total_gain_from_sale = totals.get("total_gain_from_sale", Decimal(0))

    if current_rate is not None:
        current_value = remaining_quantity * current_rate
        gain = current_value - purchase_value + total_gain_from_sale + total_gain_amount
        current_value_for_xirr = current_value
    else:
        # For investments without a ticker or if rate fetch fails
        current_value = purchase_value + total_gain_amount
        gain = total_gain_amount + total_gain_from_sale
        current_value_for_xirr = current_value - total_gain_amount

    return {
        "totals": totals,
        "remaining_quantity": remaining_quantity,
        "purchase_value": purchase_value,
        "current_rate": current_rate,
        "current_value": current_value,
        "gain": gain,
        "current_value_for_xirr": current_value_for_xirr,
    }


def _format_inr(number_str: str) -> str:
    """
    Helper to format a number string in the Indian numbering system (lakhs, crores).
    """
    parts = number_str.split(".")
    integer_part = parts[0]
    fractional_part = parts[1] if len(parts) > 1 else ""

    last_three = integer_part[-3:]
    rest = integer_part[:-3]

    if rest:
        # Add commas to the rest of the number
        formatted_rest = ""
        for i, char in enumerate(reversed(rest)):
            if i > 0 and i % 2 == 0:
                formatted_rest += ","
            formatted_rest += char
        formatted_rest = formatted_rest[::-1]
        return f"{formatted_rest},{last_three}.{fractional_part}"
    else:
        return f"{integer_part}.{fractional_part}"


@app.template_filter("format_currency")
def format_currency_filter(value, currency):
    """
    Formats a decimal value according to the specified currency's conventions
    (USD, INR, EUR, GBP), rounding down to 2 decimal places and adding symbols.
    """
    if value is None:
        return None
    # Ensure value is a Decimal for accurate quantization
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    # Round down to 2 decimal places
    floored_value = value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    symbol = ""
    if currency == Currency.USD or currency.value == "USD":
        symbol = "$"
        formatted = f"{floored_value:,.2f}"
    elif currency == Currency.INR or currency.value == "INR":
        symbol = "₹"
        formatted = _format_inr(f"{floored_value:.2f}")
    elif currency == Currency.EUR or currency.value == "EUR":
        symbol = "€"
        formatted = f"{floored_value:,.2f}"
    elif currency == Currency.GBP or currency.value == "GBP":
        symbol = "£"
        formatted = f"{floored_value:,.2f}"
    else:
        formatted = f"{floored_value:.2f}"
    
    return f"{symbol}{formatted}"


@app.template_filter("format_quantity")
def format_quantity_filter(value):
    """
    Formats a number with comma separators and two decimal places, rounding down.
    """
    if value is None:
        return None
    try:
        # Ensure value is a Decimal for accurate quantization
        if not isinstance(value, Decimal):
            value = Decimal(str(value))

        # Round down to 2 decimal places for consistency with currency
        quantized_value = value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        # Format with commas and 2 decimal places
        return f"{quantized_value:,.2f}"
    except (ValueError, TypeError):
        return value


@app.template_filter("format_currency_nodot")
def format_currency_nodot_filter(value, currency):
    """
    Formats a decimal value according to the specified currency's conventions
    (USD, INR, EUR, GBP), rounding down to 0 decimal places and adding symbols.
    """
    if value is None:
        return None
    # Ensure value is a Decimal for accurate quantization
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    # Round down to 0 decimal places
    floored_value = value.quantize(Decimal("1"), rounding=ROUND_DOWN)

    symbol = ""
    if currency == Currency.USD or currency.value == "USD":
        symbol = "$"
        formatted = f"{floored_value:,.0f}"
    elif currency == Currency.INR or currency.value == "INR":
        symbol = "₹"
        formatted = _format_inr(f"{floored_value:.2f}").split(".")[0]
    elif currency == Currency.EUR or currency.value == "EUR":
        symbol = "€"
        formatted = f"{floored_value:,.0f}"
    elif currency == Currency.GBP or currency.value == "GBP":
        symbol = "£"
        formatted = f"{floored_value:,.0f}"
    else:
        formatted = f"{floored_value:,.0f}"
        
    return f"{symbol}{formatted}"


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page to unlock encrypted data."""
    # Check if encrypted files exist
    if not os.path.exists(ENCRYPTED_DATA_FILE) or not os.path.exists(ENCRYPTED_TRANSACTIONS_FILE):
        # Fall back to unencrypted files
        if os.path.exists(DATA_FILE) and os.path.exists(TRANSACTIONS_FILE):
            flash("Encrypted files not found. Please run migrate_to_encrypted.py first.", "warning")
            return render_template("login.html", show_migration_warning=True)
        else:
            flash("No data files found.", "danger")
            return render_template("login.html")
    
    if request.method == "POST":
        password = request.form.get("password")
        if load_encrypted_data(password):
            session['unlocked'] = True
            session['password'] = password
            flash("Data unlocked successfully!", "success")
            return redirect(url_for("index"))
        else:
            flash("Incorrect password or corrupted data.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    """Logout and clear session."""
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/")
@require_unlock
def index():
    """Renders the main page with a list of all investments."""
    portfolio_data = []
    total_purchase_value_usd = Decimal(0)
    total_purchase_value_inr = Decimal(0)
    total_current_value_usd = Decimal(0)
    total_current_value_inr = Decimal(0)

    # Combined variables for ticker-based USD investments
    total_purchase_usd_ticker = Decimal(0)
    total_current_usd_ticker = Decimal(0)
    total_gain_usd_ticker = Decimal(0)
    usd_ticker_cash_flows = []

    # Combined variables for ticker-based INR investments
    total_purchase_inr_ticker = Decimal(0)
    total_current_inr_ticker = Decimal(0)
    total_gain_inr_ticker = Decimal(0)
    inr_ticker_cash_flows = []

    # Historical Aggregate Trackers
    today = datetime.date.today()
    date_3m = today - relativedelta(months=3)
    date_6m = today - relativedelta(months=6)
    date_12m = today - relativedelta(years=1)

    usd_3m_start_val = Decimal(0); usd_3m_flows = []
    usd_6m_start_val = Decimal(0); usd_6m_flows = []
    usd_12m_start_val = Decimal(0); usd_12m_flows = []
    
    inr_3m_start_val = Decimal(0); inr_3m_flows = []
    inr_6m_start_val = Decimal(0); inr_6m_flows = []
    inr_12m_start_val = Decimal(0); inr_12m_flows = []

    for inv in investments:
        transactions = transactions_data.get(inv.investment_name, [])
        metrics = _calculate_investment_metrics(inv, transactions)

        # Accumulate totals based on currency
        if inv.currency == Currency.USD:
            total_purchase_value_usd += metrics["purchase_value"]
            if metrics["current_value"] is not None:
                total_current_value_usd += metrics["current_value"]
        elif inv.currency == Currency.INR:
            total_purchase_value_inr += metrics["purchase_value"]
            if metrics["current_value"] is not None:
                total_current_value_inr += metrics["current_value"]

        # If it's a USD investment with a ticker, add to the market totals
        if inv.currency == Currency.USD and inv.ticker:
            total_purchase_usd_ticker += metrics["purchase_value"]
            if metrics["current_value"] is not None:
                total_current_usd_ticker += metrics["current_value"]
            total_gain_usd_ticker += metrics["gain"]

            usd_ticker_cash_flows.extend(
                generate_cash_flows_from_transactions(transactions)
            )
            # Add to historical aggregates
            s3, f3 = get_windowed_cash_flow_components(transactions, date_3m, today, get_historical_rate(inv.ticker, date_3m))
            usd_3m_start_val += s3; usd_3m_flows.extend(f3)
            s6, f6 = get_windowed_cash_flow_components(transactions, date_6m, today, get_historical_rate(inv.ticker, date_6m))
            usd_6m_start_val += s6; usd_6m_flows.extend(f6)
            s12, f12 = get_windowed_cash_flow_components(transactions, date_12m, today, get_historical_rate(inv.ticker, date_12m))
            usd_12m_start_val += s12; usd_12m_flows.extend(f12)

        # If it's an INR investment with a ticker, add to the market totals
        if inv.currency == Currency.INR and inv.ticker:
            total_purchase_inr_ticker += metrics["purchase_value"]
            if metrics["current_value"] is not None:
                total_current_inr_ticker += metrics["current_value"]
            total_gain_inr_ticker += metrics["gain"]

            inr_ticker_cash_flows.extend(
                generate_cash_flows_from_transactions(transactions)
            )
            # Add to historical aggregates
            s3, f3 = get_windowed_cash_flow_components(transactions, date_3m, today, get_historical_rate(inv.ticker, date_3m))
            inr_3m_start_val += s3; inr_3m_flows.extend(f3)
            s6, f6 = get_windowed_cash_flow_components(transactions, date_6m, today, get_historical_rate(inv.ticker, date_6m))
            inr_6m_start_val += s6; inr_6m_flows.extend(f6)
            s12, f12 = get_windowed_cash_flow_components(transactions, date_12m, today, get_historical_rate(inv.ticker, date_12m))
            inr_12m_start_val += s12; inr_12m_flows.extend(f12)

        # Calculate XIRR for the investment
        today = datetime.date.today()
        xirr_value = calculate_investment_xirr(
            transactions, metrics["current_value_for_xirr"], today
        )

        # Historical XIRRs (3m, 6m, 12m)
        xirr_3m = calculate_historical_investment_xirr(
            transactions, today - relativedelta(months=3), today,
            get_historical_rate(inv.ticker, today - relativedelta(months=3)) if inv.ticker else None,
            metrics["current_value"]
        )
        xirr_6m = calculate_historical_investment_xirr(
            transactions, today - relativedelta(months=6), today,
            get_historical_rate(inv.ticker, today - relativedelta(months=6)) if inv.ticker else None,
            metrics["current_value"]
        )
        xirr_12m = calculate_historical_investment_xirr(
            transactions, today - relativedelta(years=1), today,
            get_historical_rate(inv.ticker, today - relativedelta(years=1)) if inv.ticker else None,
            metrics["current_value"]
        )

        # Tag exited investments with tiny residuals (rounding errors)
        # We consider < 0.05 units and < $1/₹1 as effectively exited.
        is_exited = metrics["remaining_quantity"] < Decimal("0.05") and (metrics["current_value"] is None or metrics["current_value"] < Decimal("1.0"))

        portfolio_data.append(
            {
                "investment": inv,
                "total_quantity": metrics["remaining_quantity"],
                "current_rate": metrics["current_rate"],
                "purchase_value": metrics["purchase_value"],
                "current_value": metrics["current_value"],
                "xirr_value": xirr_value,
                "xirr_3m": xirr_3m,
                "xirr_6m": xirr_6m,
                "xirr_12m": xirr_12m,
                "gain": metrics["gain"],
                "is_exited": is_exited,
            }
        )

    # Calculate combined XIRR for all ticker-based USD investments
    if total_current_usd_ticker > 0:
        usd_ticker_cash_flows.append((today, total_current_usd_ticker))
        # Aggregates for historical windows
        if usd_3m_start_val > 0:
            usd_3m_flows.insert(0, (date_3m, -usd_3m_start_val))
            usd_3m_flows.append((today, total_current_usd_ticker))
        if usd_6m_start_val > 0:
            usd_6m_flows.insert(0, (date_6m, -usd_6m_start_val))
            usd_6m_flows.append((today, total_current_usd_ticker))
        if usd_12m_start_val > 0:
            usd_12m_flows.insert(0, (date_12m, -usd_12m_start_val))
            usd_12m_flows.append((today, total_current_usd_ticker))

    usd_ticker_xirr = calculate_xirr_from_cash_flows(usd_ticker_cash_flows)
    usd_3m_xirr = calculate_xirr_from_cash_flows(usd_3m_flows)
    usd_6m_xirr = calculate_xirr_from_cash_flows(usd_6m_flows)
    usd_12m_xirr = calculate_xirr_from_cash_flows(usd_12m_flows)

    # Calculate combined XIRR for all ticker-based INR investments
    if total_current_inr_ticker > 0:
        inr_ticker_cash_flows.append((today, total_current_inr_ticker))
        # Aggregates for historical windows
        if inr_3m_start_val > 0:
            inr_3m_flows.insert(0, (date_3m, -inr_3m_start_val))
            inr_3m_flows.append((today, total_current_inr_ticker))
        if inr_6m_start_val > 0:
            inr_6m_flows.insert(0, (date_6m, -inr_6m_start_val))
            inr_6m_flows.append((today, total_current_inr_ticker))
        if inr_12m_start_val > 0:
            inr_12m_flows.insert(0, (date_12m, -inr_12m_start_val))
            inr_12m_flows.append((today, total_current_inr_ticker))

    inr_ticker_xirr = calculate_xirr_from_cash_flows(inr_ticker_cash_flows)
    inr_3m_xirr = calculate_xirr_from_cash_flows(inr_3m_flows)
    inr_6m_xirr = calculate_xirr_from_cash_flows(inr_6m_flows)
    inr_12m_xirr = calculate_xirr_from_cash_flows(inr_12m_flows)

    # Fetch USD to INR conversion rate
    usd_to_inr_rate = get_usd_to_inr_rate()

    # Calculate grand total in INR
    grand_total_in_inr = None
    if usd_to_inr_rate is not None:
        total_current_value_usd_in_inr = total_current_value_usd * usd_to_inr_rate
        grand_total_in_inr = total_current_value_inr + total_current_value_usd_in_inr

    # Fetch historical USD to INR rate for purchase value calculation
    historical_date = datetime.date(2024, 3, 15)
    historical_usd_to_inr_rate = get_historical_usd_to_inr_rate(historical_date)

    # Calculate grand total purchase value in INR
    grand_total_purchase_in_inr = None
    if historical_usd_to_inr_rate is not None:
        total_purchase_value_usd_in_inr = (
            total_purchase_value_usd * historical_usd_to_inr_rate
        )
        grand_total_purchase_in_inr = (
            total_purchase_value_inr + total_purchase_value_usd_in_inr
        )

    # Calculate a simple overall XIRR
    overall_xirr = None
    if grand_total_purchase_in_inr is not None and grand_total_in_inr is not None:
        if grand_total_purchase_in_inr > 0 and grand_total_in_inr > 0:
            cash_flows = [
                (historical_date, -grand_total_purchase_in_inr),
                (datetime.date.today(), grand_total_in_inr),
            ]
            overall_xirr = calculate_xirr_from_cash_flows(cash_flows)

    # Calculate grand total gain in INR
    grand_total_gain_in_inr = None
    if grand_total_in_inr is not None and grand_total_purchase_in_inr is not None:
        grand_total_gain_in_inr = grand_total_in_inr - grand_total_purchase_in_inr

    # Format totals for display
    total_purchase_usd_str = format_currency_nodot_filter(
        total_purchase_value_usd, Currency.USD
    )
    total_purchase_inr_str = format_currency_nodot_filter(
        total_purchase_value_inr, Currency.INR
    )
    total_current_usd_str = format_currency_nodot_filter(
        total_current_value_usd, Currency.USD
    )
    total_current_inr_str = format_currency_nodot_filter(
        total_current_value_inr, Currency.INR
    )
    total_purchase_usd_ticker_str = format_currency_nodot_filter(
        total_purchase_usd_ticker, Currency.USD
    )
    total_current_usd_ticker_str = format_currency_nodot_filter(
        total_current_usd_ticker, Currency.USD
    )
    total_purchase_inr_ticker_str = format_currency_nodot_filter(
        total_purchase_inr_ticker, Currency.INR
    )
    total_current_inr_ticker_str = format_currency_nodot_filter(
        total_current_inr_ticker, Currency.INR
    )
    total_gain_usd_ticker_str = format_currency_nodot_filter(
        total_gain_usd_ticker, Currency.USD
    )
    total_gain_inr_ticker_str = format_currency_nodot_filter(
        total_gain_inr_ticker, Currency.INR
    )
    grand_total_in_inr_str = (
        format_currency_nodot_filter(grand_total_in_inr, Currency.INR)
        if grand_total_in_inr is not None
        else None
    )
    grand_total_purchase_in_inr_str = (
        format_currency_nodot_filter(grand_total_purchase_in_inr, Currency.INR)
        if grand_total_purchase_in_inr is not None
        else None
    )
    grand_total_gain_in_inr_str = (
        format_currency_nodot_filter(grand_total_gain_in_inr, Currency.INR)
        if grand_total_gain_in_inr is not None
        else None
    )

    return render_template(
        "index.html",
        portfolio_data=portfolio_data,
        total_purchase_usd_str=total_purchase_usd_str,
        total_purchase_inr_str=total_purchase_inr_str,
        total_current_usd_str=total_current_usd_str,
        total_current_inr_str=total_current_inr_str,
        total_purchase_usd_ticker_str=total_purchase_usd_ticker_str,
        total_current_usd_ticker_str=total_current_usd_ticker_str,
        total_gain_usd_ticker_str=total_gain_usd_ticker_str,
        usd_ticker_xirr=usd_ticker_xirr,
        usd_3m_xirr=usd_3m_xirr,
        usd_6m_xirr=usd_6m_xirr,
        usd_12m_xirr=usd_12m_xirr,
        total_purchase_inr_ticker_str=total_purchase_inr_ticker_str,
        total_current_inr_ticker_str=total_current_inr_ticker_str,
        total_gain_inr_ticker_str=total_gain_inr_ticker_str,
        inr_ticker_xirr=inr_ticker_xirr,
        inr_3m_xirr=inr_3m_xirr,
        inr_6m_xirr=inr_6m_xirr,
        inr_12m_xirr=inr_12m_xirr,
        grand_total_in_inr_str=grand_total_in_inr_str,
        grand_total_purchase_in_inr_str=grand_total_purchase_in_inr_str,
        grand_total_gain_in_inr_str=grand_total_gain_in_inr_str,
        overall_xirr=overall_xirr,
    )


@app.route("/add", methods=["GET", "POST"])
@require_unlock
def add_investment():
    """Handles adding a new investment via a web form."""
    form = InvestmentForm()
    if form.validate_on_submit():
        new_investment = Investment(
            investment_name=form.investment_name.data,
            ticker=form.ticker.data,
            five_year_annualised_return=form.five_year_annualised_return.data,
            ten_year_annualised_return=form.ten_year_annualised_return.data,
            currency=Currency[form.currency.data],
        )
        investments.append(new_investment)
        save_encrypted_data()
        flash(
            f"Investment '{new_investment.investment_name}' added successfully!",
            "success",
        )
        return redirect(url_for("index"))
    return render_template("form_page.html", form=form, title="Add a New Investment")


@app.route("/edit/<int:investment_index>", methods=["GET", "POST"])
@require_unlock
def edit_investment(investment_index):
    """Handles editing an existing investment."""
    # Basic bounds checking to ensure the investment exists
    if not 0 <= investment_index < len(investments):
        flash("Investment not found.", "danger")
        return redirect(url_for("index"))

    investment_to_edit = investments[investment_index]
    form = InvestmentForm()

    if form.validate_on_submit():  # This is on a POST request
        # Update the existing object with data from the submitted form
        investment_to_edit.investment_name = form.investment_name.data
        investment_to_edit.ticker = form.ticker.data
        investment_to_edit.five_year_annualised_return = (
            form.five_year_annualised_return.data
        )
        investment_to_edit.ten_year_annualised_return = (
            form.ten_year_annualised_return.data
        )
        investment_to_edit.currency = Currency[form.currency.data]

        save_encrypted_data()
        flash(
            f"Investment '{investment_to_edit.investment_name}' updated successfully!",
            "success",
        )
        return redirect(url_for("index"))

    # For a GET request, pre-populate the form with the existing investment data
    form.process(data=investment_to_edit.__dict__)
    form.currency.data = (
        investment_to_edit.currency.name
    )  # Manually set enum for SelectField

    return render_template(
        "form_page.html", form=form, title=f"Edit {investment_to_edit.investment_name}"
    )


@app.route("/investments/<string:investment_name>/transactions")
@require_unlock
def view_transactions(investment_name):
    """Displays all transactions for a specific investment."""
    # Find the investment object to get its currency
    investment = next(
        (inv for inv in investments if inv.investment_name == investment_name), None
    )
    if not investment:
        flash(f"Investment '{investment_name}' not found.", "danger")
        return redirect(url_for("index"))

    transactions_for_investment = transactions_data.get(investment_name, [])

    # Sort transactions chronologically.
    # The sort key is the earliest date found on the transaction (buy, sell, or gain).
    # Transactions without any date are sorted to the end.
    def get_sort_key(transaction: Transaction) -> datetime.date:
        """Get the earliest date from a transaction to use as a sort key."""
        dates = [
            d
            for d in [
                transaction.buy_date,
                transaction.sell_date,
                transaction.gain_date,
            ]
            if d
        ]
        # A valid transaction should have a date, but this handles edge cases.
        return min(dates) if dates else datetime.date.max

    sorted_transactions = sorted(transactions_for_investment, key=get_sort_key)

    # Use the helper to get all calculated metrics
    metrics = _calculate_investment_metrics(investment, sorted_transactions)

    xirr_value = calculate_investment_xirr(
        sorted_transactions, metrics["current_value_for_xirr"], datetime.date.today()
    )

    return render_template(
        "transactions.html",
        investment=investment,
        transactions=sorted_transactions,
        xirr_value=xirr_value,
        # Pass totals and other metrics to the template
        total_buy_quantity=metrics["totals"]["total_buy_quantity"],
        total_buy_amount=metrics["totals"]["total_buy_amount"],
        total_sell_quantity=metrics["totals"]["total_sell_quantity"],
        total_sell_amount=metrics["totals"]["total_sell_amount"],
        total_gain_amount=metrics["totals"]["total_gain_amount"],
        total_gain_from_sale=metrics["totals"]["total_gain_from_sale"],
        net_buy_amount=metrics["totals"]["net_buy_amount"],
        current_value=metrics["current_value"],
        current_rate=metrics["current_rate"] if metrics["current_rate"] else 0,
    )


@app.route("/transactions/add", methods=["GET", "POST"])
@app.route(
    "/investments/<string:investment_name>/transactions/add", methods=["GET", "POST"]
)
@require_unlock
def add_transaction(investment_name=None):
    """Handles adding a new transaction."""
    if not investments:
        flash("You must add an investment before you can add a transaction.", "warning")
        return redirect(url_for("add_investment"))

    form = TransactionForm()
    form.investment_name.choices = [
        (inv.investment_name, inv.investment_name) for inv in investments
    ]

    if request.method == "GET" and investment_name:
        # Pre-select the current investment if coming from a specific investment page
        form.investment_name.data = investment_name

    if form.validate_on_submit():
        investment_name_from_form = form.investment_name.data

        # Calculate gain_from_sale if it's a sell transaction
        gain_from_sale = None
        if form.sell_quantity.data and form.sell_rate.data and form.buy_rate.data:
            gain_from_sale = (
                form.sell_rate.data - form.buy_rate.data
            ) * form.sell_quantity.data

        new_transaction = Transaction(
            buy_date=form.buy_date.data,
            buy_quantity=form.buy_quantity.data,
            buy_rate=form.buy_rate.data,
            description=form.description.data,
            sell_date=form.sell_date.data,
            sell_quantity=form.sell_quantity.data,
            sell_rate=form.sell_rate.data,
            gain_from_sale=gain_from_sale,
            gain_date=form.gain_date.data,
            gain_amount=form.gain_amount.data,
        )
        # Add transaction to the correct list in the dictionary
        transactions_data.setdefault(investment_name_from_form, []).append(
            new_transaction
        )
        save_encrypted_data()
        flash(f"Transaction added for {investment_name_from_form}!", "success")
        return redirect(
            url_for("view_transactions", investment_name=investment_name_from_form)
        )

    title = "Add Transaction"
    if investment_name:
        title = f"Add Transaction for {investment_name}"

    return render_template("form_page.html", form=form, title=title)


@app.route(
    "/investments/<string:investment_name>/transactions/edit/<int:transaction_index>",
    methods=["GET", "POST"],
)
@require_unlock
def edit_transaction(investment_name, transaction_index):
    """Handles editing an existing transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", "danger")
        return redirect(url_for("view_transactions", investment_name=investment_name))

    transaction_to_edit = transactions_for_investment[transaction_index]
    form = TransactionForm()
    form.investment_name.choices = [
        (inv.investment_name, inv.investment_name) for inv in investments
    ]

    if form.validate_on_submit():
        new_investment_name = form.investment_name.data

        # Calculate gain_from_sale if it's a sell transaction
        gain_from_sale = None
        if form.sell_quantity.data and form.sell_rate.data and form.buy_rate.data:
            gain_from_sale = (
                form.sell_rate.data - form.buy_rate.data
            ) * form.sell_quantity.data

        # Update the transaction object's data
        transaction_to_edit.buy_date = form.buy_date.data
        transaction_to_edit.buy_quantity = form.buy_quantity.data
        transaction_to_edit.buy_rate = form.buy_rate.data
        transaction_to_edit.description = form.description.data
        transaction_to_edit.sell_date = form.sell_date.data
        transaction_to_edit.sell_quantity = form.sell_quantity.data
        transaction_to_edit.sell_rate = form.sell_rate.data
        transaction_to_edit.gain_from_sale = gain_from_sale
        transaction_to_edit.gain_date = form.gain_date.data
        transaction_to_edit.gain_amount = form.gain_amount.data

        # If the investment was changed, move the transaction to the new list
        if new_investment_name != investment_name:
            # Remove from the old investment's transaction list
            transactions_data[investment_name].pop(transaction_index)
            # Add to the new investment's transaction list
            transactions_data.setdefault(new_investment_name, []).append(
                transaction_to_edit
            )

        save_encrypted_data()
        flash(f"Transaction updated for {new_investment_name}!", "success")
        return redirect(
            url_for("view_transactions", investment_name=new_investment_name)
        )

    # Pre-populate form for GET request
    form.process(data=transaction_to_edit.__dict__)
    form.investment_name.data = investment_name  # Set dropdown to original investment

    return render_template(
        "form_page.html", form=form, title=f"Edit Transaction for {investment_name}"
    )


@app.route(
    "/investments/<string:investment_name>/transactions/delete/<int:transaction_index>",
    methods=["POST"],
)
@require_unlock
def delete_transaction(investment_name, transaction_index):
    """Handles deleting a transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", "danger")
        return redirect(url_for("view_transactions", investment_name=investment_name))

    transactions_for_investment.pop(transaction_index)
    save_encrypted_data()
    flash(f"Transaction for {investment_name} deleted!", "info")
    return redirect(url_for("view_transactions", investment_name=investment_name))


@app.route("/expenses")
@require_unlock
def expenses_list():
    """Renders the list of expenses."""
    return render_template("expenses.html", expenses=expenses)


@app.route("/expenses/add", methods=["GET", "POST"])
@require_unlock
def add_expense():
    """Handles adding a new expense."""
    form = ExpenseForm()
    if form.validate_on_submit():
        new_expense = Expense(
            name=form.name.data,
            amount=form.amount.data,
            currency=Currency[form.currency.data],
            date=form.date.data,
            category=ExpenseCategory[form.category.data],
            is_recurring=form.is_recurring.data,
            recurrence_period=RecurrencePeriod[form.recurrence_period.data],
            end_date=form.end_date.data,
        )
        expenses.append(new_expense)
        save_encrypted_data()
        flash(f"Expense '{new_expense.name}' added successfully!", "success")
        return redirect(url_for("expenses_list"))
    return render_template("form_page.html", form=form, title="Add a New Expense")


@app.route("/expenses/edit/<int:expense_index>", methods=["GET", "POST"])
@require_unlock
def edit_expense(expense_index):
    """Handles editing an existing expense."""
    if not 0 <= expense_index < len(expenses):
        flash("Expense not found.", "danger")
        return redirect(url_for("expenses_list"))

    expense_to_edit = expenses[expense_index]
    form = ExpenseForm()

    if form.validate_on_submit():
        expense_to_edit.name = form.name.data
        expense_to_edit.amount = form.amount.data
        expense_to_edit.currency = Currency[form.currency.data]
        expense_to_edit.date = form.date.data
        expense_to_edit.category = ExpenseCategory[form.category.data]
        expense_to_edit.is_recurring = form.is_recurring.data
        expense_to_edit.recurrence_period = RecurrencePeriod[form.recurrence_period.data]
        expense_to_edit.end_date = form.end_date.data

        save_encrypted_data()
        flash(f"Expense '{expense_to_edit.name}' updated successfully!", "success")
        return redirect(url_for("expenses_list"))

    # Pre-populate form
    form.process(data=expense_to_edit.__dict__)
    form.currency.data = expense_to_edit.currency.name
    form.category.data = expense_to_edit.category.name
    form.recurrence_period.data = expense_to_edit.recurrence_period.name

    return render_template(
        "form_page.html", form=form, title=f"Edit {expense_to_edit.name}"
    )


@app.route("/expenses/delete/<int:expense_index>", methods=["POST"])
@require_unlock
def delete_expense(expense_index):
    """Handles deleting an expense."""
    if not 0 <= expense_index < len(expenses):
        flash("Expense not found.", "danger")
        return redirect(url_for("expenses_list"))

    expenses.pop(expense_index)
    save_encrypted_data()
    flash("Expense deleted!", "info")
    return redirect(url_for("expenses_list"))




@app.route("/retirement-projection")
@require_unlock
def retirement_projection():
    """Models retirement based on US market investments and future expenses."""
    # Calculate current US portfolio XIRR for default pre-retirement growth
    usd_ticker_cash_flows = []
    total_current_usd_ticker = Decimal(0)
    for inv in investments:
        if inv.currency == Currency.USD and inv.ticker:
            transactions = transactions_data.get(inv.investment_name, [])
            metrics = _calculate_investment_metrics(inv, transactions)
            if metrics["current_value"] is not None:
                total_current_usd_ticker += metrics["current_value"]
                usd_ticker_cash_flows.extend(generate_cash_flows_from_transactions(transactions))
    
    if total_current_usd_ticker > 0:
        usd_ticker_cash_flows.append((datetime.date.today(), total_current_usd_ticker))
    
    us_xirr = calculate_xirr_from_cash_flows(usd_ticker_cash_flows)
    # Default to 7% if XIRR calculation is not possible
    us_xirr_dec = (us_xirr / Decimal("100")) if us_xirr is not None else Decimal("0.07")

    # Get user inputs from query params with defaults
    pre_growth_param = request.args.get('pre_growth')
    pre_growth = Decimal(pre_growth_param) / 100 if pre_growth_param else us_xirr_dec
    
    post_growth_param = request.args.get('post_growth')
    post_growth = Decimal(post_growth_param) / 100 if post_growth_param else Decimal("0.065")
    
    swr_param = request.args.get('swr')
    swr = Decimal(swr_param) / 100 if swr_param else Decimal("0.04")
    
    # Emergency medical corpus - ₹50L
    medical_corpus_inr = Decimal("5000000")
    
    # Currency rates
    rates = get_exchange_rates("USD")
    usd_to_inr = rates.get("INR", Decimal("83"))
    eur_to_usd = get_rate("EUR", "USD") or Decimal("1.08")
    gbp_to_usd = get_rate("GBP", "USD") or Decimal("1.26")

    medical_corpus_usd = medical_corpus_inr / usd_to_inr

    # Current US Market Portfolio
    total_current_usd = Decimal(0)
    for inv in investments:
        if inv.currency == Currency.USD:
            metrics = _calculate_investment_metrics(inv, transactions_data.get(inv.investment_name, []))
            if metrics["current_value"]:
                total_current_usd += metrics["current_value"]

    # Calculate Recurring Lifestyle Expense from JSON (items with category=RETIREMENT)
    retirement_lifestyle_expenses = [e for e in expenses if e.category == ExpenseCategory.RETIREMENT]
    retirement_yearly_outflow_usd = Decimal("0")
    
    for exp in retirement_lifestyle_expenses:
        amt_usd = exp.amount
        if exp.currency == Currency.INR: amt_usd /= usd_to_inr
        elif exp.currency == Currency.EUR: amt_usd *= eur_to_usd
        elif exp.currency == Currency.GBP: amt_usd *= gbp_to_usd
        
        if exp.recurrence_period == RecurrencePeriod.MONTHLY:
            retirement_yearly_outflow_usd += amt_usd * 12
        elif exp.recurrence_period == RecurrencePeriod.YEARLY:
            retirement_yearly_outflow_usd += amt_usd
        elif exp.recurrence_period == RecurrencePeriod.EVERY_5_YEARS:
            retirement_yearly_outflow_usd += amt_usd / 5
        else: # One-time
            retirement_yearly_outflow_usd += amt_usd

    # If no retirement expenses defined yet, use the previous defaults as a placeholder
    if not retirement_lifestyle_expenses:
        yearly_lifestyle_inr = (Decimal("180000") * 12) + Decimal("70000")
        trips_usd = Decimal("3000") + (Decimal("2500") * eur_to_usd) + (Decimal("300000") / usd_to_inr)
        retirement_yearly_outflow_usd = (yearly_lifestyle_inr / usd_to_inr) + trips_usd

    required_corpus_usd = (retirement_yearly_outflow_usd / swr) + medical_corpus_usd

    # Projection (Monthly for precision)
    projection_months = 40 * 12
    start_date = datetime.date.today().replace(day=1)
    yearly_data_map = {}
    active_portfolio = total_current_usd
    retirement_date = None
    
    # Calculate monthly rates (Linear division to match SWR withdrawal logic)
    pre_growth_monthly = pre_growth / 12
    post_growth_monthly = post_growth / 12

    for m_idx in range(projection_months):
        current_m_date = start_date + relativedelta(months=m_idx)
        year_val = current_m_date.year
        
        if year_val not in yearly_data_map:
            yearly_data_map[year_val] = {"portfolio": Decimal(0), "outflow": Decimal(0), "required_corpus": required_corpus_usd}

        # Monthly Outflow (Pre-retirement education/other)
        monthly_outflow_usd = Decimal(0)
        non_retirement_exps = [e for e in expenses if e.category != ExpenseCategory.RETIREMENT]
        
        for exp in non_retirement_exps:
            # Check if this expense applies to this specific month
            exp_occurs = False
            if not exp.is_recurring:
                if exp.date.year == current_m_date.year and exp.date.month == current_m_date.month:
                    exp_occurs = True
            elif exp.recurrence_period == RecurrencePeriod.MONTHLY:
                if exp.date <= current_m_date and (not exp.end_date or current_m_date <= exp.end_date):
                    exp_occurs = True
            elif exp.recurrence_period == RecurrencePeriod.YEARLY:
                if exp.date.month == current_m_date.month and exp.date <= current_m_date and (not exp.end_date or current_m_date <= exp.end_date):
                    exp_occurs = True
            elif exp.recurrence_period == RecurrencePeriod.EVERY_5_YEARS:
                # Occurs every 60 months starting from exp.date
                diff = (current_m_date.year - exp.date.year) * 12 + (current_m_date.month - exp.date.month)
                if diff >= 0 and diff % 60 == 0 and (not exp.end_date or current_m_date <= exp.end_date):
                    exp_occurs = True
            
            if exp_occurs:
                amt_usd = exp.amount
                if exp.currency == Currency.INR: amt_usd /= usd_to_inr
                elif exp.currency == Currency.EUR: amt_usd *= eur_to_usd
                elif exp.currency == Currency.GBP: amt_usd *= gbp_to_usd
                monthly_outflow_usd += amt_usd

        # Growth & Retirement Check
        is_retired = (retirement_date is not None and current_m_date >= retirement_date)
        
        # If not already retired, check if we hit the target THIS month
        if retirement_date is None and active_portfolio >= required_corpus_usd:
            retirement_date = current_m_date
            is_retired = True

        growth_rate = post_growth_monthly if is_retired else pre_growth_monthly
        growth = active_portfolio * growth_rate
        
        effective_monthly_outflow = monthly_outflow_usd
        if is_retired:
            effective_monthly_outflow += (retirement_yearly_outflow_usd / 12)

        active_portfolio = active_portfolio + growth - effective_monthly_outflow
        
        # Aggregate to yearly for the UI
        yearly_data_map[year_val]["portfolio"] = active_portfolio # End of year/latest month balance
        yearly_data_map[year_val]["outflow"] += effective_monthly_outflow
        yearly_data_map[year_val]["can_retire"] = active_portfolio >= required_corpus_usd

    # Convert map to sorted list for template
    yearly_data = []
    for year in sorted(yearly_data_map.keys()):
        data = yearly_data_map[year]
        yearly_data.append({
            "year": year,
            "portfolio": data["portfolio"],
            "outflow": data["outflow"],
            "can_retire": data["can_retire"],
            "required_corpus": data["required_corpus"]
        })

    retirement_date_str = retirement_date.strftime("%B %Y") if retirement_date else None

    return render_template(
        "retirement_projection.html",
        yearly_data=yearly_data,
        current_portfolio=total_current_usd,
        required_corpus=required_corpus_usd,
        retirement_date=retirement_date_str,
        medical_corpus_usd=medical_corpus_usd,
        lifestyle_expense_usd=retirement_yearly_outflow_usd,
        retirement_lifestyle_expenses=retirement_lifestyle_expenses,
        swr=swr,
        pre_growth=pre_growth,
        post_growth=post_growth,
        us_xirr=us_xirr,
        Currency=Currency
    )


@app.route("/reload")
@require_unlock
def reload_data():
    """Reloads all data from the JSON files on disk."""
    global investments, transactions_data
    investments = load_investments_from_json(DATA_FILE)
    transactions_data = load_transactions_from_json(TRANSACTIONS_FILE)
    flash("Data reloaded successfully from disk.", "success")
    return redirect(url_for("index"))


@app.route("/portfolio-graph")
@require_unlock
def portfolio_graph():
    """Displays a graph showing portfolio growth over time."""
    try:
        # Get date range from query parameter
        range_param = request.args.get('range', 'all')
        end_date = datetime.date.today()
        
        if range_param == '3m':
            start_date = end_date - datetime.timedelta(days=90)
        elif range_param == '6m':
            start_date = end_date - datetime.timedelta(days=180)
        elif range_param == '1y':
            start_date = end_date - datetime.timedelta(days=365)
        else:
            start_date = None  # All time
        
        from api_calls import get_current_rate
        
        # Get current rates for all investments
        current_rates = {}
        for inv in investments:
            if inv.ticker:
                rate = get_current_rate(inv.ticker)
                if rate:
                    current_rates[inv.investment_name] = rate
        
        # Get USD to INR rate
        usd_to_inr_rate = get_usd_to_inr_rate()
        if not usd_to_inr_rate:
            usd_to_inr_rate = Decimal('83')
        
        # Generate portfolio timeline
        snapshots = generate_portfolio_timeline(investments, transactions_data, current_rates, start_date, end_date)
        
        if not snapshots:
            flash("No portfolio data available for graphing.", "warning")
            return redirect(url_for("index"))
        
        # Prepare data for Chart.js
        chart_data = prepare_chart_data(snapshots, usd_to_inr_rate)
        
        return render_template("portfolio_graph.html", chart_data=chart_data, selected_range=range_param)
    except Exception as e:
        flash(f"Error generating graph: {str(e)}", "danger")
        return redirect(url_for("index"))
        
        if not snapshots:
            flash("No portfolio data available for graphing.", "warning")
            return redirect(url_for("index"))
        
        # Prepare data for Chart.js
        chart_data = prepare_chart_data(snapshots, usd_to_inr_rate)
        
        return render_template("portfolio_graph.html", chart_data=chart_data)
    except Exception as e:
        flash(f"Error generating portfolio graph: {str(e)}", "danger")
        return redirect(url_for("index"))


if __name__ == "__main__":
    # Create the data directory if it doesn't exist
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    #touch investment.json and transaction.json if they don't exist
    if not os.path.exists(DATA_FILE):
        open(DATA_FILE, 'a').close()
    if not os.path.exists(TRANSACTIONS_FILE):
        app.run(debug=True)
