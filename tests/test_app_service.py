from datetime import date

import pytest

from core.app_service import AppService
from core.errors import DomainError, InvalidStateError, NotFoundError
from core.models import Announcement, Company, DateRange, Opportunity
from core.scanner import ScanOrchestrator
from core.storage import atomic_write_json
from core.task_manager import TaskManager


class OneAnnouncementCollector:
    def collect(self, company, period):
        return [
            Announcement(
                announcement_id="ann-1",
                stock_code=company.stock_code,
                company_name=company.company_name,
                title="项目公告",
                publish_date=period.start_date,
                source_url="https://example.com/a",
            )
        ]

    def download(self, announcement, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"body")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.scanner.parse_document", lambda path: ("body", []))
    orchestrator = ScanOrchestrator(OneAnnouncementCollector(), tmp_path)
    manager = TaskManager(orchestrator)
    app = AppService(orchestrator, manager)
    try:
        yield app
    finally:
        manager.shutdown()


def wait_for(service, scan_id):
    service.task_manager.futures[scan_id].result(timeout=10)


def test_start_scan_waits_for_review_then_confirms(service):
    view = service.start_scan(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        "mock",
    )
    scan_id = view.scan.scan_id
    wait_for(service, scan_id)
    view = service.get_scan_view(scan_id)
    assert view.scan.status == "awaiting_review"
    assert view.scan.require_review is True
    assert view.scan.analysis_mode == "mock"
    assert view.capabilities.can_confirm is True
    assert view.capabilities.can_resume is False

    service.confirm_analysis(scan_id, per_minutes=2, batch_hours=3)
    wait_for(service, scan_id)
    view = service.get_scan_view(scan_id)
    assert view.scan.status == "completed"
    assert view.scan.announcement_timeout == 120
    assert view.scan.analysis_timeout == 10800
    assert view.capabilities.can_download is True
    assert view.reports == {"xlsx": True, "html": True}


def test_legacy_mode_must_be_selected_before_resume(service):
    scan = service.orchestrator.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = "failed"
    service.orchestrator.save(scan)

    with pytest.raises(DomainError) as exc:
        service.resume_scan(scan.scan_id)
    assert exc.value.code == "ANALYSIS_MODE_UNKNOWN"

    view = service.set_legacy_mode(scan.scan_id, "mock")
    assert view.scan.analysis_mode == "mock"
    assert view.capabilities.can_resume is True


def test_announcement_and_opportunity_queries(service):
    scan = service.orchestrator.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    folder = service.orchestrator.root / "results" / scan.scan_id
    atomic_write_json(
        folder / "announcements.json",
        {
            "items": [
                Announcement(
                    announcement_id=f"ann-{index}",
                    stock_code="000001",
                    company_name="甲公司" if index < 2 else "乙公司",
                    title="采购公告" if index == 0 else "投资公告",
                    publish_date=date(2026, 1, index + 1),
                    source_url=f"https://example.com/{index}",
                ).model_dump()
                for index in range(3)
            ]
        },
    )
    page = service.list_announcements(scan.scan_id)
    assert page.total == 3
    assert service.list_announcements(scan.scan_id, query="采购").total == 1
    assert service.list_announcements(scan.scan_id, query="乙公司").total == 1
    assert len(service.list_announcements(scan.scan_id, limit=1, offset=1).items) == 1

    opportunities = [
        Opportunity(
            opportunity_id=f"opp-{index}",
            announcement_id="ann-0",
            stock_code="000001",
            company_name="甲公司" if index == 0 else "乙公司",
            publish_date=date(2026, 1, 1),
            announcement_title="公告",
            category="采购类" if index == 0 else "投资并购类",
            event_summary="事件",
            source_url="https://example.com",
            analysis_status="completed" if index == 0 else "partial",
        )
        for index in range(2)
    ]
    atomic_write_json(folder / "opportunities.json", {"items": [item.model_dump() for item in opportunities]})
    result = service.list_opportunities(scan.scan_id)
    assert result.total == 2
    assert result.categories == ["投资并购类", "采购类"]
    assert result.companies == ["乙公司", "甲公司"]
    assert service.list_opportunities(scan.scan_id, categories=["采购类"]).total == 1
    assert service.list_opportunities(scan.scan_id, statuses=["partial"]).total == 1
    assert service.list_opportunities(scan.scan_id, limit=1).total == 2
    assert len(service.list_opportunities(scan.scan_id, limit=1).items) == 1


def test_corrupt_scan_and_missing_report_are_visible(service):
    folder = service.orchestrator.root / "results" / "scan_corrupt"
    folder.mkdir(parents=True)
    (folder / "scan.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(DomainError) as exc:
        service.get_scan_view("scan_corrupt")
    assert exc.value.code == "SCAN_DATA_CORRUPT"

    with pytest.raises(NotFoundError):
        service.get_scan_view("scan_missing")

    scan = service.orchestrator.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    with pytest.raises(DomainError) as exc:
        service.get_report_bytes(scan.scan_id, "xlsx")
    assert exc.value.code == "REPORT_NOT_READY"


def test_history_skips_corrupt_tasks(service):
    valid = service.orchestrator.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    corrupt = service.orchestrator.root / "results" / "scan_corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "scan.json").write_text("{not json", encoding="utf-8")

    history = service.list_history()
    assert [item.scan_id for item in history] == [valid.scan_id]


def test_invalid_state_actions_are_rejected(service):
    scan = service.orchestrator.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    with pytest.raises(InvalidStateError):
        service.confirm_analysis(scan.scan_id)
    with pytest.raises(InvalidStateError):
        service.retry_sources(scan.scan_id)
    with pytest.raises(InvalidStateError):
        service.retry_failed_analysis(scan.scan_id)


def test_orchestrator_exposes_the_api_used_by_the_service():
    # Guards against deploying a new app_service against an older scanner.
    for name in ("save", "run_collection", "prepare_analysis", "run_agents", "run_export"):
        assert callable(getattr(ScanOrchestrator, name, None)), name
