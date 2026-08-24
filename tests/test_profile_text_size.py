from pathlib import Path

from app import create_app
from app.config import Settings


def settings(database_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def profile_form(text_size: str) -> dict[str, str]:
    return {
        "name": "Home", "display_type": "lcd", "theme": "dark", "layout": "row",
        "text_size": text_size, "minute_format": "m", "near_format": "DUE",
        "scheduled_format": "superscript", "stale_format": "?", "arrival_count": "3",
        "stale_threshold": "120", "refresh_interval": "15", "show_bus": "on",
        "show_rail": "on", "show_legend": "on", "show_occupancy": "on",
    }


def test_profile_has_five_text_sizes_and_medium_default(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()

    response = browser.get("/admin")
    assert response.status_code == 200
    for value, label in (("extra_small", "Extra Small"), ("small", "Small"),
                         ("medium", "Medium"), ("large", "Large"),
                         ("extra_large", "Extra Large")):
        assert f'value="{value}"'.encode() in response.data
        assert label.encode() in response.data
    assert b'value="medium" selected' in response.data


def test_profile_text_size_updates_modern_and_legacy_dashboards(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()
    database = app.extensions["database"]
    profile = database.profile("home")

    response = browser.post(f'/admin/profiles/{profile["id"]}', data=profile_form("extra_large"))
    assert response.status_code == 302
    assert database.profile("home")["text_size"] == "extra_large"
    assert b"text-size-extra-large" in browser.get("/dashboard/home").data
    assert b"text-size-extra-large" in browser.get("/dashboard/home/legacy").data


def test_profile_rejects_unknown_text_size(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()
    profile = app.extensions["database"].profile("home")

    response = browser.post(f'/admin/profiles/{profile["id"]}', data=profile_form("huge"))
    assert response.status_code == 400
