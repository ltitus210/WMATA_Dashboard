from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.polling import PollingManager, schedule_service_dates
from app.wmata.client import WMATAError


def test_polling_uses_current_and_previous_service_dates_only():
    eastern = ZoneInfo("America/New_York")
    dates = schedule_service_dates(datetime(2026, 8, 23, 23, 55, tzinfo=eastern))
    assert dates == {"2026-08-22", "2026-08-23"}
    assert "2026-08-24" not in dates


class PollingDatabase:
    def __init__(self):
        self.entries = [
            {"profile_id": 1, "mode": "bus", "location_id": "1001", "route": "D40"},
            {"profile_id": 1, "mode": "bus", "location_id": "1002", "route": "D40"},
        ]
        self.meta = {}

    def rows(self, _sql, _args=()):
        return self.entries

    def one(self, _sql, _args=()):
        return {"show_occupancy": 0}

    def set_meta(self, key, value):
        self.meta[key] = value


class ResilientClient:
    def __init__(self):
        self.position_calls = []
        self.schedule_calls = []

    def stations(self, force=False):
        return {"payload": []}

    def stops(self, force=False):
        return {"payload": [
            {"StopID": "1001", "Lat": 0, "Lon": 0},
            {"StopID": "1002", "Lat": 0, "Lon": 0},
        ]}

    def predictions(self, stop_id, force=False):
        if stop_id == "1001":
            raise WMATAError("rate limited", 429)
        return {"payload": {"Predictions": []}}

    def stop_schedule(self, stop_id, service_date, force=False):
        self.schedule_calls.append((stop_id, service_date, force))
        raise WMATAError("rate limited", 429)

    def positions(self, route, force=False):
        self.position_calls.append((route, force))
        return {"payload": []}


class Tracker:
    def __init__(self):
        self.expired = False

    def observe(self, *_args):
        pass

    def expire(self):
        self.expired = True


def test_polling_continues_after_rate_limit_and_fetches_each_route_once():
    database = PollingDatabase()
    client = ResilientClient()
    manager = PollingManager(None, database, client, SimpleNamespace(poll_interval=20))
    manager.tracker = Tracker()

    manager.poll_once(force=True)

    assert len(client.schedule_calls) == 4
    assert client.position_calls == [("D40", True)]
    assert manager.tracker.expired is True
    assert "last_poll_completed" in database.meta

