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


def test_stop_discovery_includes_express_route_without_current_service(tmp_path):
    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    client = WMATAClient("test-key", db)
    client.stops = lambda force=False: {"payload": [{
        "StopID": "1002006", "Name": "Georgia Av NW+Irving St NW", "Routes": ["D40", "D4X"]
    }]}
    client.stop_schedule = lambda stop_id, date, force=False: {"payload": []}
    client.predictions = lambda stop_id, force=False: {"payload": {"Predictions": [{
        "RouteID": "D40", "DirectionNum": "0", "DirectionText": "North to Silver Spring"
    }]}}

    result = client.discover_stop("1002006")
    assert result["variants"] == [
        {"route": "D40", "direction": "0", "destination": "North to Silver Spring",
         "direction_text": "North to Silver Spring"},
        {"route": "D4X", "direction": "", "destination": "", "direction_text": "",
         "provisional": True},
    ]


def test_stop_discovery_keeps_express_details_seen_during_prior_service(tmp_path):
    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    client = WMATAClient("test-key", db)
    client.stops = lambda force=False: {"payload": [{
        "StopID": "1001986", "Name": "Georgia Av NW+Columbia Rd NW", "Routes": ["D40", "D4X"]
    }]}
    client.stop_schedule = lambda stop_id, date, force=False: {"payload": []}
    client.predictions = lambda stop_id, force=False: {"payload": {"Predictions": [{
        "RouteID": "D4X", "DirectionNum": "1", "DirectionText": "South to Archives"
    }]}}
    client.discover_stop("1001986")

    client.predictions = lambda stop_id, force=False: {"payload": {"Predictions": []}}
    result = client.discover_stop("1001986")
    assert result["variants"] == [
        {"route": "D40", "direction": "", "destination": "", "direction_text": "",
         "provisional": True},
        {"route": "D4X", "direction": "1", "destination": "South to Archives",
         "direction_text": "South to Archives"},
    ]
    assert db.get_cache("bus_options", "1001986")["expired"] is False


def test_route_only_bus_entry_accepts_express_prediction(tmp_path):
    from app.predictions.engine import merge_bus

    entry = {"route": "D4X", "direction": "", "destination": ""}
    profile = {"arrival_count": 3, "stale_threshold": 120, "near_format": "DUE",
               "minute_format": "m", "scheduled_format": "superscript"}
    predictions = [{"RouteID": "D4X", "DirectionNum": "1", "DirectionText": "South to Archives",
                    "Minutes": 8, "TripID": "express-trip", "VehicleID": "express-bus"}]

    arrivals = merge_bus(entry, profile, predictions, [], [], cache_age=10)
    assert len(arrivals) == 1
    assert arrivals[0]["route"] == "D4X"
    assert arrivals[0]["destination"] == "South to Archives"


def test_station_discovery_builds_and_accumulates_cached_service_options(tmp_path):
    db = Database(str(tmp_path / "client.sqlite3"))
    db.initialize()
    client = WMATAClient("test-key", db)
    client.stations = lambda force=False: {"payload": [{
        "Code": "E04", "Name": "Columbia Heights", "LineCode1": "GR", "LineCode2": "YL"
    }]}
    client.rail_predictions = lambda code, force=False: {"payload": [
        {"Line": "GR", "Group": "2", "DestinationName": "Branch Av"},
        {"Line": "No", "Group": "1", "DestinationName": "No Passenger"},
    ]}

    first = client.discover_station("e04")
    assert first["lines"] == ["GR", "YL"]
    assert first["variants"] == [{"line": "GR", "group": "2", "destination": "Branch Av"}]

    client.rail_predictions = lambda code, force=False: {"payload": [
        {"Line": "YL", "Group": "2", "DestinationName": "Huntington"}
    ]}
    second = client.discover_station("E04")
    assert second["variants"] == [
        {"line": "GR", "group": "2", "destination": "Branch Av"},
        {"line": "YL", "group": "2", "destination": "Huntington"},
    ]
    assert db.get_cache("rail_options", "E04")["expired"] is False
