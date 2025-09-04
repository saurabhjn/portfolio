from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length

from model import Currency


class InvestmentForm(FlaskForm):
    """Form for adding or editing an investment."""
    investment_name = StringField(
        'Investment Name',
        validators=[DataRequired(), Length(min=2, max=100)]
    )
    ticker = StringField(
        'Ticker Symbol',
        validators=[DataRequired(), Length(min=1, max=20)]
    )
    five_year_annualised_return = DecimalField(
        '5-Year Annualised Return (%)',
        validators=[DataRequired()],
        places=2
    )
    ten_year_annualised_return = DecimalField(
        '10-Year Annualised Return (%)',
        validators=[DataRequired()],
        places=2
    )
    currency = SelectField(
        'Currency',
        # The choices are tuples of (value, label)
        choices=[(c.name, c.value) for c in Currency],
        validators=[DataRequired()]
    )
    submit = SubmitField('Save Investment')
