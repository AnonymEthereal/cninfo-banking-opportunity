from datetime import date
from pathlib import Path
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator

class Collector:
    def collect(self, company, date_range):
        return [Announcement(announcement_id='a1', stock_code='000001', company_name='A', title='t', publish_date=date_range.start_date, source_url='x')]
    def download(self, announcement, destination):
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(b'not pdf'); return destination

class Empty:
    def analyze(self, *args): return EventExtractionResult(announcement_id='a1', events=[])

def test_agent_resume_uses_saved_results(tmp_path: Path):
    runner = ScanOrchestrator(Collector(), tmp_path)
    scan = runner.create([Company(stock_code='1', company_name='A')], DateRange(start_date=date(2026,1,1), end_date=date(2026,1,2)))
    runner.run_collection(scan.scan_id)
    runner.prepare_analysis(scan.scan_id)
    # Resume path must be safe even when no prior opportunities exist.
    runner.run_agents(scan.scan_id, Empty(), Empty(), Empty())
    result = runner.load(scan.scan_id)
    assert result.progress.stage == 'analysis_completed'
