import os
from flask import Flask, render_template, redirect, url_for, flash, request
import datetime

from flask_bootstrap import Bootstrap5
from model import (
    Investment,
    Currency,
    Transaction,
    load_investments_from_json,
    save_investments_to_json,
    load_transactions_from_json,
    save_transactions_to_json,
)
from form import InvestmentForm, TransactionForm

app = Flask(__name__)
# Flask-WTF requires a secret key for CSRF protection.
# It's good practice to set this from an environment variable.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-default-secret-key-for-dev')
bootstrap = Bootstrap5(app)

DATA_FILE = "investments.json"
TRANSACTIONS_FILE = "transactions.json"

# This list will hold our investment objects in memory.
# It's loaded from the JSON file when the application starts.
investments = load_investments_from_json(DATA_FILE)

# This dictionary will hold our transaction objects in memory.
# It's loaded from the JSON file when the application starts.
transactions_data = load_transactions_from_json(TRANSACTIONS_FILE)


@app.route('/')
def index():
    """Renders the main page with a list of all investments."""
    return render_template('index.html', investments=investments)


@app.route('/add', methods=['GET', 'POST'])
def add_investment():
    """Handles adding a new investment via a web form."""
    form = InvestmentForm()
    if form.validate_on_submit():
        new_investment = Investment(
            investment_name=form.investment_name.data,
            ticker=form.ticker.data,
            five_year_annualised_return=form.five_year_annualised_return.data,
            ten_year_annualised_return=form.ten_year_annualised_return.data,
            currency=Currency[form.currency.data]
        )
        investments.append(new_investment)
        save_investments_to_json(DATA_FILE, investments)
        flash(f"Investment '{new_investment.investment_name}' added successfully!", 'success')
        return redirect(url_for('index'))
    return render_template('form_page.html', form=form, title="Add a New Investment")


@app.route('/edit/<int:investment_index>', methods=['GET', 'POST'])
def edit_investment(investment_index):
    """Handles editing an existing investment."""
    # Basic bounds checking to ensure the investment exists
    if not 0 <= investment_index < len(investments):
        flash("Investment not found.", 'danger')
        return redirect(url_for('index'))

    investment_to_edit = investments[investment_index]
    form = InvestmentForm()

    if form.validate_on_submit():  # This is on a POST request
        # Update the existing object with data from the submitted form
        investment_to_edit.investment_name = form.investment_name.data
        investment_to_edit.ticker = form.ticker.data
        investment_to_edit.five_year_annualised_return = form.five_year_annualised_return.data
        investment_to_edit.ten_year_annualised_return = form.ten_year_annualised_return.data
        investment_to_edit.currency = Currency[form.currency.data]

        save_investments_to_json(DATA_FILE, investments)
        flash(f"Investment '{investment_to_edit.investment_name}' updated successfully!", 'success')
        return redirect(url_for('index'))

    # For a GET request, pre-populate the form with the existing investment data
    form.process(data=investment_to_edit.__dict__)
    form.currency.data = investment_to_edit.currency.name  # Manually set enum for SelectField

    return render_template('form_page.html', form=form, title=f"Edit {investment_to_edit.investment_name}")


@app.route('/investments/<string:investment_name>/transactions')
def view_transactions(investment_name):
    """Displays all transactions for a specific investment."""
    # Check if the investment exists
    if not any(inv.investment_name == investment_name for inv in investments):
        flash(f"Investment '{investment_name}' not found.", 'danger')
        return redirect(url_for('index'))

    transactions_for_investment = transactions_data.get(investment_name, [])
    return render_template(
        'transactions.html',
        investment_name=investment_name,
        transactions=transactions_for_investment
    )


@app.route('/transactions/add', methods=['GET', 'POST'])
@app.route('/investments/<string:investment_name>/transactions/add', methods=['GET', 'POST'])
def add_transaction(investment_name=None):
    """Handles adding a new transaction."""
    if not investments:
        flash("You must add an investment before you can add a transaction.", "warning")
        return redirect(url_for('add_investment'))

    form = TransactionForm()
    form.investment_name.choices = [(inv.investment_name, inv.investment_name) for inv in investments]

    if request.method == 'GET' and investment_name:
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
            gain_from_sale=form.gain_from_sale.data
        )
        # Add transaction to the correct list in the dictionary
        transactions_data.setdefault(new_transaction.investment_name, []).append(new_transaction)
        save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
        flash(f"Transaction added for {new_transaction.investment_name}!", 'success')
        return redirect(url_for('view_transactions', investment_name=new_transaction.investment_name))

    title = "Add Transaction"
    if investment_name:
        title = f"Add Transaction for {investment_name}"

    return render_template('form_page.html', form=form, title=title)


@app.route('/investments/<string:investment_name>/transactions/edit/<int:transaction_index>', methods=['GET', 'POST'])
def edit_transaction(investment_name, transaction_index):
    """Handles editing an existing transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", 'danger')
        return redirect(url_for('view_transactions', investment_name=investment_name))

    transaction_to_edit = transactions_for_investment[transaction_index]
    form = TransactionForm()
    form.investment_name.choices = [(inv.investment_name, inv.investment_name) for inv in investments]

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
        flash(f"Transaction updated for {transaction_to_edit.investment_name}!", 'success')
        return redirect(url_for('view_transactions', investment_name=transaction_to_edit.investment_name))

    # Pre-populate form for GET request
    form.process(data=transaction_to_edit.__dict__)
    form.investment_name.data = transaction_to_edit.investment_name # Set dropdown

    return render_template('form_page.html', form=form, title=f"Edit Transaction for {investment_name}")


@app.route('/investments/<string:investment_name>/transactions/delete/<int:transaction_index>', methods=['POST'])
def delete_transaction(investment_name, transaction_index):
    """Handles deleting a transaction."""
    transactions_for_investment = transactions_data.get(investment_name, [])
    if not 0 <= transaction_index < len(transactions_for_investment):
        flash("Transaction not found.", 'danger')
        return redirect(url_for('view_transactions', investment_name=investment_name))

    deleted_transaction = transactions_for_investment.pop(transaction_index)
    save_transactions_to_json(TRANSACTIONS_FILE, transactions_data)
    flash(f"Transaction for {deleted_transaction.investment_name} deleted!", 'info')
    return redirect(url_for('view_transactions', investment_name=investment_name))

if __name__ == '__main__':
    app.run(debug=True)
