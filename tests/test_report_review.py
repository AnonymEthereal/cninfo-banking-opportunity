from datetime import date, datetime
from openpyxl import load_workbook
from core.models import Company, DateRange, Scan, ScanError, Opportunity
from exporters.excel_exporter import export_xlsx
from exporters.html_exporter import export_html


def test_reports_preserve_evidence_and_escape_content(tmp_path):
    scan = Scan(scan_id="review", companies=[Company(stock_code="000001", company_name="A")], date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)), created_at=datetime.now(), updated_at=datetime.now(), errors=[ScanError(scope="announcement", code="DOCUMENT_PARSE_FAILED", message="failed", announcement_id="bad")])
    opportunity = Opportunity(opportunity_id="opp", announcement_id="ann", stock_code="000001", company_name="<script>alert(1)</script>", publish_date=date(2026, 1, 1), announcement_title="title", category="其他类", event_summary="=1+1", source_url="https://example.com", amount={"value": None, "undisclosed": True}, evidence=[{"text": "evidence", "source_page": 2}])
    workbook = load_workbook(export_xlsx([opportunity], scan, tmp_path / "report.xlsx"))
    sheet = workbook["商机明细"]
    headers = [cell.value for cell in sheet[1]]
    values = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert values["announcement_id"] == "ann"
    assert values["amount_value"] is None
    assert values["amount_undisclosed"] is True
    assert sheet.cell(2, headers.index("event_summary") + 1).data_type == "s"
    assert sheet.freeze_panes == "A2"
    assert workbook["异常明细"].max_row == 2
    html = export_html([opportunity], scan, tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "DOCUMENT_PARSE_FAILED" in html
    assert "evidence" in html
    assert not list(tmp_path.glob("*.tmp"))
