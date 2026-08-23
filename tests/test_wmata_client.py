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

