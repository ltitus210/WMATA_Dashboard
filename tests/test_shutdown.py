from pathlib import Path
from threading import Event

from app import create_app
from app.config import Settings


def settings(database_path: Path, user: str = "", password: str = "") -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), user, password,
                    "INFO", 20, False, "test-secret")


def test_shutdown_button_and_post_only_handler(tmp_path):
    called = Event()
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3"),
        "SHUTDOWN_HANDLER": lambda application: called.set(),
    })
    browser = app.test_client()

    admin_page = browser.get("/admin")
    assert b"Stop dashboard" in admin_page.data
    assert browser.get("/admin/shutdown").status_code == 405

    response = browser.post("/admin/shutdown")
    assert response.status_code == 202
    assert b"Stopping cleanly" in response.data
    assert called.wait(timeout=2)


def test_shutdown_route_honors_admin_authentication(tmp_path):
    called = Event()
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", "admin", "correct-password"),
        "SHUTDOWN_HANDLER": lambda application: called.set(),
    })
    browser = app.test_client()

    assert browser.post("/admin/shutdown").status_code == 401
    assert not called.is_set()
    response = browser.post("/admin/shutdown", auth=("admin", "correct-password"))
    assert response.status_code == 202
    assert called.wait(timeout=2)

