import json
from datetime import date
from pathlib import Path
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
    runner.run_agents(scan.scan_id, MultiEventAgent(), DraftAgent(), Marketing())
    result = json.loads((tmp_path / "results" / scan.scan_id / "opportunities.json").read_text(encoding="utf-8"))
    assert len(result["items"]) == 2
    assert result["items"][0]["amount"]["value"] is None
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
