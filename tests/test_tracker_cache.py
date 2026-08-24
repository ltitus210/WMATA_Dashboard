from datetime import UTC, datetime, timedelta

from app.database import Database
from app.vehicles.state_tracker import VehicleStateTracker, distance_m


def make_db(tmp_path):
    database = Database(str(tmp_path / "test.sqlite3"))
    database.initialize()
    return database


def test_cache_expiration(tmp_path):
    db = make_db(tmp_path)
    db.put_cache("test", "one", {"ok": True}, -1, "mock")
    assert db.get_cache("test", "one")["expired"] is True


def test_profile_defaults_are_independent(tmp_path):
    db = make_db(tmp_path)
    db.seed_default_profile()
    home = db.profile("home")
    assert home["arrival_count"] == 3 and home["layout"] == "row"


def test_operational_metadata_can_be_cleared_after_recovery(tmp_path):
    db = make_db(tmp_path)
    db.set_meta("polling_error", "temporary failure")
    assert db.meta()["polling_error"] == "temporary failure"
    db.delete_meta("polling_error")
    assert "polling_error" not in db.meta()


def test_distance_and_state_thresholds():
    assert distance_m(38.9, -77.0, 38.9, -77.0) == 0
    assert VehicleStateTracker.classify(30) == "at_stop"
    assert VehicleStateTracker.classify(80) == "near_stop"
    assert VehicleStateTracker.classify(200, 80) == "passed"


def test_passage_requires_prediction_disappearance(tmp_path):
    db = make_db(tmp_path)
    tracker = VehicleStateTracker(db)
    stop = {"StopID":"1000001","Lat":38.9,"Lon":-77.0}
    near = {"VehicleID":"1","TripID":"t","RouteID":"S2","Lat":38.9002,"Lon":-77.0}
    far = {**near,"Lat":38.902}
    tracker.observe(stop, near, 0.5, True)
    not_passed = tracker.observe(stop, far, 0, True)
    assert not_passed["state"] == "approaching"


def test_conservative_passage_and_last_bus(tmp_path):
    db = make_db(tmp_path)
    tracker = VehicleStateTracker(db)
    stop = {"StopID":"1000001","Lat":38.9,"Lon":-77.0}
    near = {"VehicleID":"1","TripID":"t","RouteID":"S2","TripHeadsign":"Federal Triangle","Lat":38.9002,"Lon":-77.0}
    far = {**near,"Lat":38.902}
    tracker.observe(stop, near, 0.5, True)
    result = tracker.observe(stop, far, None, False)
    assert result["state"] == "passed" and result["confidence"] == "high"
    last = tracker.last_bus("1000001", "S2", "Federal Triangle")
    assert last is not None and last["trip_id"] == "t"


def test_last_bus_matches_direction_prefix_to_wmata_headsign(tmp_path):
    db = make_db(tmp_path)
    now = datetime.now(UTC).isoformat()
    for trip, headsign, direction in (("wrong", "FORT TOTTEN", "0"),
                                      ("right", "FEDERAL TRIANGLE", "1")):
        db.execute(
            """INSERT INTO vehicle_observations(vehicle_id,trip_id,route,headsign,direction,stop_id,
               observed_at,state,inferred_passage_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (trip, trip, "D44", headsign, direction, "1003048", now, "passed", now),
        )
    last = VehicleStateTracker(db).last_bus(
        "1003048", "D44", "South to Federal Triangle", "1"
    )
    assert last is not None and last["trip_id"] == "right"
