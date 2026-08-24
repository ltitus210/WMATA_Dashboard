from pathlib import Path

from app import create_app
from app.config import Settings


def settings(database_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def test_configuration_and_diagnostics_include_persistent_theme_toggle(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()

    for path in ("/admin", "/diagnostics"):
        response = browser.get(path)
        assert response.status_code == 200
        assert b"theme-toggle" in response.data
        assert b'role="switch"' in response.data
        assert b"<span>Light</span><span>Dark</span>" in response.data
        assert b"admin-theme.js" in response.data
        assert b"wmata-admin-theme" in response.data

    stylesheet = browser.get("/static/style.css").data
    assert b"background:var(--ink);color:var(--paper)" in stylesheet
    assert b".danger-button{background:#a92518;color:#fff}" in stylesheet
