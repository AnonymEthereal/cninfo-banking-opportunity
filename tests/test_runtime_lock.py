from datetime import date, datetime, timedelta
import time
from threading import Event

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


def test_submit_takes_lock_before_the_worker_starts(tmp_path, monkeypatch):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    started, release = Event(), Event()

    def slow_collect(scan_id, cancel_event=None):
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr(runner, "run_collection", slow_collect)
    future = manager.submit(scan.scan_id, agents())
    try:
        # The lock is already held even though the worker may not have run yet.
        info = RuntimeLock(tmp_path).current_active()
        assert info and info["scan_id"] == scan.scan_id
    finally:
        release.set()
    future.result(timeout=10)
    assert RuntimeLock(tmp_path).current_active() is None
    manager.shutdown()


def test_heartbeat_keeps_lock_fresh_during_long_task(tmp_path, monkeypatch):
    import core.task_manager as task_manager_module

    real_lock = task_manager_module.RuntimeLock
    monkeypatch.setattr(
        task_manager_module, "RuntimeLock", lambda root: real_lock(root, ttl_seconds=1)
    )

    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    started, release = Event(), Event()

    def slow_collect(scan_id, cancel_event=None):
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr(runner, "run_collection", slow_collect)
    future = manager.submit(scan.scan_id, agents())
    try:
        assert started.wait(timeout=10)
        first = real_lock(tmp_path).read()["heartbeat_at"]
        # The heartbeat interval is ttl/3, so one second is enough here.
        time.sleep(1.6)
        second = real_lock(tmp_path).read()["heartbeat_at"]
        assert second > first
        assert real_lock(tmp_path, ttl_seconds=1).current_active() is not None
    finally:
        release.set()
    future.result(timeout=10)
    manager.shutdown()


def test_finished_task_is_forgotten(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    future = manager.submit(scan.scan_id, agents())
    future.result(timeout=10)
    assert manager.futures == {}
    assert manager.running_scan_ids() == []
    manager.shutdown()


def test_stop_request_preserves_progress_and_is_resumable(tmp_path, monkeypatch):
    from threading import Event

    class FirstCompanyWaits:
        started = Event()

        def collect(self, company, date_range):
            self.started.set()
            return []

    collector = FirstCompanyWaits()
    runner = ScanOrchestrator(collector, tmp_path)
    manager = TaskManager(runner)
    companies = [
        Company(stock_code="1", company_name="A"),
        Company(stock_code="2", company_name="B"),
    ]
    scan = runner.create(
        companies,
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.require_review = True
    scan.analysis_mode = "mock"
    runner.save(scan)
    service = AppService(runner, manager)
    monkeypatch.setenv("COLLECTION_INTERVAL_SECONDS", "0")

    future = manager.submit(scan.scan_id, agents())
    assert collector.started.wait(timeout=5)
    assert service.get_scan_view(scan.scan_id).capabilities.can_cancel is True
    service.cancel_scan(scan.scan_id)
    assert service.get_scan_view(scan.scan_id).capabilities.stop_requested is True
    stopped = future.result(timeout=10)

    assert stopped.status == "cancelled"
    assert stopped.progress.completed == 1
    assert stopped.counts.announcements == 0
    assert manager.request_stop(scan.scan_id) is False
    assert service.get_scan_view(scan.scan_id).capabilities.can_resume is True
    assert RuntimeLock(tmp_path).current_active() is None
    service.resume_scan(scan.scan_id)
    resumed = manager.futures[scan.scan_id].result(timeout=10)
    assert resumed.status == "awaiting_review"
    assert all(error.code != "TASK_CANCELLED" for error in resumed.errors)
    manager.shutdown()


def test_runtime_state_exposes_elapsed_time_and_progress(tmp_path):
    from core.app_service import AppService

    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_active")
    info = lock.read()
    info.update(stage="agents", completed=4, total=12)
    lock.path.write_text(__import__("json").dumps(info), encoding="utf-8")
    try:
        state = AppService(runner, manager).get_runtime_state()
        assert state.active is True
        assert state.stage == "agents"
        assert state.completed == 4
        assert state.total == 12
        assert state.elapsed_seconds >= 0
        assert state.heartbeat_age_seconds is not None
    finally:
        lock.release("scan_active")
        manager.shutdown()


def test_service_keeps_busy_code_when_submit_loses_the_race(tmp_path):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    service = AppService(runner, manager)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = "analyzing"
    scan.analysis_mode = "mock"
    runner.save(scan)

    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_external")
    try:
        with pytest.raises(ServiceError) as exc:
            service.resume_scan(scan.scan_id)
        assert exc.value.code == "ANOTHER_SCAN_RUNNING"
    finally:
        lock.release("scan_external")
        manager.shutdown()
