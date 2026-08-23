from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import Iterable
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def parse_wmata_time(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=EASTERN)
    return result.astimezone(UTC)


def normalize_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()


def scheduled_marker(style: str) -> str:
    return {"s": "s", "superscript": "ˢ", "(s)": " (s)", "Scheduled": " Scheduled"}.get(style, "ˢ")


def stale_marker(style: str) -> str:
    return {"?": "?", "﹖": "﹖", "Stale": " Stale"}.get(style, "?")


def format_minutes(minutes: float, profile: dict, state: str = "live") -> str:
    near = profile.get("near_format", "DUE")
    unit = profile.get("minute_format", "m")
    if minutes < 1:
        if near == "actual":
            text = f"{max(0, round(minutes * 60))}s"
        else:
            text = near
    else:
        amount = max(0, round(minutes))
        text = f"{amount}{unit}" if unit == "m" else f"{amount} {unit}"
    if state == "scheduled":
        text += scheduled_marker(profile.get("scheduled_format", "superscript"))
    elif state == "stale":
        text += stale_marker(profile.get("stale_format", "?"))
    return text


def _matches(entry: dict, item: dict) -> bool:
    route = str(item.get("RouteID", item.get("Line", "")))
    direction = str(item.get("DirectionNum", item.get("Group", "")))
    destination = str(item.get("TripHeadsign", item.get("DirectionText", item.get("DestinationName", ""))))
    return (not entry.get("route") or route == entry["route"]) and \
           (not entry.get("direction") or direction == entry["direction"]) and \
           (not entry.get("destination") or normalize_text(entry["destination"]) in normalize_text(destination))


def _dedupe_key(item: dict) -> tuple:
    if item.get("trip_id"):
        return ("trip", item["trip_id"])
    if item.get("vehicle_id"):
        return ("vehicle", item["vehicle_id"])
    return ("bucket", item.get("route"), item.get("direction"), round(item["minutes"] / 2))


def merge_bus(entry: dict, profile: dict, predictions: list[dict], schedules: list[dict],
              positions: list[dict], cache_age: int = 0, now: datetime | None = None,
              passed_trip_ids: set[str] | None = None) -> list[dict]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    passed_trip_ids = passed_trip_ids or set()
    pos_by_trip = {str(p.get("TripID")): p for p in positions if p.get("TripID")}
    pos_by_vehicle = {str(p.get("VehicleID")): p for p in positions if p.get("VehicleID")}
    live: list[dict] = []
    for p in predictions:
        if not _matches(entry, p) or str(p.get("TripID", "")) in passed_trip_ids:
            continue
        trip_id, vehicle_id = str(p.get("TripID", "")), str(p.get("VehicleID", ""))
        position = pos_by_trip.get(trip_id) or pos_by_vehicle.get(vehicle_id)
        gps_at = parse_wmata_time(position.get("DateTime")) if position else None
        gps_age = int((now - gps_at).total_seconds()) if gps_at else cache_age
        state = "stale" if gps_age > int(profile.get("stale_threshold", 120)) else "live"
        minutes = max(0.0, float(p.get("Minutes", 0)))
        live.append({"route": p.get("RouteID", ""), "direction": str(p.get("DirectionNum", "")),
                     "destination": p.get("DirectionText", entry.get("destination", "")), "minutes": minutes,
                     "state": state, "trip_id": trip_id, "vehicle_id": vehicle_id,
                     "gps_at": gps_at.isoformat() if gps_at else None, "gps_age": gps_age,
                     "occupancy": position.get("OccupancyStatus") if position else None,
                     "reason": "live prediction with matched GPS" if position else "live prediction; no matching GPS"})

    live_trips = {x["trip_id"] for x in live if x["trip_id"]}
    scheduled: list[dict] = []
    for s in schedules:
        if not _matches(entry, s) or str(s.get("TripID", "")) in live_trips:
            continue
        at = parse_wmata_time(s.get("ScheduleTime") or s.get("Time"))
        if not at:
            continue
        minutes = (at - now).total_seconds() / 60
        if minutes < -1:
            continue
        scheduled.append({"route": s.get("RouteID", ""), "direction": str(s.get("DirectionNum", "")),
                          "destination": s.get("TripHeadsign", ""), "minutes": max(0, minutes),
                          "state": "scheduled", "trip_id": str(s.get("TripID", "")), "vehicle_id": "",
                          "gps_at": None, "gps_age": None, "occupancy": None,
                          "reason": "published schedule fallback"})
    unique: dict[tuple, dict] = {}
    rank = {"live": 0, "stale": 1, "scheduled": 2}
    for item in sorted(live + scheduled, key=lambda x: (x["minutes"], rank[x["state"]])):
        unique.setdefault(_dedupe_key(item), item)
    result = sorted(unique.values(), key=lambda x: x["minutes"])[:int(profile.get("arrival_count", 3))]
    for item in result:
        item["display"] = format_minutes(item["minutes"], profile, item["state"])
    return result


def merge_rail(entry: dict, profile: dict, trains: Iterable[dict], cache_age: int = 0) -> list[dict]:
    result = []
    state = "stale" if cache_age > int(profile.get("stale_threshold", 120)) else "live"
    for train in trains:
        if not _matches(entry, train):
            continue
        raw = str(train.get("Min", "---")).upper()
        if raw in {"ARR", "BRD"}:
            minutes, display = 0.0, format_minutes(0, profile, state)
        else:
            try:
                minutes, display = float(raw), format_minutes(float(raw), profile, state)
            except ValueError:
                minutes, display = 9999.0, raw
        result.append({"route": train.get("Line", ""), "direction": str(train.get("Group", "")),
                       "destination": train.get("DestinationName", "No Passenger"), "minutes": minutes,
                       "display": display, "state": state, "cars": train.get("Car"),
                       "train_id": train.get("Train"), "reason": "WMATA PIDS rail prediction"})
    return sorted(result, key=lambda x: x["minutes"])[:int(profile.get("arrival_count", 3))]
