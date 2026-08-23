# WMATA Arrival Display

A minimalist, always-on Metrobus and optional Metrorail arrival board for a
Raspberry Pi. One Python process centrally polls WMATA, stores last-good data in
SQLite, and serves lightweight dashboard pages to every screen on the LAN.

The dashboard intentionally distinguishes **fresh live**, **stale live**, and
**scheduled fallback** arrivals. It never declares that a bus served a stop based
only on a scheduled time passing.

## Quick start

Python 3.13 is required.

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
cp .env.example .env
python run.py
```

Open `http://127.0.0.1:8080/admin` to configure stops and
`http://127.0.0.1:8080/dashboard/home` for the default display. With the default
`WMATA_BIND=0.0.0.0`, use the Pi's LAN IP from another device.

The WMATA API key can be entered in the admin interface or supplied through
`WMATA_API_KEY` in `.env`. Environment configuration takes precedence after an
application restart.

## What is included

- Multiple profiles and stable URLs (`/dashboard/home`, `/dashboard/office`, …)
- Row and grouped stop-card layouts
- LCD light/dark, monochrome e-Ink, and color e-Ink modes
- Bus stop discovery, route/direction/TripHeadsign selection, editing, ordering,
  disabling, and deletion
- Metrorail station discovery and mixed bus/rail boards
- Configurable arrival count, minute wording, near-arrival term, scheduled/stale
  notation, browser refresh, and stale threshold
- Centralized polling, TTL caches, last-good recovery, force refresh, and
  category-specific/all-cache purging
- Short-lived vehicle history and conservative last-bus inference
- Optional HTTP Basic authentication for admin and diagnostics
- Diagnostics for uptime, local time, WMATA requests, cache, and vehicle evidence
- Confirmed, clean server shutdown from the protected administration interface
- systemd backend and Chromium kiosk units

## Architecture and data correctness

The complete endpoint/field audit, schema, cache policy, state model, merge
algorithm, midnight handling, and known WMATA limitations are in
[`docs/WMATA_API_AND_DESIGN.md`](docs/WMATA_API_AND_DESIGN.md).

The source flow is:

```text
WMATA JSON + optional GTFS-RT
          ↓
  WMATAClient (bounded HTTP)
          ↓
 SQLite last-good cache ← PollingManager → VehicleStateTracker
          ↓
     PredictionEngine
          ↓
 Flask JSON endpoint → tiny browser poller → LCD / e-Ink layout
```

WMATA's official developer resources confirm that static GTFS provides official
schedule data and GTFS-RT contains arrival predictions, vehicle positions,
advisories, and crowding. The legacy bus prediction response supplies Minutes,
RouteID, DirectionNum/Text, TripID, and VehicleID; the bus-position response is
the source for GPS coordinates and `DateTime`. No explicit bus-at-stop event or
historical position endpoint exists, so the app records its own short history.

### Prediction merge

For each configured row the engine filters route, direction, and normalized
destination; matches live predictions to position and schedule by TripID (then
VehicleID/bounded fallback); classifies GPS age; prefers fresh live, then stale
live, then schedule; deduplicates; removes tracker-confirmed passed trips; sorts;
and caps to the profile count. A live and scheduled representation of the same
TripID is never shown twice.

### Stop-passage and “Last” logic

The tracker measures vehicle-to-stop distance and retains two hours of samples.
States are `approaching`, `near_stop` (≤120 m), `at_stop` (≤45 m), `passed`, and
`expired`. “Passed” requires prior proximity plus movement beyond 160 m and the
prediction disappearing. Distance alone or disappearance alone is insufficient.
The inferred timestamp, evidence, and confidence are visible in diagnostics.
Before sufficient evidence exists the board shows `Last —`, not a fabricated time.

### Freshness and failure recovery

Live TTLs are 20–30 seconds; schedules are 6 hours; route geometry is 12 hours;
stops, routes, and stations are 24 hours. Failed refreshes preserve last-good
payloads and attach an error. The board stays usable during an outage and displays
the age/warning. HTTP requests have connection/read timeouts, the poller catches
malformed responses, and systemd restarts the service after process failure.

### Time and service days

All comparisons use aware datetimes. WMATA local timestamps are attached to
`America/New_York`; explicit offsets are preserved and values are normalized to
UTC internally. The poller caches the current and previous service dates, then
filters the actual returned timestamps. Together they cover service crossing
midnight without assuming 00:00 begins a new transit service day. WMATA sometimes
returns HTTP 400 with “No schedule data available for this date”; that specific
response is cached as an empty schedule while real-time polling continues.

### Occupancy

WMATA crowding is in its GTFS-RT feeds, not in the legacy JSON bus prediction or
position record. `OccupancyStatus` is rendered only when a GTFS-RT record matches
TripID or VehicleID; otherwise the UI omits it. No crowding state is guessed.

## Configuration

Environment variables are documented in `.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `WMATA_API_KEY` | empty | Required server-side WMATA subscription key |
| `WMATA_BIND` / `WMATA_PORT` | `0.0.0.0` / `8080` | LAN listener |
| `WMATA_DATABASE` | `instance/wmata-dashboard.sqlite3` | Persistent SQLite file |
| `WMATA_ADMIN_USER/PASSWORD` | empty | Enables Basic auth when user is set |
| `WMATA_POLL_INTERVAL` | `20` | Central poll cadence in seconds (minimum 10) |
| `WMATA_START_POLLER` | `true` | Disable for tests or one-shot management |
| `WMATA_LOG_LEVEL` | `INFO` | Python logging level |
| `WMATA_LOG_FILE` | `instance/logs/wmata-dashboard.log` | Rotating application log |
| `WMATA_LOG_MAX_BYTES` | `2000000` | Bytes before log rotation |
| `WMATA_LOG_BACKUP_COUNT` | `3` | Number of rotated logs retained |
| `WMATA_SECRET_KEY` | development value | Set a random production value |

The API key is only read server-side and is never emitted in HTML or JSON.
Keys entered through the admin page are stored in SQLite's separate
`secret_values` table, and the database file is restricted to mode `0600`. The
key field is always blank when the page loads; the UI reports only whether a key
is configured. Because browser submission still travels over the network, enable
admin authentication and use a trusted LAN or an HTTPS reverse proxy before
entering a key remotely. Keep `instance/` and database backups out of Git.

## Raspberry Pi OS deployment

Use a 64-bit Raspberry Pi OS release that packages Python 3.13 (Debian 13/Trixie
or later). Verify first with `python3 --version`; on older Bookworm images install
Python 3.13 through your maintained package/source policy before proceeding.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip chromium git
sudo mkdir -p /opt/wmata-dashboard
sudo chown pi:pi /opt/wmata-dashboard
# copy this project into /opt/wmata-dashboard
cd /opt/wmata-dashboard
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install .
cp .env.example .env
chmod 600 .env
```

Edit `.env`, supply the API key and a random secret, and optionally set admin
credentials. Test with:

```bash
.venv/bin/waitress-serve --host=0.0.0.0 --port=8080 --call app:create_app
```

Install automatic startup:

```bash
sudo cp wmata-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wmata-dashboard
sudo systemctl status wmata-dashboard
journalctl -u wmata-dashboard -f
```

If your login user is not `pi`, edit `User`, `Group`, `WorkingDirectory`, and the
paths in the service file. The SQLite database survives app and Pi restarts.

## Chromium kiosk

Edit `wmata-kiosk.service` to select the profile URL and Pi username, then:

```bash
sudo cp wmata-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wmata-kiosk
```

The kiosk unit uses `Restart=always`, suppresses Chromium crash UI, and waits for
the backend unit. For Wayland-based Raspberry Pi desktops, autostart Chromium from
the desktop session instead if `DISPLAY=:0` is unavailable. e-Ink profiles poll
less frequently, disable animation, and only replace the DOM when data changes;
hardware-specific partial/full refresh remains the display driver's responsibility.

## Administration and diagnostics

The admin StopID lookup fetches the full cached stop catalog, then the selected
stop's schedule to derive current routes, directions, and TripHeadsigns. Static
metadata can therefore be slightly stale without affecting a live prediction;
diagnostics labels each cache category separately. Cache purge never touches
profiles or entries.

Useful URLs:

- `/admin` — configuration and discovery
- `/diagnostics` — requests, cache and vehicle evidence
- `/health` — lightweight process health JSON
- `/api/dashboard/<slug>` — local dashboard state consumed by browsers

The bottom of `/admin` includes a **Stop dashboard** control. After confirmation,
it stops the central polling thread, flushes logging, returns a shutdown status
page, and terminates the server process with `SIGTERM`. With the supplied systemd
unit's `Restart=on-failure`, an intentional clean stop stays stopped; restart it
with `sudo systemctl start wmata-dashboard`. Protect the admin page before making
it available outside a trusted LAN.

The same System panel includes **Purge log files**. After confirmation, the app
closes its rotating file handler, removes only `wmata-dashboard.log` and its
numbered rotated backups, then opens a fresh owner-only log. It does not delete
other files in the log directory. Log files and rotated backups are excluded by
`.gitignore`.

The JSON dashboard endpoint is intentionally an application view, not a proxy for
the raw WMATA API.

## Testing

```bash
python -m pytest -q
```

Tests use mocked/pure inputs—no WMATA key or Internet access. They cover formatting,
fresh/stale/scheduled selection, filtering, TripID/vehicle deduplication, passed
removal, rail coexistence primitives, aware midnight parsing, cache expiration,
state thresholds, conservative passage inference, and last-bus calculation.

## Troubleshooting

- **API key warning:** confirm `.env` is in the service working directory, then
  restart the unit. Never paste the key into the browser.
- **No destinations during discovery:** the stop may have no trips on today's
  service date. Confirm StopID and force refresh from Diagnostics.
- **Stale marker on otherwise plausible arrivals:** check GPS age in diagnostics;
  raising a profile threshold is possible, but hiding stale state is not.
- **`Last —`:** this is expected until the running process observes a vehicle near
  the stop and later obtains enough passage evidence.
- **Rate limiting/outage:** increase central poll interval. Existing clients share
  one cache and do not multiply WMATA calls.
- **Clock errors:** enable NTP (`timedatectl`) and confirm `America/New_York` data is
  installed. The application itself does not mutate the Pi timezone.

## Upstream references

- [WMATA API portal](https://developer.wmata.com/apis)
- [WMATA developer resources and GTFS/GTFS-RT notes](https://www.wmata.com/about/developers.html)
- [WMATA crowding feed notice](https://www.wmata.com/about/developers/crowding.html)
- [WMATA Train Position FAQ](https://developer.wmata.com/trainpositionsfaq)
- [Microsoft WMATA connector field reference](https://learn.microsoft.com/en-us/connectors/wmata/)
