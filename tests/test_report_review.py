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
    assert values["公告编号"] == "ann"
    assert values["核心金额数值"] is None
    assert values["金额是否未披露"] == "是"
    assert sheet.cell(2, headers.index("事件摘要") + 1).data_type == "s"
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
    source_column = unsafe_headers.index("公告原文链接") + 1
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
    assert values["金额核验状态"] == "待核实"
    assert values["币种"] == "币种未明确"


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
        export_xlsx(
            [opportunity], scan, tmp_path / "analysis-outcome.xlsx"
        )
    )
    excluded = workbook["未形成商机"]
    headers = [cell.value for cell in excluded[1]]
    values = dict(zip(headers, [cell.value for cell in excluded[2]]))
    assert values["切入点判断"] == "暂未识别明确切入点"
    assert values["分析结论"] == opportunity.analysis_summary

    html = export_html(
        [opportunity], scan, tmp_path / "analysis-outcome.html"
    ).read_text(encoding="utf-8")
    assert "暂未识别明确切入点" in html
    assert opportunity.analysis_summary in html


def test_excel_uses_chinese_headers_and_exports_available_opportunity_fields(tmp_path):
    scan = Scan(
        scan_id="chinese-headers",
        companies=[Company(stock_code="000001", company_name="A")],
        date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    opportunity = Opportunity(
        opportunity_id="chinese-headers",
        announcement_id="ann",
        stock_code="000001",
        company_name="企业",
        publish_date=date(2026, 1, 1),
        announcement_title="公告",
        category="采购类",
        event_summary="采购设备",
        source_url="https://example.com/ann",
        counterparty="供应商甲",
        project_name="生产线项目",
        other_amounts=[{"value": 2000000, "currency": "CNY", "type": "设备金额"}],
        related_announcement_ids=["ann-related"],
    )

    workbook = load_workbook(
        export_xlsx([opportunity], scan, tmp_path / "chinese-headers.xlsx")
    )
    sheet = workbook["商机明细"]
    headers = [cell.value for cell in sheet[1]]
    values = dict(zip(headers, [cell.value for cell in sheet[2]]))

    assert headers[0] == "商机编号"
    assert "交易对手" in headers
    assert "项目名称" in headers
    assert "其他披露金额" in headers
    assert "可能关联公告编号" in headers
    assert values["交易对手"] == "供应商甲"
    assert values["项目名称"] == "生产线项目"
    assert "设备金额" in values["其他披露金额"]
    assert values["可能关联公告编号"] == "ann-related"


def test_non_opportunities_are_exported_separately_and_excluded_from_count(tmp_path):
    scan = Scan(
        scan_id="excluded-items",
        companies=[Company(stock_code="000001", company_name="A")],
        date_range=DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    no_clear = Opportunity(
        opportunity_id="excluded",
        announcement_id="ann",
        stock_code="000001",
        company_name="企业",
        publish_date=date(2026, 1, 1),
        announcement_title="股东会通知",
        category="其他类",
        event_summary="召开股东会",
        source_url="https://example.com/ann",
        analysis_outcome="no_clear_entry_point",
        analysis_summary="程序性公告，没有明确业务切入点。",
    )
    scan.counts.non_opportunities = 1
    workbook = load_workbook(
        export_xlsx(
            [],
            scan,
            tmp_path / "excluded-items.xlsx",
            non_opportunities=[no_clear],
        )
    )

    assert scan.counts.opportunities == 0
    assert workbook.sheetnames == ["商机明细", "未形成商机", "扫描概览"]
    assert workbook["未形成商机"].max_row == 2
    html = export_html(
        [],
        scan,
        tmp_path / "excluded-items.html",
        non_opportunities=[no_clear],
    ).read_text(encoding="utf-8")
    assert "未形成商机的事项" in html
    assert "程序性公告" in html
