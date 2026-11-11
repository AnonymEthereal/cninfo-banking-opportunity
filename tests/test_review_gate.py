from datetime import date
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager


def test_review_gate_does_not_download_or_analyze_before_confirmation(tmp_path, monkeypatch):
    calls = []

    class Collector:
        def collect(self, company, period):
            return [Announcement(announcement_id="a", stock_code=company.stock_code, company_name=company.company_name, title="title", publish_date=period.start_date, source_url="https://example.com")]

        def download(self, announcement, destination):
            calls.append("download")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"body")

    class Agent:
        def analyze(self, announcement, text):
            calls.append("analyze")
            return EventExtractionResult(announcement_id="a", events=[])

    monkeypatch.setattr("core.scanner.parse_document", lambda path: ("body", []))
    runner = ScanOrchestrator(Collector(), tmp_path)
    scan = runner.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)))
    scan.require_review = True
    runner._save(scan)
    manager = TaskManager(runner)
    try:
        result = manager.submit(scan.scan_id, (Agent(), None, None)).result(timeout=10)
        assert result.status == "awaiting_review"
        assert calls == []
        assert manager.submit(scan.scan_id, (Agent(), None, None)).result(timeout=10).status == "awaiting_review"
        result.analysis_confirmed = True
        runner._save(result)
        completed = manager.submit(scan.scan_id, (Agent(), None, None)).result(timeout=10)
        assert completed.status == "completed"
        assert calls == ["download", "analyze"]
        assert completed.announcement_timeout == 600
        assert completed.analysis_timeout == 28800
    finally:
        manager.shutdown()
