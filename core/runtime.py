import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4
from contextlib import contextmanager

from .errors import RuntimeLeaseLostError


_WRITE_LOCK = Lock()


@contextmanager
def _interprocess_file_lock(path: Path):
    """Serialize lease transitions and guarded commits across worker processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RuntimeLock:
    """Single-flight lock shared by the Streamlit process and restarts.

    The lock file is also the heartbeat for the active scan. It is written with
    O_EXCL, so two processes cannot both start a scan. A lock older than the TTL
    is treated as stale and can be taken over after a crash or restart.
    """

    def __init__(self, root: Path, name: str = "engine", ttl_seconds: int = 600):
        self.path = Path(root) / "runtime" / f"{name}.lock"
        self.guard_path = self.path.with_suffix(self.path.suffix + ".guard")
        self.ttl_seconds = ttl_seconds

    @contextmanager
    def exclusive(self):
        with _WRITE_LOCK:
            with _interprocess_file_lock(self.guard_path):
                yield

    def read(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def is_stale(self, info: dict | None = None) -> bool:
        info = info if info is not None else self.read()
        if not info:
            return True
        try:
            heartbeat = datetime.fromisoformat(str(info.get("heartbeat_at", "")))
        except ValueError:
            try:
                heartbeat = datetime.fromtimestamp(self.path.stat().st_mtime).astimezone()
            except OSError:
                return True
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.astimezone()
        return datetime.now().astimezone() - heartbeat > timedelta(seconds=self.ttl_seconds)

    def current_active(self) -> dict | None:
        info = self.read()
        if info and not self.is_stale(info):
            return info
        return None

    def acquire(self, scan_id: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.exclusive():
            for _ in range(3):
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    info = self.read()
                    if info is None:
                        # A fresh malformed lock may be in its initialization
                        # window. Leave it alone instead of stealing it.
                        try:
                            age = (
                                datetime.now().astimezone()
                                - datetime.fromtimestamp(self.path.stat().st_mtime).astimezone()
                            ).total_seconds()
                        except OSError:
                            return False
                        if age < min(self.ttl_seconds, 30):
                            return False
                    if info and not self.is_stale(info):
                        return False
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                now = datetime.now().astimezone().isoformat()
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "pid": os.getpid(),
                            "scan_id": scan_id,
                            "lease_id": uuid4().hex,
                            "started_at": now,
                            "heartbeat_at": now,
                            "stage": "queued",
                            "completed": 0,
                            "total": 0,
                        },
                        handle,
                        ensure_ascii=False,
                    )
                return True
            return False

    def owns(self, scan_id: str, lease_id: str | None = None) -> bool:
        info = self.read()
        return bool(
            info
            and info.get("scan_id") == scan_id
            and info.get("pid") == os.getpid()
            and (lease_id is None or info.get("lease_id") == lease_id)
        )

    def heartbeat(self, scan_id: str, lease_id: str | None = None) -> bool:
        with self.exclusive():
            info = self.read()
            if (
                not info
                or info.get("scan_id") != scan_id
                or info.get("pid") != os.getpid()
                or (lease_id is not None and info.get("lease_id") != lease_id)
            ):
                return False
            info["heartbeat_at"] = datetime.now().astimezone().isoformat()
            self._write(info)
            return True

    def update_progress(
        self,
        scan_id: str,
        stage: str,
        completed: int,
        total: int,
        lease_id: str | None = None,
    ) -> bool:
        with self.exclusive():
            info = self.read()
            if (
                not info
                or info.get("scan_id") != scan_id
                or info.get("pid") != os.getpid()
                or (lease_id is not None and info.get("lease_id") != lease_id)
            ):
                return False
            info.update(
                stage=stage,
                completed=max(0, int(completed)),
                total=max(0, int(total)),
                heartbeat_at=datetime.now().astimezone().isoformat(),
            )
            self._write(info)
            return True

    def _write(self, info: dict) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def release(self, scan_id: str, lease_id: str | None = None) -> bool:
        with self.exclusive():
            info = self.read()
            if (
                not info
                or info.get("scan_id") != scan_id
                or info.get("pid") != os.getpid()
                or (lease_id is not None and info.get("lease_id") != lease_id)
            ):
                return False
            try:
                self.path.unlink()
            except FileNotFoundError:
                return False
            return True


class RuntimeLeaseGuard:
    """Fence task writes after another run takes over the runtime lock."""

    def __init__(self, runtime: RuntimeLock, scan_id: str, lease_id: str):
        self.runtime = runtime
        self.scan_id = scan_id
        self.lease_id = lease_id
        self.lost = False

    def assert_owned(self) -> None:
        if self.lost or not self.runtime.owns(self.scan_id, self.lease_id):
            self.lost = True
            raise RuntimeLeaseLostError(
                f"Runtime lease for {self.scan_id} is no longer owned by this task"
            )

    def heartbeat(self) -> bool:
        if self.lost or not self.runtime.heartbeat(self.scan_id, self.lease_id):
            self.lost = True
            return False
        return True

    @contextmanager
    def protect_write(self):
        """Hold the cross-process lease mutex through a final atomic commit."""
        with self.runtime.exclusive():
            if self.lost or not self.runtime.owns(self.scan_id, self.lease_id):
                self.lost = True
                raise RuntimeLeaseLostError(
                    f"Runtime lease for {self.scan_id} is no longer owned by this task"
                )
            yield


def touch_runtime(root: Path, scan_id: str) -> None:
    """Refresh the heartbeat without letting lock errors break persistence."""
    try:
        RuntimeLock(root).heartbeat(scan_id)
    except Exception:
        pass


def update_runtime_progress(root: Path, scan, lease_id: str | None = None) -> None:
    try:
        updated = RuntimeLock(root).update_progress(
            scan.scan_id,
            str(scan.progress.stage),
            scan.progress.completed,
            scan.progress.total,
            lease_id=lease_id,
        )
        if lease_id is not None and not updated:
            raise RuntimeLeaseLostError(
                f"Runtime lease for {scan.scan_id} was lost while saving progress"
            )
    except RuntimeLeaseLostError:
        raise
    except Exception:
        pass
