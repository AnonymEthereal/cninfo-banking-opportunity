from datetime import date
from pathlib import Path
from core.models import Company, DateRange, Announcement
from core.scanner import ScanOrchestrator

class FakeCollector:
    def collect(self, company, date_range):
        if company.stock_code == "000002":
            raise RuntimeError("network down")
        return [Announcement(announcement_id="same", stock_code=company.stock_code, company_name=company.company_name, title="公告", publish_date=date(2026, 11, 6), source_url="https://example/a")]

def test_collection_persists_and_deduplicates(tmp_path: Path):
    runner = ScanOrchestrator(FakeCollector(), tmp_path)
    scan = runner.create([Company(stock_code="1", company_name="A"), Company(stock_code="2", company_name="B")], DateRange(start_date=date(2026, 11, 1), end_date=date(2026, 11, 6)))
    result = runner.run_collection(scan.scan_id)
    assert result.counts.announcements == 1
    assert result.counts.collection_failed == 1
    assert str(runner.load(scan.scan_id).status) in ("collecting", "ScanStatus.COLLECTING")
