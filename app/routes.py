from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import wraps
import hmac
import json
import logging
import threading
import re
import time
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for

from .database import utcnow
from .predictions.engine import format_minutes, merge_bus, merge_rail
from .vehicles.state_tracker import VehicleStateTracker
from .wmata.client import WMATAError

bp = Blueprint("main", __name__)
EASTERN = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)


def db():
    return current_app.extensions["database"]


def client():
    return current_app.extensions["wmata"]


def protected(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        settings = current_app.config["SETTINGS"]
        if not settings.admin_user:
            return view(*args, **kwargs)
        auth = request.authorization
        valid = auth and hmac.compare_digest(auth.username or "", settings.admin_user) and \
            hmac.compare_digest(auth.password or "", settings.admin_password)
        if not valid:
            return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="WMATA dashboard"'})
        return view(*args, **kwargs)
    return wrapped


def _schedule_rows(stop_id: str) -> list[dict]:
    rows = []
    now = datetime.now(EASTERN)
    for offset in (-1, 0):
        item = db().get_cache("stop_schedule", f"{stop_id}:{(now + timedelta(days=offset)).date().isoformat()}")
        if item:
            rows.extend(item["payload"])
    return rows


def dashboard_state(slug: str) -> dict:
    profile = db().profile(slug)
    if not profile:
        abort(404)
    tracker = VehicleStateTracker(db())
    state_entries = []
    max_age, errors, missing_live_cache = 0, [], False
    for entry in db().entries(profile["id"], enabled_only=True):
        arrivals, last = [], None
        if entry["mode"] == "bus":
            pc = db().get_cache("predictions", entry["location_id"])
            pos = db().get_cache("positions", entry["route"]) if entry["route"] else None
            predictions = pc["payload"].get("Predictions", []) if pc and isinstance(pc["payload"], dict) else []
            missing_live_cache = missing_live_cache or pc is None
            positions = [dict(p) for p in (pos["payload"] if pos else [])]
            if profile["show_occupancy"]:
                occupancy_cache = db().get_cache("occupancy", "bus")
                occupancy = occupancy_cache["payload"] if occupancy_cache else []
                by_trip = {str(o.get("trip_id")): o for o in occupancy if o.get("trip_id")}
                by_vehicle = {str(o.get("vehicle_id")): o for o in occupancy if o.get("vehicle_id")}
                for position in positions:
                    record = by_trip.get(str(position.get("TripID", ""))) or by_vehicle.get(str(position.get("VehicleID", "")))
                    if record and record.get("occupancy_status"):
                        position["OccupancyStatus"] = record["occupancy_status"]
            arrivals = merge_bus(entry, profile, predictions, _schedule_rows(entry["location_id"]),
                                 positions, pc["age_seconds"] if pc else 999999,
                                 passed_trip_ids=tracker.passed_trips(entry["location_id"]))
            last = tracker.last_bus(entry["location_id"], entry["route"], entry["destination"])
            if last:
                gps_at = datetime.fromisoformat(last["gps_at"]) if last.get("gps_at") else None
                gps_age = (datetime.now(UTC) - gps_at.astimezone(UTC)).total_seconds() if gps_at else 999999
                last_state = "stale" if gps_age > int(profile["stale_threshold"]) else "live"
                last = {**last, "state": last_state, "display": format_minutes(last["minutes_ago"], profile, last_state)}
            for cache in (pc, pos):
                if cache:
                    max_age = max(max_age, cache["age_seconds"])
                    if cache.get("last_error"):
                        errors.append(cache["last_error"])
        else:
            rc = db().get_cache("rail_predictions", entry["location_id"])
            missing_live_cache = missing_live_cache or rc is None
            arrivals = merge_rail(entry, profile, rc["payload"] if rc else [], rc["age_seconds"] if rc else 999999)
            if rc:
                max_age = max(max_age, rc["age_seconds"])
                if rc.get("last_error"):
                    errors.append(rc["last_error"])
        state_entries.append({**entry, "arrivals": arrivals, "last": last})
    warning = None
    if not client().api_key:
        warning = "WMATA API key not configured — open diagnostics"
    elif missing_live_cache:
        warning = "Waiting for the first WMATA update"
    elif errors:
        warning = "WMATA unavailable — showing last usable data"
    elif max_age > int(profile["stale_threshold"]):
        warning = "Cached real-time data may be stale"
    return {"profile": profile, "entries": state_entries, "freshness_seconds": max_age,
            "warning": warning, "server_time": datetime.now(EASTERN).isoformat(),
            "refresh_interval": profile["refresh_interval"]}


@bp.get("/")
def index():
    profiles = db().profiles()
    return redirect(url_for("main.dashboard", slug=profiles[0]["slug"]))


@bp.get("/dashboard/<slug>")
def dashboard(slug: str):
    return render_template("dashboard.html", state=dashboard_state(slug))


@bp.get("/api/dashboard/<slug>")
def api_dashboard(slug: str):
    return jsonify(dashboard_state(slug))


@bp.get("/health")
def health():
    return jsonify(status="ok", version=current_app.config["VERSION"], api_key_configured=bool(client().api_key))


@bp.get("/admin")
@protected
def admin():
    profiles = db().profiles()
    stored_api_key = db().get_secret("wmata_api_key")
    environment_api_key = current_app.config["SETTINGS"].api_key
    key_source = "not configured"
    if client().api_key:
        key_source = "environment" if environment_api_key and client().api_key == environment_api_key else "web interface"
    log_status = current_app.extensions["log_manager"].status()
    return render_template("admin.html", profiles=profiles,
                           entries={p["id"]: db().entries(p["id"]) for p in profiles},
                           api_key_configured=bool(client().api_key),
                           api_key_source=key_source, web_api_key_stored=bool(stored_api_key),
                           environment_api_key_configured=bool(environment_api_key),
                           admin_auth_enabled=bool(current_app.config["SETTINGS"].admin_user),
                           log_status=log_status, logs_purged=request.args.get("logs_purged"))


@bp.post("/admin/api-key")
@protected
def update_api_key():
    action = request.form.get("action", "save")
    if action == "remove":
        db().delete_secret("wmata_api_key")
        client().api_key = current_app.config["SETTINGS"].api_key
        db().set_meta("last_api_key_change", utcnow().isoformat())
        LOG.info("Removed web-configured WMATA API key")
    else:
        api_key = request.form.get("api_key", "").strip()
        if not api_key:
            abort(400, "API key cannot be empty")
        if len(api_key) > 256 or any(character.isspace() for character in api_key):
            abort(400, "Invalid API key format")
        db().set_secret("wmata_api_key", api_key)
        client().api_key = api_key
        db().set_meta("last_api_key_change", utcnow().isoformat())
        LOG.info("Updated WMATA API key through the administration interface")
    return redirect(url_for("main.admin"))


@bp.post("/admin/logs/purge")
@protected
def purge_logs():
    count = current_app.extensions["log_manager"].purge()
    db().set_meta("last_log_purge", {"at": utcnow().isoformat(), "files_removed": count})
    LOG.info("Purged %s application log file(s)", count)
    return redirect(url_for("main.admin", logs_purged=count))


def _delayed_shutdown(handler, app) -> None:
    time.sleep(1)
    handler(app)


@bp.post("/admin/shutdown")
@protected
def shutdown():
    app = current_app._get_current_object()
    handler = current_app.config["SHUTDOWN_HANDLER"]
    threading.Thread(target=_delayed_shutdown, args=(handler, app),
                     name="wmata-shutdown", daemon=True).start()
    LOG.warning("Clean application shutdown requested from the administration interface")
    return render_template("shutdown.html"), 202


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


@bp.post("/admin/profiles")
@protected
def create_profile():
    now = utcnow().isoformat()
    name = request.form.get("name", "New profile").strip()
    slug = _slug(request.form.get("slug", "") or name)
    db().execute("INSERT INTO profiles(slug,name,created_at,updated_at) VALUES(?,?,?,?)", (slug, name, now, now))
    LOG.info("Created dashboard profile %s", slug)
    return redirect(url_for("main.admin"))


@bp.post("/admin/profiles/<int:profile_id>")
@protected
def update_profile(profile_id: int):
    choices = {
        "display_type": {"lcd", "eink_mono", "eink_color"}, "theme": {"light", "dark"},
        "layout": {"row", "card"}, "minute_format": {"m", "min", "minutes"},
        "near_format": {"DUE", "ARR", "NOW", "<1", "actual"},
        "scheduled_format": {"s", "superscript", "(s)", "Scheduled"}, "stale_format": {"?", "﹖", "Stale"},
    }
    values = {}
    for key, allowed in choices.items():
        value = request.form.get(key, "")
        if value not in allowed:
            abort(400, f"Invalid {key}")
        values[key] = value
    values.update({
        "name": request.form.get("name", "Profile").strip(),
        "arrival_count": min(10, max(1, int(request.form.get("arrival_count", 3)))),
        "stale_threshold": min(3600, max(30, int(request.form.get("stale_threshold", 120)))),
        "refresh_interval": min(600, max(10, int(request.form.get("refresh_interval", 15)))),
        "show_bus": int("show_bus" in request.form), "show_rail": int("show_rail" in request.form),
        "show_legend": int("show_legend" in request.form), "show_occupancy": int("show_occupancy" in request.form),
    })
    db().execute("""UPDATE profiles SET name=?,display_type=?,theme=?,layout=?,minute_format=?,near_format=?,
        scheduled_format=?,stale_format=?,arrival_count=?,stale_threshold=?,refresh_interval=?,show_bus=?,show_rail=?,
        show_legend=?,show_occupancy=?,updated_at=? WHERE id=?""",
        (values["name"], values["display_type"], values["theme"], values["layout"], values["minute_format"],
         values["near_format"], values["scheduled_format"], values["stale_format"], values["arrival_count"],
         values["stale_threshold"], values["refresh_interval"], values["show_bus"], values["show_rail"],
         values["show_legend"], values["show_occupancy"], utcnow().isoformat(), profile_id))
    LOG.info("Updated dashboard profile id=%s", profile_id)
    return redirect(url_for("main.admin"))


@bp.post("/admin/entries")
@protected
def create_entry():
    mode = request.form.get("mode", "bus")
    if mode not in {"bus", "rail"}:
        abort(400)
    profile_id = int(request.form["profile_id"])
    position = db().one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM entries WHERE profile_id=?", (profile_id,))["n"]
    now = utcnow().isoformat()
    db().execute("""INSERT INTO entries(profile_id,position,mode,location_id,location_name,route,direction,destination,label,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (profile_id, position, mode, request.form["location_id"].strip(), request.form["location_name"].strip(),
         request.form.get("route", "").strip(), request.form.get("direction", "").strip(),
         request.form.get("destination", "").strip(), request.form.get("label", "").strip(), now, now))
    LOG.info("Added %s entry %s to profile id=%s", mode, request.form["location_id"], profile_id)
    return redirect(url_for("main.admin"))


@bp.post("/admin/entries/<int:entry_id>/move")
@protected
def move_entry(entry_id: int):
    entry = db().one("SELECT * FROM entries WHERE id=?", (entry_id,))
    if not entry:
        abort(404)
    delta = -1 if request.form.get("direction") == "up" else 1
    other = db().one("SELECT * FROM entries WHERE profile_id=? AND position=?", (entry["profile_id"], entry["position"] + delta))
    if other:
        db().execute("UPDATE entries SET position=? WHERE id=?", (entry["position"], other["id"]))
        db().execute("UPDATE entries SET position=? WHERE id=?", (entry["position"] + delta, entry_id))
    return redirect(url_for("main.admin"))


@bp.post("/admin/entries/<int:entry_id>")
@protected
def update_entry(entry_id: int):
    mode = request.form.get("mode", "bus")
    if mode not in {"bus", "rail"}:
        abort(400)
    db().execute("""UPDATE entries SET mode=?,location_id=?,location_name=?,route=?,direction=?,destination=?,
        label=?,enabled=?,updated_at=? WHERE id=?""",
        (mode, request.form["location_id"].strip(), request.form["location_name"].strip(),
         request.form.get("route", "").strip(), request.form.get("direction", "").strip(),
         request.form.get("destination", "").strip(), request.form.get("label", "").strip(),
         int("enabled" in request.form), utcnow().isoformat(), entry_id))
    LOG.info("Updated dashboard entry id=%s", entry_id)
    return redirect(url_for("main.admin"))


@bp.post("/admin/entries/<int:entry_id>/delete")
@protected
def delete_entry(entry_id: int):
    db().execute("DELETE FROM entries WHERE id=?", (entry_id,))
    LOG.info("Deleted dashboard entry id=%s", entry_id)
    return redirect(url_for("main.admin"))


@bp.get("/admin/discover/stop/<stop_id>")
@protected
def discover_stop(stop_id: str):
    try:
        return jsonify(client().discover_stop(stop_id))
    except WMATAError as exc:
        return jsonify(error=str(exc)), 502


@bp.get("/admin/discover/stations")
@protected
def discover_stations():
    try:
        return jsonify(client().stations()["payload"])
    except WMATAError as exc:
        return jsonify(error=str(exc)), 502


@bp.post("/admin/cache/purge")
@protected
def purge_cache():
    category = request.form.get("category", "all")
    if category == "all":
        db().execute("DELETE FROM cache_entries")
    else:
        db().execute("DELETE FROM cache_entries WHERE category=?", (category,))
    db().set_meta("last_cache_purge", utcnow().isoformat())
    LOG.info("Purged cache category=%s", category)
    return redirect(url_for("main.diagnostics"))


@bp.post("/admin/cache/rebuild")
@protected
def rebuild_cache():
    poller = current_app.extensions.get("poller")
    if poller:
        try:
            poller.force_refresh()
        except WMATAError:
            pass
    return redirect(url_for("main.diagnostics"))


@bp.get("/diagnostics")
@protected
def diagnostics():
    caches = db().rows("SELECT * FROM cache_entries ORDER BY category,cache_key")
    now = utcnow()
    for cache in caches:
        refreshed = datetime.fromisoformat(cache["refreshed_at"])
        cache["age_seconds"] = max(0, int((now - refreshed).total_seconds()))
        cache["expired"] = datetime.fromisoformat(cache["expires_at"]) <= now
    observations = db().rows("SELECT * FROM vehicle_observations ORDER BY observed_at DESC LIMIT 100")
    prediction_rows = []
    for profile in db().profiles():
        state = dashboard_state(profile["slug"])
        for entry in state["entries"]:
            for arrival in entry["arrivals"]:
                prediction_rows.append({"profile": profile["name"], "location": entry["location_name"],
                                        "route": entry["route"], "configured_destination": entry["destination"], **arrival})
    return render_template("diagnostics.html", caches=caches, observations=observations, meta=db().meta(),
                           profiles=db().profiles(), prediction_rows=prediction_rows,
                           uptime=int(time.monotonic() - current_app.config["STARTED_MONOTONIC"]),
                           version=current_app.config["VERSION"], now=datetime.now(EASTERN))
