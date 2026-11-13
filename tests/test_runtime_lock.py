from datetime import date, datetime, timedelta

import pytest

from core.app_service import AppService
from core.errors import RuntimeBusyError, ServiceError
from core.models import Company, DateRange
from core.runtime import RuntimeLock
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager


class NoopCollector:
    def collect(self, company, date_range):
        return []


def agents():
    return (lambda *args: {"announcement_id": "x", "events": []}, lambda *args: None, lambda *args: None)


def test_runtime_lock_is_single_flight(tmp_path):
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_a") is True
    assert lock.acquire("scan_b") is False
    assert lock.current_active()["scan_id"] == "scan_a"
    lock.release("scan_a")
    assert lock.current_active() is None
    assert lock.acquire("scan_b") is True
    lock.release("scan_b")


def test_stale_lock_can_be_taken_over(tmp_path):
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_old")
    info = lock.read()
    info["heartbeat_at"] = (datetime.now().astimezone() - timedelta(hours=1)).isoformat()
    lock.path.write_text(__import__("json").dumps(info), encoding="utf-8")

    assert lock.is_stale() is True
    assert lock.current_active() is None
    assert lock.acquire("scan_new") is True
    lock.release("scan_new")


def test_task_manager_rejects_another_scan_while_locked(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_external")
    try:
        with pytest.raises(RuntimeBusyError):
            manager.submit(scan.scan_id, agents())
    finally:
        lock.release("scan_external")
    manager.shutdown()


def test_service_exposes_runtime_busy_and_blocks_submit(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    service = AppService(runner, manager)
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_external")
    try:
        state = service.get_runtime_state()
        assert state.active is True
        assert state.scan_id == "scan_external"
        with pytest.raises(ServiceError) as exc:
            service.start_scan(
                [Company(stock_code="1", company_name="A")],
                DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
            )
        assert exc.value.code == "ANOTHER_SCAN_RUNNING"
    finally:
        lock.release("scan_external")
        manager.shutdown()


def test_view_marks_scan_blocked_by_other_task(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    service = AppService(runner, manager)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.analysis_mode = "mock"
    runner.save(scan)
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_external")
    try:
        view = service.get_scan_view(scan.scan_id)
        assert view.capabilities.blocked_by_other_scan is True
        assert view.capabilities.can_resume is False
    finally:
        lock.release("scan_external")
        manager.shutdown()


def test_lock_is_released_when_scan_finishes(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    future = manager.submit(scan.scan_id, agents())
    future.result(timeout=10)
    lock = RuntimeLock(tmp_path)
    assert lock.current_active() is None
    assert lock.path.exists() is False
    manager.shutdown()


def test_run_survives_unreadable_state_file(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    folder = tmp_path / "results" / scan.scan_id
    (folder / "scan.json").write_text("{not json", encoding="utf-8")

    future = manager.submit(scan.scan_id, agents())
    with pytest.raises(Exception):
        future.result(timeout=10)
    # The runtime lock must not stay held after an unreadable checkpoint.
    assert RuntimeLock(tmp_path).current_active() is None
    manager.shutdown()
