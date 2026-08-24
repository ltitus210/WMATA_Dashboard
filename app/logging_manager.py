from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from pathlib import Path
import re
import threading
import time


LOG_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d{3})?\s")


def format_bytes(size: int) -> str:
    """Format a byte count using binary-scaled digital storage units."""
    units = ("bytes", "KiB", "MiB", "GiB", "TiB")
    value = float(max(0, size))
    unit = units[0]
    for candidate in units[1:]:
        if value < 1024:
            break
        value /= 1024
        unit = candidate
    if unit == "bytes":
        return f"{int(value)} bytes"
    rendered = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{rendered} {unit}"


class RetentionRotatingFileHandler(RotatingFileHandler):
    """Size-rotating handler that also removes records older than its retention window."""

    def __init__(self, *args, retention_hours: int = 24, **kwargs):
        self.retention = timedelta(hours=retention_hours)
        self._next_prune = 0.0
        super().__init__(*args, **kwargs)

    def _log_files(self) -> list[Path]:
        path = Path(self.baseFilename)
        pattern = re.compile(rf"^{re.escape(path.name)}(?:\.\d+)?$")
        return sorted(item for item in path.parent.iterdir()
                      if item.is_file() and pattern.fullmatch(item.name))

    def prune(self, force: bool = False) -> int:
        now = time.monotonic()
        if not force and now < self._next_prune:
            return 0
        self._next_prune = now + 60
        cutoff = datetime.now() - self.retention
        active = Path(self.baseFilename)
        if self.stream:
            self.flush()
            self.stream.close()
            self.stream = None
        removed_lines = 0
        for path in self._log_files():
            default_keep = datetime.fromtimestamp(path.stat().st_mtime) >= cutoff
            keep = default_keep
            retained: list[str] = []
            with path.open("r", encoding="utf-8", errors="replace") as source:
                for line in source:
                    match = LOG_TIMESTAMP.match(line)
                    if match:
                        try:
                            keep = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") >= cutoff
                        except ValueError:
                            keep = default_keep
                    if keep:
                        retained.append(line)
                    else:
                        removed_lines += 1
            if retained or path == active:
                path.write_text("".join(retained), encoding="utf-8")
                path.chmod(0o600)
            else:
                path.unlink()
        if not active.exists():
            active.touch(mode=0o600)
        self.stream = self._open()
        return removed_lines

    def emit(self, record) -> None:
        self.prune()
        super().emit(record)


class LogManager:
    """Own the application's rotating file handler and its exact log family."""

    def __init__(self, path: str, max_bytes: int, backup_count: int, level: str):
        self.path = Path(path).resolve()
        self.max_bytes = max(65536, max_bytes)
        self.backup_count = max(1, backup_count)
        self.level = getattr(logging, level.upper(), logging.INFO)
        self._lock = threading.RLock()
        self._handler: RotatingFileHandler | None = None
        self._configure_handler()

    def _configure_handler(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        root = logging.getLogger()
        for existing in list(root.handlers):
            if getattr(existing, "_wmata_dashboard_handler", False):
                root.removeHandler(existing)
                existing.close()
        handler = RetentionRotatingFileHandler(
            self.path, maxBytes=self.max_bytes, backupCount=self.backup_count,
            encoding="utf-8", delay=False, retention_hours=24,
        )
        handler.setLevel(self.level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(handler, "_wmata_dashboard_handler", True)
        root.addHandler(handler)
        self.path.chmod(0o600)
        self._handler = handler
        handler.acquire()
        try:
            handler.prune(force=True)
        finally:
            handler.release()

    def _log_files(self) -> list[Path]:
        pattern = re.compile(rf"^{re.escape(self.path.name)}(?:\.\d+)?$")
        return sorted(
            candidate for candidate in self.path.parent.iterdir()
            if candidate.is_file() and pattern.fullmatch(candidate.name)
        )

    def status(self) -> dict:
        with self._lock:
            if self._handler:
                self._handler.acquire()
                try:
                    self._handler.prune(force=True)
                finally:
                    self._handler.release()
        files = self._log_files()
        size = sum(item.stat().st_size for item in files)
        return {"filename": self.path.name, "count": len(files),
                "bytes": size, "size": format_bytes(size), "retention_hours": 24}

    def purge(self) -> int:
        """Close logging, remove only the active log family, then reopen it."""
        with self._lock:
            root = logging.getLogger()
            if self._handler:
                root.removeHandler(self._handler)
                self._handler.flush()
                self._handler.close()
                self._handler = None
            files = self._log_files()
            for item in files:
                item.unlink()
            self._configure_handler()
            return len(files)
