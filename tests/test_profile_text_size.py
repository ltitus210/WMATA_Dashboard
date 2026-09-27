from pathlib import Path

from app import create_app
from app.config import Settings


def settings(database_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def profile_form(text_size: str, card_columns: str = "1", layout: str = "row") -> dict[str, str]:
    return {
        "name": "Home", "display_type": "lcd", "theme": "dark", "layout": layout,
        "card_columns": card_columns,
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


def test_stop_cards_support_one_to_five_columns_and_lcd_scrolling(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()
    database = app.extensions["database"]
    profile = database.profile("home")
    assert profile["card_columns"] == 1

    admin = browser.get("/admin").data
    assert b'name="card_columns"' in admin
    for columns in range(1, 6):
        assert f'value="{columns}"'.encode() in admin

    response = browser.post(f'/admin/profiles/{profile["id"]}',
                            data=profile_form("medium", "5", "card"))
    assert response.status_code == 302
    assert database.profile("home")["card_columns"] == 5
    dashboard = browser.get("/dashboard/home").data
    assert b"layout-card card-columns-5" in dashboard

    stylesheet = browser.get("/static/style.css").data
    assert b".layout-card.card-columns-5 .cards" in stylesheet
    assert b".layout-card .times{justify-content:flex-end;flex-wrap:wrap;text-align:right}" in stylesheet
    assert b".dashboard.display-lcd{overflow-x:hidden;overflow-y:auto}" in stylesheet
    assert b"@media screen and (max-width:600px) and (orientation:portrait)" in stylesheet
    assert b".dashboard .destination{white-space:normal" in stylesheet
    assert b".dashboard .times,.dashboard.layout-card .times{justify-content:flex-start" in stylesheet


def test_profile_rejects_invalid_stop_card_columns(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    browser = app.test_client()
    profile = app.extensions["database"].profile("home")

    for columns in ("0", "6", "many"):
        response = browser.post(f'/admin/profiles/{profile["id"]}',
                                data=profile_form("medium", columns, "card"))
        assert response.status_code == 400
