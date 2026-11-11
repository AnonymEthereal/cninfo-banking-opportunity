from datetime import date
import pytest
from core.models import Company, DateRange, Announcement
from core.scanner import ScanOrchestrator


def test_collection_recovers_success_and_empty_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLECTION_INTERVAL_SECONDS", "0")
    calls = []

    class Collector:
        interrupted = False

        def collect(self, company, period):
            calls.append(company.stock_code)
            if company.stock_code == "000003" and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt()
            if company.stock_code == "000002":
                return []
            return [Announcement(announcement_id=company.stock_code, stock_code=company.stock_code, company_name=company.company_name, title="t", publish_date=period.start_date, source_url="https://example.com")]

    collector = Collector()
    runner = ScanOrchestrator(collector, tmp_path)
    scan = runner.create([Company(stock_code=str(i), company_name=str(i)) for i in range(1, 4)], DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)))
    with pytest.raises(KeyboardInterrupt):
        runner.run_collection(scan.scan_id)
    recovered = ScanOrchestrator(collector, tmp_path).run_collection(scan.scan_id)
    assert calls == ["000001", "000002", "000003", "000003"]
    assert recovered.counts.announcements == 2
    assert recovered.progress.completed == 3
