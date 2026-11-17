from datetime import date, datetime, timedelta
import time
from threading import Event

import pytest

from core.app_service import AppService
from core.errors import RuntimeBusyError, RuntimeLeaseLostError, ServiceError
from core.models import Announcement, Company, DateRange, ScanStatus
from core.runtime import RuntimeLeaseGuard, RuntimeLock
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager
from core.storage import atomic_write_json


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


def test_acquire_does_not_steal_a_fresh_partially_written_lock(tmp_path):
    lock = RuntimeLock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("", encoding="utf-8")

    assert lock.acquire("scan_new") is False
    assert lock.path.exists()

    old = datetime.now().astimezone() - timedelta(minutes=2)
    import os

    os.utime(lock.path, (old.timestamp(), old.timestamp()))
    assert lock.acquire("scan_recovered") is True
    assert lock.read()["scan_id"] == "scan_recovered"
    lock.release("scan_recovered")


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


def test_old_lease_cannot_heartbeat_or_release_after_takeover(tmp_path):
    lock = RuntimeLock(tmp_path)
    assert lock.acquire("scan_old")
    old_lease = lock.read()["lease_id"]
    info = lock.read()
    info["heartbeat_at"] = (datetime.now().astimezone() - timedelta(hours=1)).isoformat()
    lock.path.write_text(__import__("json").dumps(info), encoding="utf-8")

    assert lock.acquire("scan_new")
    new_info = lock.read()
    assert new_info["lease_id"] != old_lease

    guard = RuntimeLeaseGuard(lock, "scan_old", old_lease)
    assert guard.heartbeat() is False
    with pytest.raises(RuntimeLeaseLostError):
        guard.assert_owned()
    assert lock.release("scan_old", old_lease) is False
    assert lock.current_active()["scan_id"] == "scan_new"
    lock.release("scan_new", new_info["lease_id"])


def test_guarded_atomic_write_does_not_replace_after_lease_loss(tmp_path):
    class LostLease:
        def assert_owned(self):
            raise RuntimeLeaseLostError("lease lost")

    target = tmp_path / "state.json"
    target.write_text('{"owner":"new"}', encoding="utf-8")
    with pytest.raises(RuntimeLeaseLostError):
        atomic_write_json(target, {"owner": "old"}, lease_guard=LostLease())
    assert target.read_text(encoding="utf-8") == '{"owner":"new"}'
    assert not list(tmp_path.glob("*.tmp"))


def test_worker_does_not_write_checkpoint_after_lease_takeover(tmp_path):
    from threading import Event

    started, finish_agent = Event(), Event()

    class WaitingAgent:
        def analyze(self, *args):
            started.set()
            assert finish_agent.wait(timeout=10)
            return {"announcement_id": "ann", "events": []}

    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    manager = TaskManager(runner)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = ScanStatus.ANALYZING
    scan.analysis_mode = "mock"
    runner.save(scan)
    text_path = tmp_path / "text.txt"
    text_path.write_text("source", encoding="utf-8")
    announcement = Announcement(
        announcement_id="ann",
        stock_code="000001",
        company_name="A",
        title="title",
        publish_date=date(2026, 1, 1),
        source_url="https://example.com/ann",
        text_path=str(text_path),
    )
    atomic_write_json(
        tmp_path / "results" / scan.scan_id / "announcements.json",
        {"items": [announcement.model_dump()]},
    )
    old_future = manager.submit(
        scan.scan_id, (WaitingAgent(), lambda *args: None, lambda *args: None)
    )
    takeover = RuntimeLock(tmp_path)
    try:
        assert started.wait(timeout=5)
        old_info = takeover.read()
        old_info["heartbeat_at"] = (
            datetime.now().astimezone() - timedelta(hours=1)
        ).isoformat()
        takeover.path.write_text(__import__("json").dumps(old_info), encoding="utf-8")
        assert takeover.acquire("scan_new_owner")
        new_lease = takeover.read()["lease_id"]
    finally:
        finish_agent.set()

    with pytest.raises(RuntimeLeaseLostError):
        old_future.result(timeout=10)
    result_checkpoint = (
        tmp_path / "results" / scan.scan_id / "agents" / "ann" / "result.json"
    )
    assert not result_checkpoint.exists()
    assert runner.load(scan.scan_id).status == ScanStatus.ANALYZING
    assert takeover.read()["scan_id"] == "scan_new_owner"
    takeover.release("scan_new_owner", new_lease)
    manager.shutdown()


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
