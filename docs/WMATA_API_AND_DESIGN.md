# WMATA API analysis and implementation design

Research date: 2026-08-23. This document is intentionally written before the
application code. It records the external fields the implementation is allowed
to rely on and the places where inference is unavoidable.

## Verified data sources

All requests are server-side and use `api_key` in an HTTP header. The browser is
never given the key.

| Need | Endpoint/feed | Fields used |
|---|---|---|
| Bus stop metadata | `GET https://api.wmata.com/Bus.svc/json/jStops` | `Stops[].StopID`, `Name`, `Lat`, `Lon`, `Routes[]` |
| Route list | `GET .../Bus.svc/json/jRoutes` | `Routes[].RouteID`, `Name`, `LineDescription` |
| Directions and ordered stops | `GET .../Bus.svc/json/jRouteDetails?RouteID=...&Date=...` | `Direction0/1.DirectionNum`, `DirectionText`, `Stops[].StopID/Name/Lat/Lon/Routes`, `Shape[].Lat/Lon/SeqNum` |
| Stop schedule and destinations | `GET .../Bus.svc/json/jStopSchedule?StopID=...&Date=...` | `StopSchedules[].ScheduleTime`, `DirectionNum`, `RouteID`, `TripDirectionText`, `TripHeadsign`, `TripID` |
| Bus predictions | `GET .../NextBusService.svc/json/jPredictions?StopID=...` | `StopName`, `Predictions[].Minutes`, `RouteID`, `DirectionNum`, `DirectionText`, `TripID`, `VehicleID` |
| Vehicle GPS | `GET .../Bus.svc/json/jBusPositions?RouteID=...&IncludingVariations=true` | `DateTime`, `Lat`, `Lon`, `Deviation`, `DirectionNum/Text`, `RouteID`, `TripHeadsign`, `TripID`, `VehicleID`, `TripStartTime`, `TripEndTime` |
| Bus occupancy | `GET .../gtfs/bus-gtfsrt-vehiclepositions.pb` | GTFS-RT `vehicle.occupancy_status`, when populated |
| Rail station metadata | `GET .../Rail.svc/json/jStations` | `Stations[].Code`, `Name`, `Lat`, `Lon`, `LineCode1..4`, `StationTogether1..2` |
| Rail predictions | `GET .../StationPrediction.svc/json/GetPrediction/{codes}` | `Trains[].LocationCode/Name`, `DestinationCode/Name`, `Line`, `Group`, `Min`, `Car`, `Train` |
| Alerts (optional diagnostics) | `GET .../Incidents.svc/json/BusIncidents` and `GET .../Incidents.svc/json/Incidents` | incident description, routes/lines, date ranges |

The static and real-time GTFS feeds are the authoritative expansion point for
schedule and occupancy data. The first implementation keeps protobuf support an
optional dependency: legacy JSON remains usable on a resource-constrained host without it.

## Limitations and decisions

- WMATA exposes no explicit “bus served stop” event and no historical positions.
  We retain our own observations and only infer passage after an approaching
  vehicle was near the stop and is then observed moving away, progresses beyond
  the stop sequence, or disappears from predictions after being due. A schedule
  alone never creates a confirmed passage.
- `jPredictions` has no source timestamp. Freshness is based on request time plus
  the matching `jBusPositions.DateTime`. Unmatched predictions are still live
  predictions, but are marked stale once the prediction cache exceeds the profile
  threshold.
- A prediction can be joined to GPS and schedule most reliably by `TripID`, then
  by `VehicleID`; fuzzy route/direction/headsign/time matching is a final bounded
  fallback. Fuzzy matches never create a “confirmed served” event.
- Crowding is not returned by the legacy JSON prediction/position endpoints. It
  is shown only when an optional GTFS-RT vehicle record can be matched; otherwise
  it is omitted.
- Vehicle GPS commonly updates in bursts and WMATA publishes no contractual
  per-vehicle interval. The default stale threshold is therefore configurable
  (120 seconds), rather than assuming a guaranteed cadence.
- Rail `Min` may be an integer string or `ARR`, `BRD`, `DLY`, or `---`. `ARR` and
  `BRD` map to the profile's under-one-minute term; non-time status strings remain
  meaningful status values. PIDS train IDs cannot reliably be joined to the Train
  Positions API, per WMATA's Train Position FAQ, so rail arrival state uses PIDS.
- Legacy schedule timestamps are parsed as timezone-aware America/New_York values.
  ISO timestamps carrying offsets preserve them. Current and previous service
  dates cover trips crossing midnight; after-midnight trips are normalized by the
  returned timestamp, never by naive wall-clock comparison. WMATA's explicit
  “No schedule data available for this date” response is cached as an empty
  fallback set rather than treated as a failed real-time polling cycle.

## Architecture

`WMATAClient` performs bounded requests. `PollingManager` gathers each unique
configured stop/route/station once per cycle. `CacheManager` persists last-good
payloads and request metadata in SQLite. `PredictionEngine` joins fresh live,
stale live, and schedule data. `VehicleStateTracker` keeps short-lived progression
history and conservative passage evidence. Flask serves read-only dashboard JSON
and separate admin/diagnostic pages. Browsers poll only this local Flask app.

## Database schema

- `profiles`: slug, name, display type/theme/layout, transport toggles, count and
  notation options, freshness thresholds, refresh/full-refresh settings.
- `entries`: ordered profile rows for bus stops or rail stations, route/line,
  direction, destination, display label, enabled flag and JSON overrides.
- `cache_entries`: category/key, JSON payload, source, created/refreshed/expires
  timestamps, last error and HTTP metadata.
- `vehicle_observations`: short-lived vehicle/trip/stop samples, coordinates,
  prediction, GPS/observation times, progression state and passage evidence.
- `app_meta`: durable operational counters and request state.
- `secret_values`: write-only-from-the-browser application credentials, including
  an optionally web-configured WMATA key; values are excluded from diagnostics.

SQLite uses WAL mode. Configuration and cache are separate tables, so purging
cache cannot remove user settings. The database file is restricted to owner-only
permissions because it may contain an API key.

## Cache and polling

Static stops/stations/routes live for 24 hours; route details for 12 hours;
schedules for 6 hours; live predictions for 30 seconds; positions for 30 seconds;
rail predictions for 20 seconds. A single background thread computes the union of
all enabled profile requirements. On failure it retains the last-good value,
records the error, applies exponential retry backoff, and makes age visible.
Dashboard clients use a server-provided interval (LCD 15 seconds, e-Ink 60 seconds
by default) and skip DOM replacement when the content signature is unchanged.

## Vehicle state and stop-passage inference

States are `approaching`, `near_stop`, `at_stop`, `passed`, and `expired`.
Distance uses the haversine formula. An observation is near at <=120 m and at-stop
at <=45 m. Passage requires at least two observations and one of:

1. a prior `near_stop`/`at_stop` sample followed by distance increasing past 160 m
   with the prediction removed or the route stop sequence advanced; or
2. an at-stop prediction (<=1 minute), subsequent disappearance for two polls,
   and a matched vehicle position beyond the stop.

The inferred timestamp and evidence are stored with a confidence value. A vehicle
that merely vanishes, or a scheduled time that passes, is not counted. History is
retained for two hours and expired transactionally.

## Prediction merge

Filter by route, direction, and normalized TripHeadsign. Classify matched GPS as
fresh/stale using its `DateTime`. Match live-to-schedule by TripID, otherwise a
same-route/direction/headsign time window. Prefer fresh live, then stale live,
then unmatched future schedule. Deduplicate by TripID, then VehicleID, then a
route/direction/time bucket. Remove tracker-confirmed passed trips and long-past
values. Sort chronologically and cap to the profile's arrival count.

## UI, profiles, and recovery

Each profile has `/dashboard/<slug>` and can mix bus and rail entries. Row and
stop-card layouts share semantic text markers so monochrome never relies on color.
LCD light/dark and monochrome/color e-Ink are CSS profile modes. The admin page
discovers stop metadata, routes, directions and headsigns before saving; it also
reorders/deletes entries and manages cache. Diagnostics reports request, cache,
prediction, tracker, uptime, timezone, and error state. Optional HTTP Basic auth
protects admin and diagnostics; dashboards remain public. If WMATA or the network
fails, last-good content stays on screen with an age/status warning.
