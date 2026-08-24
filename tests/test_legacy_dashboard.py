from pathlib import Path

from app import create_app
from app.config import Settings


def settings(database_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def test_legacy_dashboard_is_server_rendered_and_self_refreshing(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    response = app.test_client().get("/dashboard/home/legacy")

    assert response.status_code == 200
    assert b'http-equiv="refresh" content="15"' in response.data
    assert b"legacy-dashboard.css" in response.data
    assert b"<script" not in response.data
    assert response.headers["Cache-Control"].startswith("no-store")


def test_admin_links_to_legacy_dashboard(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    response = app.test_client().get("/admin")
    assert b"Legacy iPad" in response.data
    assert b"/dashboard/home/legacy" in response.data
