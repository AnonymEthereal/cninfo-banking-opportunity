from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .errors import RuntimeBusyError
from .models import ScanError
from .runtime import RuntimeLock
from .scanner import ScanOrchestrator


class TaskManager:
    def __init__(self, orchestrator: ScanOrchestrator, max_workers: int = 1, task_timeout: int | None = None):
        self.orchestrator = orchestrator
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan")
        self.lock = Lock()
        self.futures = {}
        self.task_timeout = task_timeout

    def submit(self, scan_id: str, agents, resources=None, retry_failed=False, retry_sources=False):
        with self.lock:
            existing = self.futures.get(scan_id)
            if existing is not None and not existing.done():
                self._close_resources(resources or [])
                return existing
            active = RuntimeLock(self.orchestrator.root).current_active()
            if active:
                self._close_resources(resources or [])
                raise RuntimeBusyError(
                    "ANOTHER_SCAN_RUNNING",
                    f"任务 {active.get('scan_id')} 正在运行，请等待其结束后再提交。",
                )
            future = self.executor.submit(
                self._run, scan_id, agents, resources or [], retry_failed, retry_sources
            )
            future.add_done_callback(lambda done: self._close_resources(resources or []))
            self.futures[scan_id] = future
            return future

    def _run(self, scan_id, agents, resources, retry_failed=False, retry_sources=False):
        runtime = RuntimeLock(self.orchestrator.root)
        acquired = runtime.acquire(scan_id)
        try:
            if not acquired:
                raise RuntimeBusyError(
                    "ANOTHER_SCAN_RUNNING",
                    "另一个任务正在运行，当前任务未启动。",
                )
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
            if scan.status == 'failed':
                scan.status = 'collecting' if scan.progress.stage in ('queued', 'collecting', 'collection_completed') else 'analyzing'
                self.orchestrator._save(scan)
            if scan.status in ('queued', 'collecting'):
                self.orchestrator.run_collection(scan_id)
                scan = self.orchestrator.load(scan_id)
                if scan.require_review and not scan.analysis_confirmed:
                    scan.status, scan.progress.stage = 'awaiting_review', 'awaiting_review'
                    self.orchestrator._save(scan)
                    return scan
                self.orchestrator.prepare_analysis(scan_id, retry_documents=retry_sources)
            elif scan.status == 'awaiting_review':
                if not scan.analysis_confirmed:
                    return scan
                self.orchestrator.prepare_analysis(scan_id, retry_documents=True)
            elif scan.progress.stage == 'documents':
                self.orchestrator.prepare_analysis(scan_id)
            self.orchestrator.run_agents(
                scan_id,
                *agents,
                per_announcement_timeout=scan.announcement_timeout,
                task_timeout=self.task_timeout or scan.analysis_timeout,
                retry_failed=retry_failed,
            )
            return self.orchestrator.run_export(scan_id)
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
            if acquired:
                runtime.release(scan_id)

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

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
