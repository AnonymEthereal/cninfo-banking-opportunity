import json
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from core.models import Company, DateRange, Announcement, Event, EventExtractionResult, OpportunityDraft, MarketingAnalysis
from core.scanner import ScanOrchestrator

class FixtureCollector:
    def __init__(self, root): self.root = root
    def collect(self, company, date_range):
        return [Announcement(announcement_id="ann-fixture", stock_code=company.stock_code, company_name=company.company_name, title="项目公告", publish_date=date(2026, 11, 6), source_url="https://example/source")]
    def download(self, announcement, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        import pymupdf
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), 'Project investment; new production facility.')
            document.save(destination)
        return destination

class MultiEventAgent:
    def analyze(self, announcement, text):
        return EventExtractionResult(announcement_id=announcement.announcement_id, events=[Event(event_id="e1", summary="项目投资", evidence=[{"text":"项目投资 100 万元"}]), Event(event_id="e2", summary="建设新基地", evidence=[{"text":"拟建设新基地"}])])

class DraftAgent:
    def analyze(self, event, text):
        return OpportunityDraft(event_id=event.event_id, category="投资并购类", event_summary=event.summary, evidence=event.evidence)

class Marketing:
    def analyze(self, draft, text): return MarketingAnalysis(bank_products=["项目贷款"], suggested_action="核实融资计划")

def test_end_to_end_multiple_events_and_undisclosed_amount(tmp_path: Path):
    runner = ScanOrchestrator(FixtureCollector(tmp_path), tmp_path)
    scan = runner.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026,11,1), end_date=date(2026,11,6)))
    runner.run_collection(scan.scan_id)
    runner.prepare_analysis(scan.scan_id)
    result_scan = runner.run_agents(
        scan.scan_id, MultiEventAgent(), DraftAgent(), Marketing()
    )
    result = json.loads((tmp_path / "results" / scan.scan_id / "opportunities.json").read_text(encoding="utf-8"))
    assert len(result["items"]) == 2
    assert result["items"][0]["amount"]["value"] is None
    assert result["items"][0]["analysis_outcome"] == "opportunity_identified"
    assert result_scan.counts.opportunities == 2
    assert result_scan.counts.non_opportunities == 0
    completed = runner.run_export(scan.scan_id)
    assert completed.status == "completed"
    assert (tmp_path / "reports" / scan.scan_id / "report.xlsx").exists()

def test_agent_failure_is_visible(tmp_path: Path):
    class Broken:
        def analyze(self, *args): raise ValueError("bad model output")
    runner = ScanOrchestrator(FixtureCollector(tmp_path), tmp_path)
    scan = runner.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026,11,1), end_date=date(2026,11,6)))
    runner.run_collection(scan.scan_id); runner.prepare_analysis(scan.scan_id)
    result = runner.run_agents(scan.scan_id, Broken(), DraftAgent(), Marketing())
    assert result.counts.analysis_failed == 1
    assert result.errors[0].code == "AGENT_FAILED"


def test_successful_marketing_analysis_without_a_clear_entry_point_is_explicit(tmp_path: Path):
    class NoClearEntry:
        def analyze(self, draft, text):
            return MarketingAnalysis()

    runner = ScanOrchestrator(FixtureCollector(tmp_path), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 11, 1), end_date=date(2026, 11, 6)),
    )
    runner.run_collection(scan.scan_id)
    runner.prepare_analysis(scan.scan_id)
    result_scan = runner.run_agents(
        scan.scan_id, MultiEventAgent(), DraftAgent(), NoClearEntry()
    )

    result = json.loads(
        (tmp_path / "results" / scan.scan_id / "opportunities.json").read_text(
            encoding="utf-8"
        )
    )
    excluded = json.loads(
        (
            tmp_path
            / "results"
            / scan.scan_id
            / "non_opportunities.json"
        ).read_text(encoding="utf-8")
    )
    assert result["items"] == []
    assert len(excluded["items"]) == 2
    assert excluded["items"][0]["analysis_outcome"] == "no_clear_entry_point"
    assert "暂未识别" in excluded["items"][0]["analysis_summary"]
    assert excluded["items"][0]["bank_products"] == []
    assert result_scan.counts.opportunities == 0
    assert result_scan.counts.non_opportunities == 2
    runner.run_export(scan.scan_id)
    workbook = load_workbook(
        tmp_path / "reports" / scan.scan_id / "report.xlsx"
    )
    assert workbook.sheetnames == [
        "商机明细",
        "扫描概览",
        "公告处理明细",
    ]
    assert workbook["商机明细"].max_row == 3
    headers = [cell.value for cell in workbook["商机明细"][1]]
    is_opportunity_column = headers.index("是否商机") + 1
    assert all(
        workbook["商机明细"].cell(row, is_opportunity_column).value == "否"
        for row in (2, 3)
    )


def test_announcement_without_extracted_events_is_visible_in_report(tmp_path: Path):
    class EmptyEvents:
        def analyze(self, announcement, text):
            return EventExtractionResult(
                announcement_id=announcement.announcement_id,
                events=[],
            )

    runner = ScanOrchestrator(FixtureCollector(tmp_path), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 11, 1), end_date=date(2026, 11, 6)),
    )
    runner.run_collection(scan.scan_id)
    runner.prepare_analysis(scan.scan_id)
    result = runner.run_agents(
        scan.scan_id, EmptyEvents(), DraftAgent(), Marketing()
    )
    assert result.counts.opportunities == 0
    assert result.counts.non_opportunities == 0

    coverage = json.loads(
        (
            tmp_path
            / "results"
            / scan.scan_id
            / "announcement_analysis.json"
        ).read_text(encoding="utf-8")
    )["items"]
    assert len(coverage) == 1
    assert coverage[0]["raw_event_count"] == 0
    assert coverage[0]["opportunity_candidate_count"] == 0
    assert coverage[0]["status"] == "未识别到事件"

    runner.run_export(scan.scan_id)
    workbook = load_workbook(
        tmp_path / "reports" / scan.scan_id / "report.xlsx"
    )
    sheet = workbook["公告处理明细"]
    headers = [cell.value for cell in sheet[1]]
    values = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert values["处理状态"] == "未识别到事件"
    assert values["公告标题"] == "项目公告"


def test_announcement_coverage_reports_event_level_deduplication(tmp_path: Path):
    class DuplicateEvents:
        def analyze(self, announcement, text):
            quote = "公司发行债券20亿元。"
            return EventExtractionResult(
                announcement_id=announcement.announcement_id,
                events=[
                    Event(event_id="e1", summary="发行债券20亿元。", evidence=[{"text": quote}]),
                    Event(event_id="e2", summary="发行债券20亿元。", evidence=[{"text": quote}]),
                ],
            )

    runner = ScanOrchestrator(FixtureCollector(tmp_path), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 11, 1), end_date=date(2026, 11, 6)),
    )
    runner.run_collection(scan.scan_id)
    runner.prepare_analysis(scan.scan_id)
    runner.run_agents(scan.scan_id, DuplicateEvents(), DraftAgent(), Marketing())

    coverage = json.loads(
        (
            tmp_path
            / "results"
            / scan.scan_id
            / "announcement_analysis.json"
        ).read_text(encoding="utf-8")
    )["items"][0]
    assert coverage["raw_event_count"] == 2
    assert coverage["unique_event_count"] == 1
    assert coverage["duplicate_event_count"] == 1
    assert coverage["analyzable_event_count"] == 1
    assert coverage["opportunity_candidate_count"] == 1
