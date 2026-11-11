from datetime import date
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator
from core.storage import atomic_write_json


def test_retry_failed_only_preserves_success_and_history(tmp_path):
    runner = ScanOrchestrator(None, tmp_path)
    scan = runner.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)))
    folder = tmp_path / "results" / scan.scan_id
    text = tmp_path / "body.txt"
    text.write_text("body", encoding="utf-8")
    announcements = [Announcement(announcement_id=key, stock_code="000001", company_name="A", title="t", publish_date=date(2026, 1, 1), source_url="https://example.com", text_path=str(text)) for key in ("good", "bad")]
    atomic_write_json(folder / "announcements.json", {"items": [item.model_dump() for item in announcements]})
    atomic_write_json(folder / "agents/good/result.json", {"status": "completed", "items": []})
    atomic_write_json(folder / "agents/bad/result.json", {"status": "failed", "items": [], "code": "AGENT_FAILED"})
    scan.status = "analyzing"
    runner._save(scan)
    calls = []

    class Agent:
        def analyze(self, announcement, text):
            calls.append(announcement.announcement_id)
            return EventExtractionResult(announcement_id=announcement.announcement_id, events=[])

    result = runner.run_agents(scan.scan_id, Agent(), None, None, retry_failed=True)
    assert calls == ["bad"]
    assert result.counts.analysis_failed == 0
    assert list((folder / "agents/bad/history").glob("*.json"))
