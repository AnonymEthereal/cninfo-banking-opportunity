from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook
from core.models import Company, DateRange, Scan, Opportunity
from exporters.excel_exporter import export_xlsx
from exporters.html_exporter import export_html

def test_export_empty_reports(tmp_path: Path):
    scan = Scan(scan_id="s1", companies=[Company(stock_code="1", company_name="A")], date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)), created_at=datetime.now(), updated_at=datetime.now())
    xlsx = export_xlsx([], scan, tmp_path / "r.xlsx")
    html = export_html([], scan, tmp_path / "r.html")
    assert load_workbook(xlsx).sheetnames == ["商机明细", "扫描概览"]
    assert "没有识别到商机" in html.read_text(encoding="utf-8")

def test_html_preserves_zero_amount(tmp_path: Path):
    scan = Scan(scan_id="s2", companies=[Company(stock_code="1", company_name="A")], date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)), created_at=datetime.now(), updated_at=datetime.now())
    opportunity = Opportunity(opportunity_id="o", announcement_id="a", stock_code="000001", company_name="A", publish_date=date(2026,1,1), announcement_title="t", category="其他类", event_summary="e", source_url="https://example", amount={"value": 0, "currency": "CNY"})
    html = export_html([opportunity], scan, tmp_path / "zero.html").read_text(encoding="utf-8")
    assert "金额：0" in html
