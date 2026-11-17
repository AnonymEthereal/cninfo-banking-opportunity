from decimal import Decimal

from core.amounts import parse_disclosed_amount
from core.models import Amount


def test_parses_chinese_units_and_explicit_currency():
    assert parse_disclosed_amount("拟投资1.25亿元人民币") == (
        parse_disclosed_amount("人民币1.25亿元")
    )
    parsed = parse_disclosed_amount("人民币1.25亿元")
    assert parsed.value == Decimal("125000000")
    assert parsed.currency == "CNY"

    parsed = parse_disclosed_amount("港币2,500万元")
    assert parsed.value == Decimal("25000000")
    assert parsed.currency == "HKD"


def test_bare_yuan_unit_does_not_invent_currency():
    parsed = parse_disclosed_amount("公司投资100万元")
    assert parsed.value == Decimal("1000000")
    assert parsed.currency is None


def test_amount_schema_does_not_default_currency_to_cny():
    assert Amount().currency is None


def test_ignores_non_money_and_ambiguous_quotes():
    assert parse_disclosed_amount("注销3.58万份股票期权") is None
    assert parse_disclosed_amount("投资100万元，另获授信50万元") is None
    assert parse_disclosed_amount("项目总投资人民币一亿元") is None
    assert parse_disclosed_amount("USD 3.5 million") is None
