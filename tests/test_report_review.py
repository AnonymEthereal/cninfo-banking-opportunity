from datetime import date, datetime
from openpyxl import load_workbook
from core.models import Company, DateRange, Scan, ScanError, Opportunity
from exporters.excel_exporter import export_xlsx
from core.url_safety import safe_https_url
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


def test_html_report_only_renders_valid_https_source_links(tmp_path):
    scan = Scan(
        scan_id="link-check",
        companies=[Company(stock_code="000001", company_name="A")],
        date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    unsafe_urls = (
        "javascript:alert(1)",
        "http://example.com/announcement",
        "//example.com/announcement",
        "https://user:secret@example.com/announcement",
        "https://example.com:bad/announcement",
        "https://example.com/\n@evil",
    )
    for value in unsafe_urls:
        assert safe_https_url(value) is None

    opportunity = Opportunity(
        opportunity_id="unsafe-link",
        announcement_id="ann",
        stock_code="000001",
        company_name="企业",
        publish_date=date(2026, 1, 1),
        announcement_title="公告",
        category="其他类",
        event_summary="事实",
        source_url="javascript:alert(1)",
    )
    html = export_html([opportunity], scan, tmp_path / "unsafe.html").read_text(
        encoding="utf-8"
    )
    assert "原文链接不可用" in html
    assert "href=" not in html
    unsafe_workbook = load_workbook(
        export_xlsx([opportunity], scan, tmp_path / "unsafe.xlsx")
    )
    unsafe_headers = [
        cell.value for cell in unsafe_workbook["商机明细"][1]
    ]
    source_column = unsafe_headers.index("source_url") + 1
    assert unsafe_workbook["商机明细"].cell(2, source_column).value in ("", None)

    opportunity.source_url = "https://static.cninfo.com.cn/announcement.pdf"
    html = export_html([opportunity], scan, tmp_path / "safe.html").read_text(
        encoding="utf-8"
    )
    assert 'href="https://static.cninfo.com.cn/announcement.pdf"' in html
    assert 'rel="noopener noreferrer"' in html


def test_reports_flag_amounts_that_need_manual_verification(tmp_path):
    scan = Scan(
        scan_id="amount-review",
        companies=[Company(stock_code="000001", company_name="A")],
        date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    opportunity = Opportunity(
        opportunity_id="amount-review",
        announcement_id="ann",
        stock_code="000001",
        company_name="企业",
        publish_date=date(2026, 1, 1),
        announcement_title="公告",
        category="投资并购类",
        event_summary="项目投资",
        source_url="https://example.com/ann",
        amount={
            "value": 100000000,
            "currency": None,
            "original_text": "人民币一亿元",
            "verification_status": "pending",
        },
    )
    html = export_html([opportunity], scan, tmp_path / "pending.html").read_text(
        encoding="utf-8"
    )
    assert "金额数值待核实" in html
    assert "币种未明确" in html

    workbook = load_workbook(
        export_xlsx([opportunity], scan, tmp_path / "pending.xlsx")
    )
    headers = [cell.value for cell in workbook["商机明细"][1]]
    values = dict(zip(headers, [cell.value for cell in workbook["商机明细"][2]]))
    assert values["amount_verification_status"] == "待核实"
    assert values["currency"] == "币种未明确"


def test_reports_show_analysis_outcome_and_summary(tmp_path):
    scan = Scan(
        scan_id="analysis-outcome",
        companies=[Company(stock_code="000001", company_name="A")],
        date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    opportunity = Opportunity(
        opportunity_id="analysis-outcome",
        announcement_id="ann",
        stock_code="000001",
        company_name="企业",
        publish_date=date(2026, 1, 1),
        announcement_title="公告",
        category="销售类",
        event_summary="披露销售合同",
        source_url="https://example.com/ann",
        analysis_outcome="no_clear_entry_point",
        analysis_summary="公告未披露回款安排，暂无法确认具体结算切入点。",
    )

    workbook = load_workbook(
        export_xlsx([opportunity], scan, tmp_path / "analysis-outcome.xlsx")
    )
    headers = [cell.value for cell in workbook["商机明细"][1]]
    values = dict(zip(headers, [cell.value for cell in workbook["商机明细"][2]]))
    assert values["analysis_outcome"] == "暂未识别明确切入点"
    assert values["analysis_summary"] == opportunity.analysis_summary

    html = export_html(
        [opportunity], scan, tmp_path / "analysis-outcome.html"
    ).read_text(encoding="utf-8")
    assert "暂未识别明确切入点" in html
    assert opportunity.analysis_summary in html
