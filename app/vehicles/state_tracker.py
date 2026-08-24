from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
import logging

from ..database import Database
from ..predictions.engine import normalize_headsign, parse_wmata_time

LOG = logging.getLogger(__name__)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371000 * 2 * asin(sqrt(a))


class VehicleStateTracker:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def classify(distance: float, previous_distance: float | None = None) -> str:
        if distance <= 45:
            return "at_stop"
        if distance <= 120:
            return "near_stop"
        if previous_distance is not None and previous_distance <= 120 and distance >= 160:
            return "passed"
        return "approaching"

    def observe(self, stop: dict, position: dict, prediction_minutes: float | None,
                prediction_present: bool = True, observed_at: datetime | None = None) -> dict:
        observed_at = observed_at or datetime.now(UTC)
        vehicle_id, trip_id = str(position.get("VehicleID", "")), str(position.get("TripID", ""))
        previous = self.db.one(
            """SELECT * FROM vehicle_observations WHERE stop_id=? AND
               ((trip_id<>'' AND trip_id=?) OR (vehicle_id<>'' AND vehicle_id=?)) ORDER BY observed_at DESC LIMIT 1""",
            (str(stop["StopID"]), trip_id, vehicle_id),
        )
        dist = distance_m(float(stop["Lat"]), float(stop["Lon"]), float(position["Lat"]), float(position["Lon"]))
        state = self.classify(dist, previous["distance_m"] if previous else None)
        passage_at = evidence = confidence = None
        gps_at = parse_wmata_time(position.get("DateTime"))
        previous_gps = parse_wmata_time(previous.get("gps_at")) if previous else None
        gps_progressed = not previous_gps or not gps_at or gps_at > previous_gps
        if state == "passed" and previous and previous["state"] in {"near_stop", "at_stop"} and not prediction_present and gps_progressed:
            passage_at = (gps_at or observed_at).isoformat()
            evidence = "near/at stop, then GPS moved away and prediction disappeared"
            confidence = "high"
        elif state == "passed":
            state = "approaching"  # distance alone is insufficient
        self.db.execute(
            """INSERT INTO vehicle_observations(vehicle_id,trip_id,route,headsign,direction,stop_id,lat,lon,distance_m,
               prediction_minutes,gps_at,observed_at,state,inferred_passage_at,evidence,confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vehicle_id, trip_id, position.get("RouteID", ""), position.get("TripHeadsign", ""),
             str(position.get("DirectionNum", "")), str(stop["StopID"]), position.get("Lat"), position.get("Lon"),
             dist, prediction_minutes, gps_at.isoformat() if gps_at else None, observed_at.isoformat(), state, passage_at, evidence, confidence),
        )
        if passage_at:
            LOG.info("Inferred stop passage stop=%s trip=%s vehicle=%s evidence=%s",
                     stop["StopID"], trip_id, vehicle_id, evidence)
        return {"state": state, "distance_m": dist, "inferred_passage_at": passage_at,
                "evidence": evidence, "confidence": confidence}

    def passed_trips(self, stop_id: str) -> set[str]:
        cutoff = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        rows = self.db.rows(
            "SELECT DISTINCT trip_id FROM vehicle_observations WHERE stop_id=? AND state='passed' AND observed_at>=?",
            (stop_id, cutoff),
        )
        return {r["trip_id"] for r in rows if r["trip_id"]}

    def last_bus(self, stop_id: str, route: str, destination: str = "", direction: str = "") -> dict | None:
        rows = self.db.rows(
            """SELECT * FROM vehicle_observations WHERE stop_id=? AND route=?
               AND inferred_passage_at IS NOT NULL ORDER BY inferred_passage_at DESC LIMIT 100""",
            (stop_id, route),
        )
        configured_destination = normalize_headsign(destination)
        row = next((candidate for candidate in rows
                    if (not direction or str(candidate.get("direction", "")) == str(direction))
                    and (not configured_destination
                         or configured_destination in normalize_headsign(candidate.get("headsign", ""))
                         or normalize_headsign(candidate.get("headsign", "")) in configured_destination)), None)
        if row is None:
            return None
        at = datetime.fromisoformat(row["inferred_passage_at"])
        row["minutes_ago"] = max(0, (datetime.now(UTC) - at).total_seconds() / 60)
        return row

    def expire(self) -> None:
        recent_cutoff = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        passage_cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        self.db.execute(
            "DELETE FROM vehicle_observations WHERE (inferred_passage_at IS NULL AND observed_at<?) "
            "OR (inferred_passage_at IS NOT NULL AND observed_at<?)",
            (recent_cutoff, passage_cutoff),
        )
