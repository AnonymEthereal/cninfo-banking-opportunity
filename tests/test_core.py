from datetime import date
from core.calendar import default_date_range
from core.models import Company, DateRange


def test_company_code_normalized():
    assert Company(stock_code=1, company_name="A").stock_code == "000001"


def test_date_range_weekday():
    result = default_date_range(date(2026, 11, 9))
    assert result.start_date == date(2026, 11, 6)
    assert result.end_date == date(2026, 11, 8)

def test_national_day_closure():
    result = default_date_range(date(2025, 10, 9))
    assert result.start_date == date(2025, 9, 30)
    assert result.end_date == date(2025, 10, 8)
