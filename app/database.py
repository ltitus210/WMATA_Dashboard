from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import os
import sqlite3
import threading
from typing import Any, Iterator

from .logging_manager import format_bytes


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS profiles (
 id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 display_type TEXT NOT NULL DEFAULT 'lcd', theme TEXT NOT NULL DEFAULT 'dark',
 layout TEXT NOT NULL DEFAULT 'row', text_size TEXT NOT NULL DEFAULT 'medium', show_bus INTEGER NOT NULL DEFAULT 1,
 show_rail INTEGER NOT NULL DEFAULT 0, arrival_count INTEGER NOT NULL DEFAULT 3,
 minute_format TEXT NOT NULL DEFAULT 'm', near_format TEXT NOT NULL DEFAULT 'DUE',
 scheduled_format TEXT NOT NULL DEFAULT 'superscript', stale_format TEXT NOT NULL DEFAULT '?',
 stale_threshold INTEGER NOT NULL DEFAULT 120, refresh_interval INTEGER NOT NULL DEFAULT 15,
 full_refresh_interval INTEGER NOT NULL DEFAULT 1800, show_legend INTEGER NOT NULL DEFAULT 1,
 show_occupancy INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
 id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
 position INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL CHECK(mode IN ('bus','rail')),
 location_id TEXT NOT NULL, location_name TEXT NOT NULL, route TEXT NOT NULL DEFAULT '',
 direction TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
 label TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
 overrides_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_profile ON entries(profile_id, position, id);
CREATE TABLE IF NOT EXISTS cache_entries (
 category TEXT NOT NULL, cache_key TEXT NOT NULL, payload_json TEXT NOT NULL,
 source TEXT NOT NULL, created_at TEXT NOT NULL, refreshed_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, last_error TEXT, http_status INTEGER, latency_ms REAL,
 PRIMARY KEY(category, cache_key)
);
CREATE TABLE IF NOT EXISTS vehicle_observations (
 id INTEGER PRIMARY KEY, vehicle_id TEXT, trip_id TEXT, route TEXT, headsign TEXT,
 direction TEXT, stop_id TEXT NOT NULL, lat REAL, lon REAL, distance_m REAL,
 prediction_minutes REAL, gps_at TEXT, observed_at TEXT NOT NULL,
 state TEXT NOT NULL, inferred_passage_at TEXT, evidence TEXT, confidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_vehicle_history ON vehicle_observations(stop_id, trip_id, vehicle_id, observed_at);
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secret_values (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


class Database:
    def __init__(self, path: str):
        self.path = path
        self._write_lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        from pathlib import Path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
            if "text_size" not in columns:
                conn.execute("ALTER TABLE profiles ADD COLUMN text_size TEXT NOT NULL DEFAULT 'medium'")
        os.chmod(self.path, 0o600)

    def seed_default_profile(self) -> None:
        now = utcnow().isoformat()
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO profiles(slug,name,created_at,updated_at) VALUES(?,?,?,?)",
                ("home", "Home", now, now),
            )

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self._write_lock, self.connect() as conn:
            cur = conn.execute(sql, args)
            return int(cur.lastrowid or 0)

    def profiles(self) -> list[dict]:
        return self.rows("SELECT * FROM profiles ORDER BY name")

    def profile(self, slug: str) -> dict | None:
        return self.one("SELECT * FROM profiles WHERE slug=?", (slug,))

    def entries(self, profile_id: int, enabled_only: bool = False) -> list[dict]:
        suffix = " AND enabled=1" if enabled_only else ""
        return self.rows(f"SELECT * FROM entries WHERE profile_id=?{suffix} ORDER BY position,id", (profile_id,))

    def put_cache(self, category: str, key: str, payload: Any, ttl: int, source: str,
                  status: int | None = 200, latency_ms: float | None = None) -> None:
        now = utcnow()
        self.execute(
            """INSERT INTO cache_entries(category,cache_key,payload_json,source,created_at,refreshed_at,expires_at,last_error,http_status,latency_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(category,cache_key) DO UPDATE SET
               payload_json=excluded.payload_json,source=excluded.source,refreshed_at=excluded.refreshed_at,
               expires_at=excluded.expires_at,last_error=NULL,http_status=excluded.http_status,latency_ms=excluded.latency_ms""",
            (category, key, json.dumps(payload), source, now.isoformat(), now.isoformat(),
             (now + timedelta(seconds=ttl)).isoformat(), None, status, latency_ms),
        )

    def get_cache(self, category: str, key: str) -> dict | None:
        row = self.one("SELECT * FROM cache_entries WHERE category=? AND cache_key=?", (category, key))
        if not row:
            return None
        row["payload"] = json.loads(row.pop("payload_json"))
        row["expired"] = datetime.fromisoformat(row["expires_at"]) <= utcnow()
        row["age_seconds"] = max(0, int((utcnow() - datetime.fromisoformat(row["refreshed_at"])).total_seconds()))
        return row

    def cache_error(self, category: str, key: str, error: str, status: int | None = None) -> None:
        self.execute(
            "UPDATE cache_entries SET last_error=?,http_status=? WHERE category=? AND cache_key=?",
            (error[:1000], status, category, key),
        )

    def set_meta(self, key: str, value: Any) -> None:
        now = utcnow().isoformat()
        self.execute(
            "INSERT INTO app_meta(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, json.dumps(value), now),
        )

    def delete_meta(self, key: str) -> None:
        self.execute("DELETE FROM app_meta WHERE key=?", (key,))

    def meta(self) -> dict[str, Any]:
        result = {}
        for row in self.rows("SELECT * FROM app_meta"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    def get_secret(self, key: str) -> str:
        row = self.one("SELECT value FROM secret_values WHERE key=?", (key,))
        return row["value"] if row else ""

    def set_secret(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO secret_values(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, utcnow().isoformat()),
        )

    def delete_secret(self, key: str) -> None:
        self.execute("DELETE FROM secret_values WHERE key=?", (key,))

    def storage_status(self) -> dict:
        paths = [self.path, f"{self.path}-wal", f"{self.path}-shm"]
        database_bytes = sum(os.path.getsize(path) for path in paths if os.path.exists(path))
        cache = self.one(
            "SELECT COUNT(*) AS count,COALESCE(SUM(LENGTH(payload_json)),0) AS bytes FROM cache_entries"
        ) or {"count": 0, "bytes": 0}
        return {
            "filename": os.path.basename(self.path),
            "bytes": database_bytes,
            "size": format_bytes(database_bytes),
            "cache_count": int(cache["count"]),
            "cache_bytes": int(cache["bytes"]),
            "cache_size": format_bytes(int(cache["bytes"])),
        }
