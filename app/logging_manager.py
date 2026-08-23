from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading


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
        handler = RotatingFileHandler(
            self.path, maxBytes=self.max_bytes, backupCount=self.backup_count,
            encoding="utf-8", delay=False,
        )
        handler.setLevel(self.level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(handler, "_wmata_dashboard_handler", True)
        root.addHandler(handler)
        self.path.chmod(0o600)
        self._handler = handler

    def _log_files(self) -> list[Path]:
        pattern = re.compile(rf"^{re.escape(self.path.name)}(?:\.\d+)?$")
        return sorted(
            candidate for candidate in self.path.parent.iterdir()
            if candidate.is_file() and pattern.fullmatch(candidate.name)
        )

    def status(self) -> dict:
        files = self._log_files()
        return {"filename": self.path.name, "count": len(files),
                "bytes": sum(item.stat().st_size for item in files)}

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
