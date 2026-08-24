import logging
from datetime import datetime, timedelta
from pathlib import Path

from app import create_app
from app.config import Settings
from app.logging_manager import format_bytes


def settings(database_path: Path, log_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret", str(log_path), 65536, 3)


def test_log_purge_removes_only_application_log_family(tmp_path):
    log_path = tmp_path / "logs" / "wmata-dashboard.log"
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", log_path),
    })
    logging.getLogger("purge-test").warning("old sensitive diagnostic marker")
    for handler in logging.getLogger().handlers:
        handler.flush()
    rotated = log_path.with_name(log_path.name + ".1")
    rotated.write_text("old rotated content", encoding="utf-8")
    unrelated = log_path.parent / "keep.txt"
    unrelated.write_text("do not delete", encoding="utf-8")

    manager = app.extensions["log_manager"]
    assert manager.status()["count"] == 2
    browser = app.test_client()
    assert browser.get("/admin/logs/purge").status_code == 405
    response = browser.post("/admin/logs/purge")

    assert response.status_code == 302
    assert not rotated.exists()
    assert unrelated.read_text(encoding="utf-8") == "do not delete"
    assert log_path.exists()
    assert "old sensitive diagnostic marker" not in log_path.read_text(encoding="utf-8")
    assert app.extensions["database"].meta()["last_log_purge"]["files_removed"] == 2


def test_admin_displays_log_purge_control(tmp_path):
    log_path = tmp_path / "logs" / "wmata-dashboard.log"
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", log_path),
    })
    page = app.test_client().get("/admin")
    assert b"Purge log files" in page.data
    assert b"wmata-dashboard.log" in page.data
    assert b"Database" in page.data
    assert b"Cached payloads" in page.data
    assert b"last 24 hours" in page.data


def test_log_manager_prunes_records_older_than_24_hours(tmp_path):
    log_path = tmp_path / "logs" / "wmata-dashboard.log"
    log_path.parent.mkdir(parents=True)
    old = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    log_path.write_text(
        f"{old},000 INFO old: remove me\nold traceback continuation\n"
        f"{recent},000 INFO recent: keep me\nrecent continuation\n",
        encoding="utf-8",
    )
    rotated = log_path.with_name(log_path.name + ".1")
    rotated.write_text(f"{old},000 INFO old: remove rotated\n", encoding="utf-8")

    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", log_path),
    })
    status = app.extensions["log_manager"].status()
    content = log_path.read_text(encoding="utf-8")

    assert "remove me" not in content
    assert "old traceback continuation" not in content
    assert "keep me" in content and "recent continuation" in content
    assert not rotated.exists()
    assert status["retention_hours"] == 24


def test_binary_storage_size_formatting():
    assert format_bytes(0) == "0 bytes"
    assert format_bytes(1023) == "1023 bytes"
    assert format_bytes(1024) == "1 KiB"
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(1024 * 1024) == "1 MiB"
