from .calendar import default_date_range
from .company_list import validate_company_file
from .models import Company, DateRange
from .scanner import ScanOrchestrator

def validate_company_upload(content: bytes) -> dict:
    return validate_company_file(content)

def create_scan(companies: list[Company], date_range: DateRange | None = None, collector=None):
    if not companies:
        raise ValueError("COMPANIES_REQUIRED")
    return ScanOrchestrator(collector).create(companies, date_range or default_date_range())
