from google.transit import gtfs_realtime_pb2

from app.database import Database
from app.wmata.client import WMATAClient


class FakeResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, content):
        self.content = content

    def get(self, *args, **kwargs):
        return FakeResponse(self.content)


class NoScheduleResponse:
    status_code = 400

    def json(self):
        return {"Message": "No schedule data available for this date."}

    def raise_for_status(self):
        raise AssertionError("Expected no-schedule response to be handled before raise_for_status")


class NoScheduleSession:
    def get(self, *args, **kwargs):
        return NoScheduleResponse()


def test_gtfs_occupancy_is_parsed_and_missing_value_is_omitted(tmp_path):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    occupied = feed.entity.add()
    occupied.id = "1"
    occupied.vehicle.trip.trip_id = "trip-1"
    occupied.vehicle.vehicle.id = "bus-1"
    occupied.vehicle.occupancy_status = 1
    unknown = feed.entity.add()
    unknown.id = "2"
    unknown.vehicle.trip.trip_id = "trip-2"
    unknown.vehicle.vehicle.id = "bus-2"

    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    result = WMATAClient("test-key", db, FakeSession(feed.SerializeToString())).bus_occupancy()
    assert result["payload"][0]["occupancy_status"] == "MANY_SEATS_AVAILABLE"
    assert result["payload"][1]["occupancy_status"] is None


def test_missing_schedule_response_is_cached_as_empty_data(tmp_path):
    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    result = WMATAClient("test-key", db, NoScheduleSession()).stop_schedule(
        "1003048", "2026-08-23"
    )
    assert result["payload"] == []
    assert result["http_status"] == 400
    assert result["last_error"] is None
    assert db.meta()["last_schedule_unavailable"]["key"] == "1003048:2026-08-23"


def test_stop_discovery_falls_back_to_live_prediction_variants(tmp_path):
    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    client = WMATAClient("test-key", db)
    client.stops = lambda force=False: {"payload": [{"StopID": "1003048", "Name": "11 St NW", "Routes": ["D44"]}]}
    client.stop_schedule = lambda stop_id, date, force=False: {"payload": []}
    client.predictions = lambda stop_id, force=False: {"payload": {"Predictions": [
        {"RouteID": "D44", "DirectionNum": "1", "DirectionText": "South to Federal Triangle"}
    ]}}

    result = client.discover_stop("1003048")
    assert result["schedule_available"] is False
    assert result["variants"] == [{"route": "D44", "direction": "1",
                                    "destination": "South to Federal Triangle",
                                    "direction_text": "South to Federal Triangle"}]
