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
    assert b"LIVE ARRIVALS" in response.data
    assert b"DEPARTURES" not in response.data
    assert response.headers["Cache-Control"].startswith("no-store")


def test_admin_links_to_legacy_dashboard(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    response = app.test_client().get("/admin")
    assert b"Legacy Browser" in response.data
    assert b"/dashboard/home/legacy" in response.data
    assert response.data.count(b'target="_blank" rel="noopener"') == 6


def test_legacy_dashboard_respects_row_and_stop_card_layouts(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    database = app.extensions["database"]
    profile = database.profile("home")
    now = "2026-08-24T00:00:00+00:00"
    entries = [
        (0, "1001", "Shared stop", "D40"),
        (1, "1001", "Shared stop", "D4X"),
        (2, "1002", "Other stop", "C61"),
    ]
    for position, stop_id, name, route in entries:
        database.execute(
            """INSERT INTO entries(profile_id,position,mode,location_id,location_name,route,
               direction,destination,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (profile["id"], position, "bus", stop_id, name, route, "", "", now, now),
        )

    database.execute("UPDATE profiles SET layout='card',card_columns=3 WHERE id=?", (profile["id"],))
    cards = app.test_client().get("/dashboard/home/legacy")
    assert b"legacy-layout-card legacy-card-columns-3" in cards.data
    assert cards.data.count(b'class="legacy-card-shell"') == 2
    assert cards.data.count(b"<h2>Shared stop</h2>") == 1
    assert cards.data.count(b'class="legacy-row"') == 3

    database.execute("UPDATE profiles SET layout='row' WHERE id=?", (profile["id"],))
    rows = app.test_client().get("/dashboard/home/legacy")
    assert b"legacy-layout-row legacy-card-columns-3" in rows.data
    assert rows.data.count(b'class="legacy-card-shell"') == 3
    assert rows.data.count(b"<h2>Shared stop</h2>") == 2

    stylesheet = app.test_client().get("/static/legacy-dashboard.css").data
    assert b".legacy-layout-card.legacy-card-columns-5 .legacy-card-shell { width: 20%; }" in stylesheet
    assert b"float: left" in stylesheet
    assert b"text-align: right" in stylesheet
