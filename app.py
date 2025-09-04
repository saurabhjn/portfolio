import os
from typing import Optional
from flask import Flask, render_template, redirect, url_for, flash, request
import requests
import datetime
import json

from decimal import Decimal, ROUND_DOWN
from flask_bootstrap import Bootstrap5
from model import (
    Investment,
    Currency,
    Transaction,
    load_investments_from_json,
    save_investments_to_json,
    load_transactions_from_json,
    save_transactions_to_json,
    calculate_transaction_totals,
)
from form import InvestmentForm, TransactionForm

app = Flask(__name__)
# Flask-WTF requires a secret key for CSRF protection.
# It's good practice to set this from an environment variable.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "a-default-secret-key-for-dev")
bootstrap = Bootstrap5(app)


def load_api_key(config_path: str) -> Optional[str]:
    """Loads the Alpha Vantage API key from a JSON config file."""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            key = config.get("ALPHA_VANTAGE_API_KEY")
            if not key:
                print(f"Warning: 'ALPHA_VANTAGE_API_KEY' not found in {config_path}")
            return key
    except FileNotFoundError:
        print(f"Warning: Config file not found at {config_path}")
        return None
    except json.JSONDecodeError:
        print(f"Warning: Could not decode JSON from {config_path}")
        return None


def save_rate_cache(cache_path: str, cache: dict):
    """Saves the rate cache to a JSON file."""
    serializable_cache = {}
    for ticker, (timestamp, rate) in cache.items():
        serializable_cache[ticker] = {
            "timestamp": timestamp.isoformat(),
            "rate": str(rate),
        }
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(serializable_cache, f, indent=4)


def load_rate_cache(cache_path: str) -> dict:
    """Loads the rate cache from a JSON file."""
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        reconstructed_cache = {}
        for ticker, value in data.items():
            timestamp = datetime.datetime.fromisoformat(value["timestamp"])
            rate = Decimal(value["rate"])
            reconstructed_cache[ticker] = (timestamp, rate)
        return reconstructed_cache
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return {}


DATA_DIR = "data"
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DATA_FILE = os.path.join(DATA_DIR, "investments.json")
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")
RATE_CACHE_FILE = os.path.join(DATA_DIR, "rate_cache.json")

# This list will hold our investment objects in memory.
# It's loaded from the JSON file when the application starts.
investments = load_investments_from_json(DATA_FILE)

# This dictionary will hold our transaction objects in memory.
# It's loaded from the JSON file when the application starts.
transactions_data = load_transactions_from_json(TRANSACTIONS_FILE)

ALPHA_VANTAGE_API_KEY = load_api_key(CONFIG_FILE)

# In-memory cache for stock rates: {ticker: (timestamp, rate)}
rate_cache = load_rate_cache(RATE_CACHE_FILE)


def get_current_rate(ticker: str) -> Optional[Decimal]:
    """
    Fetches the latest closing price for a given ticker from Alpha Vantage,
    with a 10-minute cache.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print(f"API key is not configured. Skipping API call for {ticker}.")
        return None

    # Skip API calls for ticker formats not supported by Alpha Vantage
    if ticker.startswith("NSE:") or ticker.startswith("MUTF_IN:"):
        print(f"Skipping API call for unsupported ticker format: {ticker}")
        return None

    now = datetime.datetime.now()

    # Check cache first
    if ticker in rate_cache:
        cached_time, cached_rate = rate_cache[ticker]
        if now - cached_time < datetime.timedelta(minutes=30):
            print(f"Cache hit for {ticker}. Returning cached rate.")
            return cached_rate

    print(f"Cache miss for {ticker}. Fetching from API.")
    params = {
        "function": "TIME_SERIES_INTRADAY",
        "symbol": ticker,
        "interval": "5min",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    try:
        response = requests.get(
            "https://www.alphavantage.co/query", params=params, timeout=10
        )
        response.raise_for_status()  # Raise an exception for bad status codes
        data = response.json()

        # The API can return a "Note" on high frequency usage, which we treat as an error.
        if "Error Message" in data or "Note" in data:
            print(
                f"API Error for {ticker}: {data.get('Error Message') or data.get('Note')}"
            )
            return None

        meta_data = data.get("Meta Data")
        time_series = data.get("Time Series (5min)")

        if not meta_data or not time_series:
            print(f"Incomplete data for {ticker}: {data}")
            return None

        last_refreshed_key = meta_data.get("3. Last Refreshed")
        if not last_refreshed_key:
            print(f"No 'Last Refreshed' key for {ticker}")
            return None

        latest_data = time_series.get(last_refreshed_key)
        if not latest_data:
            print(f"No data for timestamp {last_refreshed_key} for {ticker}")
            return None

        close_price_str = latest_data.get("4. close")
        if not close_price_str:
            print(f"No 'close' price for timestamp {last_refreshed_key} for {ticker}")
            return None

        rate = Decimal(close_price_str)
        # Store the newly fetched rate in the cache
        rate_cache[ticker] = (now, rate)
        save_rate_cache(RATE_CACHE_FILE, rate_cache)

        return rate

    except requests.exceptions.RequestException as e:
        print(f"Request failed for {ticker}: {e}")
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Failed to parse data for {ticker}: {e}")
        return None

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
    (USD or INR), rounding down to 2 decimal places.
    """
    if value is None:
        return None
    # Ensure value is a Decimal for accurate quantization
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    # Round down to 2 decimal places
    floored_value = value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    if currency.value == "USD":
        return f"{floored_value:,.2f}"
    elif currency.value == "INR":
        return _format_inr(f"{floored_value:.2f}")
    else:
        return f"{floored_value:.2f}"


@app.template_filter("format_quantity")
def format_quantity_filter(value):
    """
    Formats a number as an integer with comma separators.
    """
    if value is None:
        return None
    try:
        # First, convert to integer to truncate decimals
        int_value = int(value)
        # Then, format with commas
        return f"{int_value:,}"
    except (ValueError, TypeError):
        return value


@app.route("/")
def index():
    """Renders the main page with a list of all investments."""
    portfolio_data = []
    for inv in investments:
        transactions_for_inv = transactions_data.get(inv.investment_name, [])
        totals = calculate_transaction_totals(transactions_for_inv)
        total_quantity = totals["total_buy_quantity"]
        purchase_value = totals["total_buy_amount"]

        # Fetch the current market rate for the investment's ticker
        current_rate = get_current_rate(inv.ticker)
        current_value = (
            total_quantity * current_rate if current_rate is not None else None
        )

        portfolio_data.append(
            {
                "investment": inv,
                "total_quantity": total_quantity,
                "current_rate": current_rate,
                "purchase_value": purchase_value,
                "current_value": current_value,
            }
        )

    return render_template("index.html", portfolio_data=portfolio_data)


@app.route("/add", methods=["GET", "POST"])
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
        save_investments_to_json(DATA_FILE, investments)
        flash(
            f"Investment '{new_investment.investment_name}' added successfully!",
            "success",
        )
        return redirect(url_for("index"))
    return render_template("form_page.html", form=form, title="Add a New Investment")


@app.route("/edit/<int:investment_index>", methods=["GET", "POST"])
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

        save_investments_to_json(DATA_FILE, investments)
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

    # Calculate totals using the new model function
    totals = calculate_transaction_totals(transactions_for_investment)

    return render_template(
        "transactions.html",
        investment=investment,
        transactions=transactions_for_investment,
        **totals,
    )


@app.route("/transactions/add", methods=["GET", "POST"])
@app.route(
    "/investments/<string:investment_name>/transactions/add", methods=["GET", "POST"]
)
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
        new_transaction = Transaction(
            investment_name=form.investment_name.data,
            buy_date=form.buy_date.data,
            buy_quantity=form.buy_quantity.data,
            buy_rate=form.buy_rate.data,
            description=form.description.data,
            sell_date=form.sell_date.data,
            sell_quantity=form.sell_quantity.data,
            sell_rate=form.sell_rate.data,
            gain_from_sale=form.gain_from_sale.data,
        )
        # Add transaction to the correct list in the dictionary
        transactions_data.setdefault(new_transaction.investment_name, []).append(
            new_transaction
        )
        save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
        flash(f"Transaction added for {new_transaction.investment_name}!", "success")
        return redirect(
            url_for(
                "view_transactions", investment_name=new_transaction.investment_name
            )
        )

    title = "Add Transaction"
    if investment_name:
        title = f"Add Transaction for {investment_name}"

    return render_template("form_page.html", form=form, title=title)


@app.route(
    "/investments/<string:investment_name>/transactions/edit/<int:transaction_index>",
    methods=["GET", "POST"],
)
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
        # Update the transaction object
        transaction_to_edit.investment_name = form.investment_name.data
        transaction_to_edit.buy_date = form.buy_date.data
        transaction_to_edit.buy_quantity = form.buy_quantity.data
        transaction_to_edit.buy_rate = form.buy_rate.data
        transaction_to_edit.description = form.description.data
        transaction_to_edit.sell_date = form.sell_date.data
        transaction_to_edit.sell_quantity = form.sell_quantity.data
        transaction_to_edit.sell_rate = form.sell_rate.data
        transaction_to_edit.gain_from_sale = form.gain_from_sale.data

        save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
        flash(
            f"Transaction updated for {transaction_to_edit.investment_name}!", "success"
        )
        return redirect(
            url_for(
                "view_transactions", investment_name=transaction_to_edit.investment_name
            )
        )

    # Pre-populate form for GET request
    form.process(data=transaction_to_edit.__dict__)
    form.investment_name.data = transaction_to_edit.investment_name  # Set dropdown

    return render_template(
        "form_page.html", form=form, title=f"Edit Transaction for {investment_name}"
    )


@app.route(
    "/investments/<string:investment_name>/transactions/delete/<int:transaction_index>",
    methods=["POST"],
)
def delete_transaction(investment_name, transaction_index):
    """Handles deleting a transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", "danger")
        return redirect(url_for("view_transactions", investment_name=investment_name))

    deleted_transaction = transactions_for_investment.pop(transaction_index)
    save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
    flash(f"Transaction for {deleted_transaction.investment_name} deleted!", "info")
    return redirect(url_for("view_transactions", investment_name=investment_name))


@app.route("/reload")
def reload_data():
    """Reloads all data from the JSON files on disk."""
    global investments, transactions_data
    investments = load_investments_from_json(DATA_FILE)
    transactions_data = load_transactions_from_json(TRANSACTIONS_FILE)
    flash("Data reloaded successfully from disk.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
