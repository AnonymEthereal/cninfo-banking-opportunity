from datetime import date
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager


def test_source_retry_reuses_success_and_analyzes_repaired_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLECTION_INTERVAL_SECONDS", "0")
    calls, downloads, analyses = [], [], []

    class Collector:
        repaired = False

        def collect(self, company, period):
            calls.append(company.stock_code)
            if company.stock_code == "000002" and not self.repaired:
                raise RuntimeError("offline")
            return [Announcement(announcement_id=company.stock_code, stock_code=company.stock_code, company_name=company.company_name, title="t", publish_date=period.start_date, source_url="https://example.com")]

        def download(self, announcement, destination):
            downloads.append(announcement.announcement_id)
            if announcement.stock_code == "000003" and not self.repaired:
                raise RuntimeError("offline")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"body")

    class Agent:
        def analyze(self, announcement, text):
            analyses.append(announcement.announcement_id)
            return EventExtractionResult(announcement_id=announcement.announcement_id, events=[])

    monkeypatch.setattr("core.scanner.parse_document", lambda path: ("body", []))
    collector = Collector()
    runner = ScanOrchestrator(collector, tmp_path)
    manager = TaskManager(runner)
    scan = runner.create([Company(stock_code=str(i), company_name=str(i)) for i in range(1, 4)], DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)))
    try:
        first = manager.submit(scan.scan_id, (Agent(), None, None)).result(timeout=10)
        assert first.counts.collection_failed == first.counts.document_failed == 1
        collector.repaired = True
        result = manager.submit(scan.scan_id, (Agent(), None, None), retry_sources=True).result(timeout=10)
        assert result.counts.collection_failed == result.counts.document_failed == 0
        assert calls.count("000001") == 1
        assert downloads.count("000001") == 1
        assert sorted(analyses) == ["000001", "000002", "000003"]
        assert result.counts.announcements == 3
        assert list((tmp_path / "results" / scan.scan_id / "history").glob("*/reports/report.xlsx"))
    finally:
        manager.shutdown()
