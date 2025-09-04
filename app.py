import os
from flask import Flask, render_template, redirect, url_for

from flask_bootstrap import Bootstrap5
from model import (
    Investment,
    Currency,
    load_investments_from_json,
    save_investments_to_json,
)
from form import InvestmentForm

app = Flask(__name__)
# Flask-WTF requires a secret key for CSRF protection.
# It's good practice to set this from an environment variable.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-default-secret-key-for-dev')
bootstrap = Bootstrap5(app)

DATA_FILE = "investments.json"

# This list will hold our investment objects in memory.
# It's loaded from the JSON file when the application starts.
investments = load_investments_from_json(DATA_FILE)


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
        return redirect(url_for('index'))
    return render_template('add_investment.html', form=form, title="Add a New Investment")


@app.route('/edit/<int:investment_index>', methods=['GET', 'POST'])
def edit_investment(investment_index):
    """Handles editing an existing investment."""
    # Basic bounds checking to ensure the investment exists
    if not 0 <= investment_index < len(investments):
        return "Investment not found", 404

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
        return redirect(url_for('index'))

    # For a GET request, pre-populate the form with the existing investment data
    form.process(data=investment_to_edit.__dict__)
    form.currency.data = investment_to_edit.currency.name  # Manually set enum for SelectField

    return render_template('add_investment.html', form=form, title=f"Edit {investment_to_edit.investment_name}")

if __name__ == '__main__':
    app.run(debug=True)
