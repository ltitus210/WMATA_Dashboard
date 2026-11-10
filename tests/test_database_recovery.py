from pathlib import Path

from app.database import Database, utcnow


def test_startup_recovery_preserves_durable_data_and_resets_transient_tables(tmp_path, monkeypatch):
    path = tmp_path / "dashboard.sqlite3"
    database = Database(str(path))
    database.initialize()
    database.seed_default_profile()
    profile = database.profile("home")
    now = utcnow().isoformat()
    database.execute(
        """INSERT INTO entries(profile_id,position,mode,location_id,location_name,route,
           direction,destination,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (profile["id"], 0, "bus", "1000001", "Test stop", "D40", "0",
         "Test destination", now, now),
    )
    database.set_secret("wmata_api_key", "private-test-key")
    database.set_meta("durable-setting", {"enabled": True})
    database.put_cache("predictions", "1000001", {"Predictions": []}, 30, "test")
    database.execute(
        """INSERT INTO vehicle_observations(stop_id,observed_at,state)
           VALUES(?,?,?)""",
        ("1000001", now, "approaching"),
    )

    monkeypatch.setattr(database, "_database_is_healthy", lambda: False)
    database.initialize()

    assert database.profile("home")["name"] == "Home"
    assert len(database.entries(profile["id"])) == 1
    assert database.get_secret("wmata_api_key") == "private-test-key"
    assert database.meta()["durable-setting"] == {"enabled": True}
    assert database.one("SELECT COUNT(*) AS count FROM cache_entries")["count"] == 0
    assert database.one("SELECT COUNT(*) AS count FROM vehicle_observations")["count"] == 0
    assert database.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert database.one("PRAGMA synchronous")["synchronous"] == 2
    assert database.recovery_backup
    assert Path(database.recovery_backup).is_file()
    assert Path(database.recovery_backup).stat().st_mode & 0o777 == 0o600
