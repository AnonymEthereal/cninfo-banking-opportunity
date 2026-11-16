from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, Lock, Thread

from .errors import RuntimeBusyError, TaskCancelled
from .models import ScanError
from .runtime import RuntimeLock
from .scanner import ScanOrchestrator


class TaskManager:
    def __init__(self, orchestrator: ScanOrchestrator, max_workers: int = 1, task_timeout: int | None = None):
        self.orchestrator = orchestrator
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan")
        self.lock = Lock()
        self.futures = {}
        self.stop_events = {}
        self.task_timeout = task_timeout

    def submit(self, scan_id: str, agents, resources=None, retry_failed=False, retry_sources=False):
        """Take the single-flight lock, then queue the worker.

        Acquiring here instead of inside the worker closes the window where a
        second submit (or a page rerun after a deploy) sees no lock owner while
        the task is already queued. The worker only releases it.
        """
        with self.lock:
            existing = self.futures.get(scan_id)
            if existing is not None and not existing.done():
                self._close_resources(resources or [])
                return existing
            runtime = RuntimeLock(self.orchestrator.root)
            if not runtime.acquire(scan_id):
                self._close_resources(resources or [])
                active = runtime.read() or {}
                raise RuntimeBusyError(
                    "ANOTHER_SCAN_RUNNING",
                    f"任务 {active.get('scan_id', '其他任务')} 正在运行，请等待其结束后再提交。",
                )
            stop_event = Event()
            try:
                future = self.executor.submit(
                    self._run,
                    scan_id,
                    agents,
                    resources or [],
                    retry_failed,
                    retry_sources,
                    True,
                    stop_event,
                )
            except BaseException:
                runtime.release(scan_id)
                self._close_resources(resources or [])
                raise
            self.futures[scan_id] = future
            self.stop_events[scan_id] = stop_event
        # Register after releasing the manager lock: if the worker already
        # finished, concurrent.futures runs the callback synchronously.
        future.add_done_callback(lambda done: self._finish(scan_id, done, resources))
        return future

    def _finish(self, scan_id, future, resources):
        self._close_resources(resources or [])
        with self.lock:
            if self.futures.get(scan_id) is future:
                self.futures.pop(scan_id, None)
                self.stop_events.pop(scan_id, None)

    def _run(
        self,
        scan_id,
        agents,
        resources,
        retry_failed=False,
        retry_sources=False,
        lock_held=False,
        cancel_event=None,
    ):
        runtime = RuntimeLock(self.orchestrator.root)
        if lock_held:
            # The lock was taken in submit. Re-check ownership in case another
            # process replaced or removed it before this worker started.
            info = runtime.read() or {}
            owned = info.get("scan_id") == scan_id and info.get("pid") == os.getpid()
            acquired = owned or runtime.acquire(scan_id)
        else:
            acquired = runtime.acquire(scan_id)
        stop_heartbeat = Event()
        heartbeat_thread = None
        try:
            if not acquired:
                raise RuntimeBusyError(
                    "ANOTHER_SCAN_RUNNING",
                    "另一个任务正在运行，当前任务未启动。",
                )
            cancel_event = cancel_event or Event()
            heartbeat_thread = Thread(
                target=self._heartbeat_loop,
                args=(runtime, scan_id, stop_heartbeat),
                name=f"scan-heartbeat-{scan_id}",
                daemon=True,
            )
            heartbeat_thread.start()
            scan = self.orchestrator.load(scan_id)
            if scan.status in ('completed', 'completed_with_errors') and not (retry_failed or retry_sources):
                return scan
            if retry_failed or retry_sources:
                from .storage import atomic_write_json
                from uuid import uuid4
                atomic_write_json(self.orchestrator.root / 'results' / scan_id / 'history' / f'{uuid4().hex}.json', scan)
                if retry_sources:
                    from pathlib import Path
                    import shutil
                    archive = self.orchestrator.root / 'results' / scan_id / 'history' / uuid4().hex
                    archive.mkdir(parents=True, exist_ok=True)
                    source = self.orchestrator.root / 'results' / scan_id
                    for filename in ('announcements.json', 'opportunities.json'):
                        if (source / filename).exists():
                            shutil.copy2(source / filename, archive / filename)
                    reports = self.orchestrator.root / 'reports' / scan_id
                    if reports.exists():
                        shutil.copytree(reports, archive / 'reports')
                    for error in scan.errors:
                        if error.code == 'DOCUMENT_PARSE_FAILED' and error.announcement_id:
                            document = self.orchestrator.root / 'announcements' / error.announcement_id
                            if document.exists():
                                shutil.copytree(document, archive / 'documents' / error.announcement_id, dirs_exist_ok=True)
                scan.status = 'collecting' if retry_sources else 'analyzing'
                if retry_sources and scan.require_review:
                    scan.analysis_confirmed = False
                self.orchestrator._save(scan)
            if scan.status in ('failed', 'cancelled'):
                if scan.status == 'cancelled':
                    scan.errors = [
                        error for error in scan.errors if error.code != 'TASK_CANCELLED'
                    ]
                scan.status = 'collecting' if scan.progress.stage in ('queued', 'collecting', 'collection_completed') else 'analyzing'
                self.orchestrator._save(scan)
            if scan.status in ('queued', 'collecting'):
                self.orchestrator.run_collection(scan_id, cancel_event=cancel_event)
                scan = self.orchestrator.load(scan_id)
                if cancel_event.is_set():
                    raise TaskCancelled()
                if scan.require_review and not scan.analysis_confirmed:
                    scan.status, scan.progress.stage = 'awaiting_review', 'awaiting_review'
                    self.orchestrator._save(scan)
                    return scan
                self.orchestrator.prepare_analysis(scan_id, retry_documents=retry_sources, cancel_event=cancel_event)
            elif scan.status == 'awaiting_review':
                if not scan.analysis_confirmed:
                    return scan
                self.orchestrator.prepare_analysis(scan_id, retry_documents=True, cancel_event=cancel_event)
            elif scan.progress.stage == 'documents':
                self.orchestrator.prepare_analysis(scan_id, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise TaskCancelled()
            self.orchestrator.run_agents(
                scan_id,
                *agents,
                per_announcement_timeout=scan.announcement_timeout,
                task_timeout=self.task_timeout or scan.analysis_timeout,
                retry_failed=retry_failed,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise TaskCancelled()
            return self.orchestrator.run_export(scan_id)
        except TaskCancelled:
            scan = self.orchestrator.load(scan_id)
            scan.status = "cancelled"
            scan.errors.append(
                ScanError(
                    scope="task",
                    code="TASK_CANCELLED",
                    message="任务已按请求停止；已完成检查点保留，可从检查点继续。",
                    details={"stage": scan.progress.stage, "action": "从检查点继续任务。"},
                )
            )
            self.orchestrator._save(scan)
            return scan
        except Exception as exc:
            try:
                scan = self.orchestrator.load(scan_id)
            except Exception:
                # A corrupt/unreadable state file must not hide the original failure.
                raise exc
            scan.status = "failed"
            scan.errors.append(
                ScanError(
                    scope="task",
                    code=getattr(exc, "code", "TASK_FAILED"),
                    message="后台任务执行失败，可查看阶段与错误类型后恢复。",
                    details={
                        "stage": scan.progress.stage,
                        "error_type": type(exc).__name__,
                        "action": "检查配置、文件权限与网络后，从检查点继续。",
                    },
                )
            )
            try:
                self.orchestrator._save(scan)
            except Exception:
                # Persisting the failure is best effort; the lock is still released.
                pass
            raise
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
            if acquired:
                runtime.release(scan_id)

    @staticmethod
    def _heartbeat_loop(runtime: RuntimeLock, scan_id: str, stop: Event) -> None:
        """Refresh the lock while an agent call runs without saving progress."""
        interval = max(1.0, runtime.ttl_seconds / 3)
        while not stop.wait(interval):
            try:
                runtime.heartbeat(scan_id)
            except Exception:
                return

    @staticmethod
    def _close_resources(resources):
        for resource in resources:
            close = getattr(resource, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    def status(self, scan_id: str):
        return self.orchestrator.load(scan_id)

    def is_running(self, scan_id: str) -> bool:
        with self.lock:
            future = self.futures.get(scan_id)
            if future is not None and not future.done():
                return True
        active = RuntimeLock(self.orchestrator.root).current_active()
        return bool(active and active.get("scan_id") == scan_id)

    def active_scan_id(self) -> str | None:
        active = RuntimeLock(self.orchestrator.root).current_active()
        if active:
            return active.get("scan_id")
        with self.lock:
            for scan_id, future in self.futures.items():
                if not future.done():
                    return scan_id
        return None

    def running_scan_ids(self) -> list[str]:
        with self.lock:
            return [scan_id for scan_id, future in self.futures.items() if not future.done()]

    def request_stop(self, scan_id: str) -> bool:
        with self.lock:
            future = self.futures.get(scan_id)
            stop_event = self.stop_events.get(scan_id)
            if future is None or future.done() or stop_event is None:
                return False
            stop_event.set()
            return True

    def can_cancel(self, scan_id: str) -> bool:
        with self.lock:
            future = self.futures.get(scan_id)
            return bool(
                future is not None
                and not future.done()
                and scan_id in self.stop_events
            )

    def stop_requested(self, scan_id: str) -> bool:
        with self.lock:
            event = self.stop_events.get(scan_id)
            return bool(event and event.is_set())

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
