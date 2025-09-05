from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SelectField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError
from wtforms.fields.datetime import DateField

from model import Currency


class InvestmentForm(FlaskForm):
    """Form for adding or editing an investment."""

    investment_name = StringField(
        "Investment Name", validators=[DataRequired(), Length(min=2, max=100)]
    )
    ticker = StringField("Ticker Symbol", validators=[Optional(), Length(max=50)])
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
        validators=[Optional()],
    )
    buy_quantity = DecimalField("Buy Quantity", validators=[Optional()], places=4)
    buy_rate = DecimalField("Buy Rate", validators=[Optional()], places=4)
    description = StringField("Description", validators=[Optional(), Length(max=200)])
    sell_date = DateField("Sell Date", format="%Y-%m-%d", validators=[Optional()])
    sell_quantity = DecimalField("Sell Quantity", validators=[Optional()], places=4)
    sell_rate = DecimalField("Sell Rate", validators=[Optional()], places=4)
    gain_date = DateField("Gain Date", format="%Y-%m-%d", validators=[Optional()])
    gain_amount = DecimalField("Gain Amount", validators=[Optional()], places=2)
    submit = SubmitField("Save Transaction")

    def __init__(self, *args, **kwargs):
        # Custom __init__ to accept transaction data for validation
        self.transactions_data = kwargs.pop("transactions_data", {})
        self.original_investment_name = kwargs.pop("original_investment_name", None)
        self.transaction_index = kwargs.pop("transaction_index", None)
        super().__init__(*args, **kwargs)

    def validate(self, extra_validators=None):
        """
        Custom validation to ensure that at least one complete group of fields
        (buy, sell, or gain) is provided, and no group is partial.
        """
        # Run parent validation first
        if not super().validate(extra_validators):
            return False

        buy_fields = [self.buy_date.data, self.buy_quantity.data, self.buy_rate.data]
        sell_fields = [
            self.sell_date.data,
            self.sell_quantity.data,
            self.sell_rate.data,
        ]
        gain_fields = [self.gain_date.data, self.gain_amount.data]

        is_buy_partial = any(f is not None for f in buy_fields) and not all(
            f is not None for f in buy_fields
        )
        is_sell_partial = any(f is not None for f in sell_fields) and not all(
            f is not None for f in sell_fields
        )
        is_gain_partial = any(f is not None for f in gain_fields) and not all(
            f is not None for f in gain_fields
        )

        has_error = False
        if is_buy_partial:
            self.buy_date.errors.append(
                "All 'Buy' fields (Date, Quantity, Rate) must be filled together."
            )
            has_error = True
        if is_sell_partial:
            self.sell_date.errors.append(
                "All 'Sell' fields (Date, Quantity, Rate) must be filled together."
            )
            has_error = True
        if is_gain_partial:
            self.gain_date.errors.append(
                "Both 'Gain' fields (Date, Amount) must be filled together."
            )
            has_error = True

        is_any_complete = any(
            [
                all(f is not None for f in buy_fields),
                all(f is not None for f in sell_fields),
                all(f is not None for f in gain_fields),
            ]
        )

        if not is_any_complete and not has_error:
            self.submit.errors.append(
                "A transaction requires at least one complete group: Buy, Sell, or Gain."
            )
            has_error = True

        # New validation: Ensure sell_quantity does not exceed available quantity
        if (
            self.sell_quantity.data is not None
            and self.sell_quantity.data > 0
            and not self.sell_quantity.errors
            and self.buy_quantity.data is not None
            and self.buy_quantity.data > 0
        ):
            if self.sell_quantity.data > self.buy_quantity.data:
                self.sell_quantity.errors.append(
                    f"Sell quantity cannot exceed available quantity ({self.buy_quantity.data:f})."
                )
                has_error = True

        return not has_error
