from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SelectField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.fields.datetime import DateField

from model import Currency


class InvestmentForm(FlaskForm):
    """Form for adding or editing an investment."""

    investment_name = StringField(
        "Investment Name", validators=[DataRequired(), Length(min=2, max=100)]
    )
    ticker = StringField(
        "Ticker Symbol", validators=[DataRequired(), Length(min=1, max=50)]
    )
    five_year_annualised_return = DecimalField(
        "5-Year Annualised Return (%)", validators=[Optional()], places=2
    )
    ten_year_annualised_return = DecimalField(
        "10-Year Annualised Return (%)", validators=[Optional()], places=2
    )
    currency = SelectField(
        "Currency",
        # The choices are tuples of (value, label)
        choices=[(c.name, c.value) for c in Currency],
        validators=[DataRequired()],
    )
    submit = SubmitField("Save Investment")


class TransactionForm(FlaskForm):
    """Form for adding or editing a transaction."""

    investment_name = SelectField("Investment", validators=[DataRequired()])
    buy_date = DateField(
        "Buy Date",
        format="%Y-%m-%d",  # Expected date format
        validators=[DataRequired()],
    )
    buy_quantity = DecimalField("Buy Quantity", validators=[DataRequired()], places=2)
    buy_rate = DecimalField("Buy Rate", validators=[DataRequired()], places=2)
    description = StringField("Description", validators=[Optional(), Length(max=200)])
    sell_date = DateField("Sell Date", format="%Y-%m-%d", validators=[Optional()])
    sell_quantity = DecimalField("Sell Quantity", validators=[Optional()], places=2)
    sell_rate = DecimalField("Sell Rate", validators=[Optional()], places=2)
    gain_from_sale = DecimalField("Gain from Sale", validators=[Optional()], places=2)
    submit = SubmitField("Save Transaction")
