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
            if name == "stop_schedule" and response.status_code == 400:
                try:
                    message = str(response.json().get("Message", ""))
                except ValueError:
                    message = ""
                if "no schedule data available" in message.lower():
                    self.db.put_cache(name, key, [], ttl, self.BASE + path,
                                      response.status_code, latency)
                    self.db.set_meta("last_schedule_unavailable", {"key": key, "message": message})
                    result = self.db.get_cache(name, key)
                    assert result is not None
                    return result
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
        cached_options = self.db.get_cache("bus_options", str(stop_id))
        if cached_options:
            for option in cached_options["payload"]:
                key = (str(option.get("route", "")), str(option.get("direction", "")),
                       str(option.get("destination", "")))
                if key[0]:
                    variants[key] = option
        for row in schedule:
            key = (str(row.get("RouteID", "")), str(row.get("DirectionNum", "")), str(row.get("TripHeadsign", "")))
            if key[0]:
                variants[key] = {"route": key[0], "direction": key[1], "destination": key[2],
                                 "direction_text": row.get("TripDirectionText", "")}
        prediction_payload = self.predictions(stop_id, force)["payload"]
        predictions = prediction_payload.get("Predictions", []) if isinstance(prediction_payload, dict) else []
        for row in predictions:
            key = (str(row.get("RouteID", "")), str(row.get("DirectionNum", "")), str(row.get("DirectionText", "")))
            if key[0]:
                variants.setdefault(key, {"route": key[0], "direction": key[1], "destination": key[2],
                                          "direction_text": row.get("DirectionText", "")})

        # Preserve variants seen during other service periods so limited/express routes
        # remain configurable on evenings and weekends when they have no live prediction.
        observed = sorted(variants.values(), key=lambda x: (x["route"], x["direction"], x["destination"]))
        self.db.put_cache("bus_options", str(stop_id), observed, 604800,
                          f"derived:{self.BASE}/Bus.svc/json/jStopSchedule")

        # The stop catalog is the authoritative list of routes serving the stop. If
        # WMATA supplies no current details, add a route-only option. Empty direction
        # and destination values intentionally mean "any" to the prediction matcher.
        advertised_routes = [str(route) for route in (stop.get("Routes") or []) if str(route)]
        detailed_routes = {option["route"] for option in observed}
        for route in advertised_routes:
            if route not in detailed_routes:
                observed.append({"route": route, "direction": "", "destination": "",
                                 "direction_text": "", "provisional": True})

        return {"stop": stop, "variants": sorted(observed, key=lambda x: (x["route"], x["direction"], x["destination"])),
                "schedule_available": bool(schedule)}

    def discover_station(self, station_code: str, force: bool = False) -> dict:
        """Return cached station metadata and accumulated passenger service variants."""
        code = station_code.strip().upper()
        stations = self.stations(force)["payload"]
        station = next((s for s in stations if str(s.get("Code", "")).upper() == code), None)
        if not station:
            raise WMATAError(f"Station {code} was not found")

        cached_options = self.db.get_cache("rail_options", code)
        variants: dict[tuple[str, str, str], dict] = {}
        if cached_options:
            for option in cached_options["payload"]:
                key = (str(option.get("line", "")), str(option.get("group", "")),
                       str(option.get("destination", "")))
                variants[key] = option

        trains = self.rail_predictions(code, force)["payload"]
        for train in trains:
            line = str(train.get("Line", "")).strip().upper()
            group = str(train.get("Group", "")).strip()
            destination = str(train.get("DestinationName", "")).strip()
            if not line or line in {"NO", "--"} or not destination or destination.lower() == "no passenger":
                continue
            key = (line, group, destination)
            variants[key] = {"line": line, "group": group, "destination": destination}

        options = sorted(variants.values(), key=lambda x: (x["line"], x["group"], x["destination"]))
        self.db.put_cache("rail_options", code, options, 86400,
                          f"derived:{self.BASE}/StationPrediction.svc/json/GetPrediction/{code}")
        lines = [str(station.get(f"LineCode{index}", "")).strip()
                 for index in range(1, 5) if station.get(f"LineCode{index}")]
        return {"station": station, "lines": lines, "variants": options}
