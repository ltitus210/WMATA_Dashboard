from __future__ import annotations

from datetime import datetime, timedelta
import logging
import threading
from zoneinfo import ZoneInfo

from .vehicles.state_tracker import VehicleStateTracker
from .wmata.client import WMATAClient, WMATAError

LOG = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")


def schedule_service_dates(now_local: datetime) -> set[str]:
    """Current and previous dates cover service that crosses local midnight."""
    return {(now_local + timedelta(days=offset)).date().isoformat() for offset in (-1, 0)}


class PollingManager:
    def __init__(self, app, db, client: WMATAClient, settings):
        self.app, self.db, self.client, self.settings = app, db, client, settings
        self.tracker = VehicleStateTracker(db)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="wmata-poller", daemon=True)
        self.running = False

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 5) -> None:
        """Ask the polling loop to exit and wait briefly for a clean stop."""
        self.stop_event.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=timeout)
        LOG.info("Central WMATA poller stopped")

    def _run(self) -> None:
        self.running = True
        LOG.info("Central WMATA poller started")
        while not self.stop_event.is_set():
            try:
                self.poll_once()
                self.db.set_meta("polling_status", "healthy")
                self.db.delete_meta("polling_error")
            except Exception as exc:  # polling must survive malformed upstream data
                LOG.exception("Polling cycle failed")
                self.db.set_meta("polling_status", "degraded")
                self.db.set_meta("polling_error", str(exc))
            self.stop_event.wait(self.settings.poll_interval)
        self.running = False

    def poll_once(self, force: bool = False) -> None:
        entries = self.db.rows("SELECT * FROM entries WHERE enabled=1 ORDER BY profile_id,position")
        bus_entries, rail_entries = [e for e in entries if e["mode"] == "bus"], [e for e in entries if e["mode"] == "rail"]
        stops_by_id = {}
        if bus_entries:
            try:
                stops_by_id = {str(x["StopID"]): x for x in self.client.stops(force)["payload"]}
                if any(self.db.one("SELECT show_occupancy FROM profiles WHERE id=?", (e["profile_id"],))["show_occupancy"] for e in bus_entries):
                    self.client.bus_occupancy(force)
            except WMATAError:
                pass
        now_local = datetime.now(EASTERN)
        service_dates = schedule_service_dates(now_local)
        for stop_id in {e["location_id"] for e in bus_entries}:
            prediction_cache = self.client.predictions(stop_id, force)
            predictions = prediction_cache["payload"].get("Predictions", []) if isinstance(prediction_cache["payload"], dict) else []
            for service_date in service_dates:
                self.client.stop_schedule(stop_id, service_date, force)
            for route in {e["route"] for e in bus_entries if e["location_id"] == stop_id and e["route"]}:
                position_cache = self.client.positions(route, force)
                positions = position_cache["payload"]
                stop = stops_by_id.get(stop_id)
                if not stop:
                    continue
                prediction_keys = {(str(p.get("TripID", "")), str(p.get("VehicleID", ""))): p for p in predictions}
                for position in positions:
                    key = (str(position.get("TripID", "")), str(position.get("VehicleID", "")))
                    pred = prediction_keys.get(key)
                    if str(position.get("RouteID", "")) == route:
                        self.tracker.observe(stop, position, pred.get("Minutes") if pred else None, pred is not None)
        for codes in {e["location_id"] for e in rail_entries}:
            self.client.rail_predictions(codes, force)
        self.tracker.expire()
        self.db.set_meta("last_poll_completed", datetime.now().astimezone().isoformat())

    def force_refresh(self) -> None:
        self.poll_once(force=True)
