from pathlib import Path

from app import create_app
from app.config import Settings
from app.database import utcnow


def settings(database_path: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def test_widget_cards_are_draggable_and_order_is_persisted(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    database = app.extensions["database"]
    profile = database.profile("home")
    now = utcnow().isoformat()
    ids = []
    for position, stop_id in enumerate(("1001", "1002", "1003")):
        ids.append(database.execute(
            """INSERT INTO entries(profile_id,position,mode,location_id,location_name,route,direction,
               destination,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (profile["id"], position, "bus", stop_id, f"Stop {stop_id}", "D44", "1",
             "Federal Triangle", now, now),
        ))

    browser = app.test_client()
    admin = browser.get("/admin")
    assert admin.data.count(b'class="entry-widget"') == 3
    assert admin.data.count(b'draggable="true"') == 3

    reordered = [ids[2], ids[0], ids[1]]
    response = browser.post(
        f"/admin/profiles/{profile['id']}/entries/reorder", json={"entry_ids": reordered}
    )
    assert response.status_code == 200
    assert [row["id"] for row in database.entries(profile["id"])] == reordered

    invalid = browser.post(
        f"/admin/profiles/{profile['id']}/entries/reorder", json={"entry_ids": reordered[:-1]}
    )
    assert invalid.status_code == 400
