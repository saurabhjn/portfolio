import os
from typing import Optional
from flask import Flask, render_template, redirect, url_for, flash, request
import datetime
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
from api_calls import (
    get_current_rate,
    get_usd_to_inr_rate,
    get_historical_usd_to_inr_rate,
)
from form import InvestmentForm, TransactionForm
from xirr import (
    calculate_investment_xirr,
    calculate_xirr_from_cash_flows,
    generate_cash_flows_from_transactions,
)

app = Flask(__name__)
# Flask-WTF requires a secret key for CSRF protection.
# It's good practice to set this from an environment variable.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "a-default-secret-key-for-dev")
bootstrap = Bootstrap5(app)


DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "investments.json")
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")

# This list will hold our investment objects in memory.
# It's loaded from the JSON file when the application starts.
investments = load_investments_from_json(DATA_FILE)

# This dictionary will hold our transaction objects in memory.
# It's loaded from the JSON file when the application starts.
transactions_data = load_transactions_from_json(TRANSACTIONS_FILE)


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
    (USD or INR), rounding down to 0 decimal places.
    """
    if value is None:
        return None
    # Ensure value is a Decimal for accurate quantization
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    # Round down to 0 decimal places
    floored_value = value.quantize(Decimal("1"), rounding=ROUND_DOWN)

    if currency.value == "USD":
        return f"{floored_value:,.0f}"
    elif currency.value == "INR":
        return _format_inr(f"{floored_value:.2f}").split(".")[0]
    else:
        return f"{floored_value:,.0f}"


@app.route("/")
def index():
    """Renders the main page with a list of all investments."""
    portfolio_data = []
    total_purchase_value_usd = Decimal(0)
    total_purchase_value_inr = Decimal(0)
    total_current_value_usd = Decimal(0)
    total_current_value_inr = Decimal(0)

    # New variables for ticker-based USD investments
    total_purchase_usd_ticker = Decimal(0)
    total_current_usd_ticker = Decimal(0)
    total_gain_usd_ticker = Decimal(0)
    usd_ticker_cash_flows = []

    # New variables for ticker-based INR investments
    total_purchase_inr_ticker = Decimal(0)
    total_current_inr_ticker = Decimal(0)
    total_gain_inr_ticker = Decimal(0)
    inr_ticker_cash_flows = []

    for inv in investments:
        transactions_for_inv = transactions_data.get(inv.investment_name, [])
        totals = calculate_transaction_totals(transactions_for_inv)
        remaining_quantity = (
            totals["total_buy_quantity"] - totals["total_sell_quantity"]
        )
        purchase_value = totals["net_buy_amount"]

        # Fetch the current market rate for the investment's ticker
        current_rate = get_current_rate(inv.ticker) if inv.ticker else None

        if current_rate is not None:
            current_value = remaining_quantity * current_rate
            total_gain_amount = totals.get("total_gain_amount", Decimal(0))
            total_gain_from_sale = totals.get("total_gain_from_sale", Decimal(0))
            gain = (
                current_value - purchase_value + total_gain_amount + total_gain_from_sale
            )
            current_value_for_xirr = current_value
        else:
            # For investments without a ticker or if rate fetch fails,
            # current value is the net of cash flows from transactions.
            # This should match the logic in view_transactions.
            total_gain_amount = totals.get("total_gain_amount", Decimal(0))
            total_sell_amount = totals.get("total_sell_amount", Decimal(0))
            current_value = purchase_value + total_gain_amount - total_sell_amount
            gain = total_gain_amount + total_gain_from_sale
            current_value_for_xirr = current_value - total_gain_amount


        # Accumulate totals based on currency
        # Calculate Gain for the investment
        if inv.currency == Currency.USD:
            total_purchase_value_usd += purchase_value
            if current_value is not None:
                total_current_value_usd += current_value
        elif inv.currency == Currency.INR:
            total_purchase_value_inr += purchase_value
            if current_value is not None:
                total_current_value_inr += current_value

        # If it's a USD investment with a ticker, add to the market totals
        if inv.currency == Currency.USD and inv.ticker:
            total_purchase_usd_ticker += purchase_value
            if current_value is not None:
                total_current_usd_ticker += current_value
            total_gain_usd_ticker += gain

            usd_ticker_cash_flows.extend(
                generate_cash_flows_from_transactions(transactions_for_inv)
            )

        # If it's an INR investment with a ticker, add to the market totals
        if inv.currency == Currency.INR and inv.ticker:
            total_purchase_inr_ticker += purchase_value
            if current_value is not None:
                total_current_inr_ticker += current_value
            total_gain_inr_ticker += gain

            inr_ticker_cash_flows.extend(
                generate_cash_flows_from_transactions(transactions_for_inv)
            )

        # Calculate XIRR for the investment
        xirr_value = calculate_investment_xirr(
            transactions_for_inv, current_value_for_xirr, datetime.date.today()
        )

        portfolio_data.append(
            {
                "investment": inv,
                "total_quantity": remaining_quantity,
                "current_rate": current_rate,
                "purchase_value": purchase_value,
                "current_value": current_value,
                "xirr_value": xirr_value,
                "gain": gain,
            }
        )

    # Calculate combined XIRR for all ticker-based USD investments
    if total_current_usd_ticker > 0:
        usd_ticker_cash_flows.append(
            (datetime.date.today(), total_current_usd_ticker)
        )
    usd_ticker_xirr = calculate_xirr_from_cash_flows(usd_ticker_cash_flows)

    # Calculate combined XIRR for all ticker-based INR investments
    if total_current_inr_ticker > 0:
        inr_ticker_cash_flows.append(
            (datetime.date.today(), total_current_inr_ticker)
        )
    inr_ticker_xirr = calculate_xirr_from_cash_flows(inr_ticker_cash_flows)

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
        total_purchase_inr_ticker_str=total_purchase_inr_ticker_str,
        total_current_inr_ticker_str=total_current_inr_ticker_str,
        total_gain_inr_ticker_str=total_gain_inr_ticker_str,
        inr_ticker_xirr=inr_ticker_xirr,
        grand_total_in_inr_str=grand_total_in_inr_str,
        grand_total_purchase_in_inr_str=grand_total_purchase_in_inr_str,
        grand_total_gain_in_inr_str=grand_total_gain_in_inr_str,
        overall_xirr=overall_xirr,
    )


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

    transactions_for_investment.sort(key=get_sort_key)

    # Calculate summary totals
    totals = calculate_transaction_totals(transactions_for_investment)

    # Calculate Current Value and XIRR
    current_rate = get_current_rate(investment.ticker) if investment.ticker else None
    remaining_quantity = totals.get("total_buy_quantity", Decimal(0)) - totals.get(
        "total_sell_quantity", Decimal(0)
    )

    if current_rate is not None:
        current_value = remaining_quantity * current_rate
        current_value_for_xirr = current_value
    else:
        # For investments without a ticker or if rate fetch fails
        current_value = (
            totals.get("total_buy_amount", Decimal(0))
            + totals.get("total_gain_amount", Decimal(0))
            - totals.get("total_sell_amount", Decimal(0))
        )
        current_value_for_xirr = current_value - totals.get("total_gain_amount", Decimal(0))


    xirr_value = calculate_investment_xirr(
        transactions_for_investment, current_value_for_xirr, datetime.date.today()
    )

    return render_template(
        "transactions.html",
        investment=investment,
        transactions=transactions_for_investment,
        xirr_value=xirr_value,
        **totals,
        current_value=current_value,
        current_rate=current_rate,
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
        save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
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

        save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
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
def delete_transaction(investment_name, transaction_index):
    """Handles deleting a transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", "danger")
        return redirect(url_for("view_transactions", investment_name=investment_name))

    transactions_for_investment.pop(transaction_index)
    save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
    flash(f"Transaction for {investment_name} deleted!", "info")
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
