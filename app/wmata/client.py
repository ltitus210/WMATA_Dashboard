from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any

import requests

from ..database import Database

LOG = logging.getLogger(__name__)


class WMATAError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class WMATAClient:
    BASE = "https://api.wmata.com"
    ENDPOINTS = {
        "stops": ("/Bus.svc/json/jStops", "Stops", 86400),
        "routes": ("/Bus.svc/json/jRoutes", "Routes", 86400),
        "stop_schedule": ("/Bus.svc/json/jStopSchedule", "StopSchedules", 21600),
        "route_details": ("/Bus.svc/json/jRouteDetails", None, 43200),
        "predictions": ("/NextBusService.svc/json/jPredictions", None, 30),
        "positions": ("/Bus.svc/json/jBusPositions", "BusPositions", 30),
        "stations": ("/Rail.svc/json/jStations", "Stations", 86400),
        "rail_predictions": ("/StationPrediction.svc/json/GetPrediction/{codes}", "Trains", 20),
    }

    def __init__(self, api_key: str, db: Database, session: requests.Session | None = None):
        self.api_key = api_key
        self.db = db
        self.session = session or requests.Session()

    def fetch(self, name: str, key: str, params: dict | None = None, force: bool = False) -> dict:
        cached = self.db.get_cache(name, key)
        if cached and not cached["expired"] and not force:
            return cached
        if not self.api_key:
            if cached:
                cached["last_error"] = "WMATA_API_KEY is not configured"
                return cached
            raise WMATAError("WMATA_API_KEY is not configured")
        path, payload_key, ttl = self.ENDPOINTS[name]
        if "{codes}" in path:
            path = path.format(codes=key)
        started = time.monotonic()
        try:
            response = self.session.get(
                self.BASE + path,
                params=params,
                headers={"api_key": self.api_key, "Accept": "application/json"},
                timeout=(4, 12),
            )
            latency = (time.monotonic() - started) * 1000
            response.raise_for_status()
            body = response.json()
            payload: Any = body.get(payload_key, []) if payload_key else body
            self.db.put_cache(name, key, payload, ttl, self.BASE + path, response.status_code, latency)
            self.db.set_meta("last_successful_wmata_request", datetime.now().astimezone().isoformat())
            self.db.set_meta("last_http_status", response.status_code)
            self.db.set_meta("last_latency_ms", round(latency, 1))
            result = self.db.get_cache(name, key)
            assert result is not None
            return result
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self.db.cache_error(name, key, str(exc), status)
            self.db.set_meta("last_failed_wmata_request", datetime.now().astimezone().isoformat())
            self.db.set_meta("last_error", str(exc))
            LOG.warning("WMATA %s request failed: %s", name, exc)
            if cached:
                cached["last_error"] = str(exc)
                return cached
            raise WMATAError(str(exc), status) from exc

    def stops(self, force: bool = False) -> dict:
        return self.fetch("stops", "all", force=force)

    def stations(self, force: bool = False) -> dict:
        return self.fetch("stations", "all", force=force)

    def predictions(self, stop_id: str, force: bool = False) -> dict:
        return self.fetch("predictions", stop_id, {"StopID": stop_id}, force)

    def stop_schedule(self, stop_id: str, date: str, force: bool = False) -> dict:
        return self.fetch("stop_schedule", f"{stop_id}:{date}", {"StopID": stop_id, "Date": date}, force)

    def positions(self, route: str, force: bool = False) -> dict:
        return self.fetch("positions", route, {"RouteID": route, "IncludingVariations": "true"}, force)

    def rail_predictions(self, codes: str, force: bool = False) -> dict:
        return self.fetch("rail_predictions", codes, force=force)

    def bus_occupancy(self, force: bool = False) -> dict:
        category, key = "occupancy", "bus"
        cached = self.db.get_cache(category, key)
        if cached and not cached["expired"] and not force:
            return cached
        if not self.api_key:
            if cached:
                return cached
            raise WMATAError("WMATA_API_KEY is not configured")
        started = time.monotonic()
        try:
            from google.transit import gtfs_realtime_pb2
            response = self.session.get(
                self.BASE + "/gtfs/bus-gtfsrt-vehiclepositions.pb",
                headers={"api_key": self.api_key, "Accept": "application/x-protobuf"}, timeout=(4, 12),
            )
            latency = (time.monotonic() - started) * 1000
            response.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            names = {0:"EMPTY",1:"MANY_SEATS_AVAILABLE",2:"FEW_SEATS_AVAILABLE",3:"STANDING_ROOM_ONLY",
                     4:"CRUSHED_STANDING_ROOM_ONLY",5:"FULL",6:"NOT_ACCEPTING_PASSENGERS"}
            records = []
            for entity in feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                vehicle = entity.vehicle
                records.append({"trip_id": vehicle.trip.trip_id, "route_id": vehicle.trip.route_id,
                                "vehicle_id": vehicle.vehicle.id, "timestamp": int(vehicle.timestamp or 0),
                                "occupancy_status": names.get(int(vehicle.occupancy_status))
                                if vehicle.HasField("occupancy_status") else None})
            self.db.put_cache(category, key, records, 30, self.BASE + "/gtfs/bus-gtfsrt-vehiclepositions.pb",
                              response.status_code, latency)
            result = self.db.get_cache(category, key)
            assert result is not None
            return result
        except (requests.RequestException, ValueError, ImportError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self.db.cache_error(category, key, str(exc), status)
            self.db.set_meta("occupancy_error", str(exc))
            if cached:
                cached["last_error"] = str(exc)
                return cached
            raise WMATAError(str(exc), status) from exc

    def discover_stop(self, stop_id: str, force: bool = False) -> dict:
        stops = self.stops(force)["payload"]
        stop = next((s for s in stops if str(s.get("StopID")) == str(stop_id)), None)
        if not stop:
            raise WMATAError(f"Stop {stop_id} was not found")
        today = datetime.now().astimezone().date().isoformat()
        schedule = self.stop_schedule(stop_id, today, force)["payload"]
        variants: dict[tuple[str, str, str], dict] = {}
        for row in schedule:
            key = (str(row.get("RouteID", "")), str(row.get("DirectionNum", "")), str(row.get("TripHeadsign", "")))
            variants[key] = {"route": key[0], "direction": key[1], "destination": key[2],
                             "direction_text": row.get("TripDirectionText", "")}
        return {"stop": stop, "variants": sorted(variants.values(), key=lambda x: (x["route"], x["direction"], x["destination"]))}
