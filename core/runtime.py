import json
import os
from datetime import datetime, timedelta
from pathlib import Path


class RuntimeLock:
    """Single-flight lock shared by the Streamlit process and restarts.

    The lock file is also the heartbeat for the active scan. It is written with
    O_EXCL, so two processes cannot both start a scan. A lock older than the TTL
    is treated as stale and can be taken over after a crash or restart.
    """

    def __init__(self, root: Path, name: str = "engine", ttl_seconds: int = 600):
        self.path = Path(root) / "runtime" / f"{name}.lock"
        self.ttl_seconds = ttl_seconds

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
        for _ in range(3):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self.read()
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
                        "started_at": now,
                        "heartbeat_at": now,
                    },
                    handle,
                    ensure_ascii=False,
                )
            return True
        return False

    def heartbeat(self, scan_id: str) -> None:
        info = self.read()
        if not info or info.get("scan_id") != scan_id or info.get("pid") != os.getpid():
            return
        info["heartbeat_at"] = datetime.now().astimezone().isoformat()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def release(self, scan_id: str) -> None:
        info = self.read()
        if not info or info.get("scan_id") != scan_id or info.get("pid") != os.getpid():
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def touch_runtime(root: Path, scan_id: str) -> None:
    """Refresh the heartbeat without letting lock errors break persistence."""
    try:
        RuntimeLock(root).heartbeat(scan_id)
    except Exception:
        pass
