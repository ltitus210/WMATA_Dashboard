from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    bind: str
    port: int
    database_path: str
    admin_user: str
    admin_password: str
    log_level: str
    poll_interval: int
    start_poller: bool
    secret_key: str
    log_file: str = ""
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        database = Path(os.getenv("WMATA_DATABASE", "instance/wmata-dashboard.sqlite3"))
        log_file = Path(os.getenv("WMATA_LOG_FILE", "instance/logs/wmata-dashboard.log"))
        return cls(
            api_key=os.getenv("WMATA_API_KEY", "").strip(),
            bind=os.getenv("WMATA_BIND", "0.0.0.0"),
            port=int(os.getenv("WMATA_PORT", "8080")),
            database_path=str(database.resolve()),
            admin_user=os.getenv("WMATA_ADMIN_USER", ""),
            admin_password=os.getenv("WMATA_ADMIN_PASSWORD", ""),
            log_level=os.getenv("WMATA_LOG_LEVEL", "INFO"),
            poll_interval=max(10, int(os.getenv("WMATA_POLL_INTERVAL", "20"))),
            start_poller=_bool("WMATA_START_POLLER", True),
            secret_key=os.getenv("WMATA_SECRET_KEY", "dev-only-change-me"),
            log_file=str(log_file.resolve()),
            log_max_bytes=max(65536, int(os.getenv("WMATA_LOG_MAX_BYTES", "2000000"))),
            log_backup_count=max(1, int(os.getenv("WMATA_LOG_BACKUP_COUNT", "3"))),
        )
