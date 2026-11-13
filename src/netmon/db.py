"""SQLite storage: monitors, check history, hourly rollups, incidents, settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE monitors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,
    target          TEXT NOT NULL,
    group_name      TEXT NOT NULL DEFAULT '',
    interval        INTEGER NOT NULL,
    timeout         REAL NOT NULL,
    retries         INTEGER NOT NULL,
    latency_warn_ms INTEGER,
    options         TEXT NOT NULL DEFAULT '{}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    status_since    REAL NOT NULL,
    created_at      REAL NOT NULL
);

CREATE TABLE checks (
    id         INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    state      TEXT NOT NULL,
    latency_ms REAL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_checks_monitor_ts ON checks(monitor_id, ts);

-- One row per monitor per hour, so long-range uptime never scans raw checks.
CREATE TABLE hourly (
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    hour       INTEGER NOT NULL,
    total      INTEGER NOT NULL,
    ok         INTEGER NOT NULL,
    lat_sum    REAL NOT NULL,
    lat_n      INTEGER NOT NULL,
    lat_max    REAL NOT NULL,
    PRIMARY KEY (monitor_id, hour)
) WITHOUT ROWID;

CREATE TABLE incidents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    severity   TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    cause      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_incidents_monitor ON incidents(monitor_id, started_at);
CREATE INDEX idx_incidents_started ON incidents(started_at);

CREATE TABLE notifiers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL,
    config       TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_sent_at REAL,
    last_error   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
) WITHOUT ROWID;
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "site_title": "Network Health Monitor",
    "public_view": False,
    "notify_degraded": True,
    "retention_days": 14,
}

# Hourly rollups and incidents are small; keep them long enough for yearly reports.
ROLLUP_RETENTION_DAYS = 400


class Database:
    """Thin synchronous wrapper around one SQLite connection.

    Every statement is short, so calls are made directly from the event loop.
    The lock makes the connection safe to share with worker threads.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path} was created by a newer version of netmon "
                    f"(schema {version}, this build supports {SCHEMA_VERSION})"
                )
            if version == 0:
                self._conn.executescript(
                    f"BEGIN;{SCHEMA}PRAGMA user_version={SCHEMA_VERSION};COMMIT;"
                )

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # --- settings ---------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return DEFAULT_SETTINGS.get(key, default)
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_settings(self) -> dict[str, Any]:
        return {key: self.get_setting(key) for key in DEFAULT_SETTINGS}

    # --- sessions ---------------------------------------------------------

    def add_session(self, token_hash: str, expires_at: float) -> None:
        self._exec("INSERT INTO sessions(token_hash, expires_at) VALUES(?, ?)",
                   (token_hash, expires_at))

    def session_valid(self, token_hash: str) -> bool:
        row = self._one("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash,))
        return row is not None and row["expires_at"] > time.time()

    def delete_session(self, token_hash: str) -> None:
        self._exec("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_all_sessions(self) -> None:
        self._exec("DELETE FROM sessions")

    # --- monitors ---------------------------------------------------------

    @staticmethod
    def _monitor(row: sqlite3.Row) -> dict[str, Any]:
        monitor = {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "target": row["target"],
            "group": row["group_name"],
            "interval": row["interval"],
            "timeout": row["timeout"],
            "retries": row["retries"],
            "latency_warn_ms": row["latency_warn_ms"],
            "enabled": bool(row["enabled"]),
            "status": row["status"],
            "status_since": row["status_since"],
            "created_at": row["created_at"],
        }
        monitor.update(json.loads(row["options"]))
        return monitor

    def list_monitors(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM monitors ORDER BY group_name COLLATE NOCASE, "
                         "name COLLATE NOCASE, id")
        return [self._monitor(row) for row in rows]

    def get_monitor(self, monitor_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM monitors WHERE id=?", (monitor_id,))
        return self._monitor(row) if row else None

    def create_monitor(self, fields: dict[str, Any], options: dict[str, Any]) -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO monitors(name, type, target, group_name, interval, timeout, retries, "
            "latency_warn_ms, options, enabled, status, status_since, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["name"], fields["type"], fields["target"], fields["group"],
                fields["interval"], fields["timeout"], fields["retries"],
                fields["latency_warn_ms"], json.dumps(options), int(fields["enabled"]),
                "pending" if fields["enabled"] else "paused", now, now,
            ),
        )
        return int(cur.lastrowid)

    def update_monitor(self, monitor_id: int, fields: dict[str, Any],
                       options: dict[str, Any]) -> None:
        self._exec(
            "UPDATE monitors SET name=?, type=?, target=?, group_name=?, interval=?, timeout=?, "
            "retries=?, latency_warn_ms=?, options=?, enabled=? WHERE id=?",
            (
                fields["name"], fields["type"], fields["target"], fields["group"],
                fields["interval"], fields["timeout"], fields["retries"],
                fields["latency_warn_ms"], json.dumps(options), int(fields["enabled"]),
                monitor_id,
            ),
        )

    def set_monitor_enabled(self, monitor_id: int, enabled: bool) -> None:
        self._exec("UPDATE monitors SET enabled=? WHERE id=?", (int(enabled), monitor_id))

    def set_monitor_status(self, monitor_id: int, status: str, since: float) -> None:
        self._exec("UPDATE monitors SET status=?, status_since=? WHERE id=?",
                   (status, since, monitor_id))

    def delete_monitor(self, monitor_id: int) -> None:
        self._exec("DELETE FROM monitors WHERE id=?", (monitor_id,))

    # --- checks & rollups -------------------------------------------------

    def record_check(self, monitor_id: int, ts: float, state: str,
                     latency_ms: float | None, detail: str) -> None:
        ok = 0 if state == "down" else 1
        has_latency = latency_ms is not None and ok
        latency = float(latency_ms) if has_latency else 0.0
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT INTO checks(monitor_id, ts, state, latency_ms, detail) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (monitor_id, ts, state, latency_ms, detail),
                )
                self._conn.execute(
                    "INSERT INTO hourly(monitor_id, hour, total, ok, lat_sum, lat_n, lat_max) "
                    "VALUES(?, ?, 1, ?, ?, ?, ?) "
                    "ON CONFLICT(monitor_id, hour) DO UPDATE SET total=total+1, "
                    "ok=ok+excluded.ok, lat_sum=lat_sum+excluded.lat_sum, "
                    "lat_n=lat_n+excluded.lat_n, lat_max=max(lat_max, excluded.lat_max)",
                    (monitor_id, int(ts // 3600) * 3600, ok, latency, int(bool(has_latency)),
                     latency),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def recent_checks(self, monitor_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT ts, state, latency_ms, detail FROM checks WHERE monitor_id=? "
            "ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

    def checks_since(self, monitor_id: int, since: float) -> list[sqlite3.Row]:
        return self._all(
            "SELECT ts, state, latency_ms FROM checks WHERE monitor_id=? AND ts>=? ORDER BY ts",
            (monitor_id, since),
        )

    def hourly_since(self, monitor_id: int, since: float) -> list[sqlite3.Row]:
        return self._all(
            "SELECT hour, total, ok, lat_sum, lat_n, lat_max FROM hourly "
            "WHERE monitor_id=? AND hour>=? ORDER BY hour",
            (monitor_id, int(since // 3600) * 3600),
        )

    def uptime_summary(self, now: float | None = None) -> dict[int, dict[str, float | None]]:
        """Uptime ratio per monitor over the last 24 hours, 7 days and 30 days."""
        now = now or time.time()
        day, week, month = (int((now - s) // 3600) * 3600 for s in (86400, 7 * 86400, 30 * 86400))
        rows = self._all(
            "SELECT monitor_id, "
            "SUM(CASE WHEN hour>=? THEN ok END) AS ok_d, "
            "SUM(CASE WHEN hour>=? THEN total END) AS n_d, "
            "SUM(CASE WHEN hour>=? THEN ok END) AS ok_w, "
            "SUM(CASE WHEN hour>=? THEN total END) AS n_w, "
            "SUM(ok) AS ok_m, SUM(total) AS n_m "
            "FROM hourly WHERE hour>=? GROUP BY monitor_id",
            (day, day, week, week, month),
        )

        def ratio(ok: int | None, total: int | None) -> float | None:
            return ok / total if total else None

        return {
            row["monitor_id"]: {
                "24h": ratio(row["ok_d"], row["n_d"]),
                "7d": ratio(row["ok_w"], row["n_w"]),
                "30d": ratio(row["ok_m"], row["n_m"]),
            }
            for row in rows
        }

    def report(self, days: int, now: float | None = None) -> dict[int, dict[str, Any]]:
        """Per-monitor uptime, latency and outage totals for the last ``days`` days."""
        now = now or time.time()
        since = now - days * 86400
        stats: dict[int, dict[str, Any]] = {}
        for row in self._all(
            "SELECT monitor_id, SUM(ok) AS ok, SUM(total) AS total, SUM(lat_sum) AS lat_sum, "
            "SUM(lat_n) AS lat_n, MAX(lat_max) AS lat_max FROM hourly WHERE hour>=? "
            "GROUP BY monitor_id",
            (int(since // 3600) * 3600,),
        ):
            stats[row["monitor_id"]] = {
                "uptime": row["ok"] / row["total"] if row["total"] else None,
                "avg_ms": row["lat_sum"] / row["lat_n"] if row["lat_n"] else None,
                "max_ms": row["lat_max"] if row["lat_n"] else None,
                "outages": 0,
                "downtime_s": 0.0,
            }
        for row in self._all(
            "SELECT monitor_id, COUNT(*) AS outages, "
            "SUM(COALESCE(ended_at, ?) - MAX(started_at, ?)) AS downtime "
            "FROM incidents WHERE severity='down' AND COALESCE(ended_at, ?) >= ? "
            "GROUP BY monitor_id",
            (now, since, now, since),
        ):
            entry = stats.setdefault(
                row["monitor_id"],
                {"uptime": None, "avg_ms": None, "max_ms": None, "outages": 0, "downtime_s": 0.0},
            )
            entry["outages"] = row["outages"]
            entry["downtime_s"] = row["downtime"] or 0.0
        return stats

    def prune(self, retention_days: int, now: float | None = None) -> int:
        """Delete raw checks past retention plus very old rollups, incidents and sessions."""
        now = now or time.time()
        old = now - ROLLUP_RETENTION_DAYS * 86400
        cur = self._exec("DELETE FROM checks WHERE ts<?", (now - retention_days * 86400,))
        self._exec("DELETE FROM hourly WHERE hour<?", (old,))
        self._exec("DELETE FROM incidents WHERE ended_at IS NOT NULL AND ended_at<?", (old,))
        self._exec("DELETE FROM sessions WHERE expires_at<?", (now,))
        return cur.rowcount

    # --- incidents --------------------------------------------------------

    def open_incident(self, monitor_id: int, severity: str, started_at: float,
                      cause: str) -> int:
        cur = self._exec(
            "INSERT INTO incidents(monitor_id, severity, started_at, cause) VALUES(?, ?, ?, ?)",
            (monitor_id, severity, started_at, cause),
        )
        return int(cur.lastrowid)

    def close_incidents(self, monitor_id: int, ended_at: float) -> None:
        self._exec(
            "UPDATE incidents SET ended_at=MAX(?, started_at) "
            "WHERE monitor_id=? AND ended_at IS NULL",
            (ended_at, monitor_id),
        )

    def list_incidents(self, monitor_id: int | None = None, limit: int = 100,
                       open_only: bool = False) -> list[dict[str, Any]]:
        where, params = [], []
        if monitor_id is not None:
            where.append("i.monitor_id=?")
            params.append(monitor_id)
        if open_only:
            where.append("i.ended_at IS NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._all(
            "SELECT i.id, i.monitor_id, m.name AS monitor_name, i.severity, i.started_at, "
            f"i.ended_at, i.cause FROM incidents i JOIN monitors m ON m.id=i.monitor_id {clause} "
            "ORDER BY i.ended_at IS NULL DESC, i.started_at DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(row) for row in rows]

    # --- notifiers --------------------------------------------------------

    @staticmethod
    def _notifier(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "config": json.loads(row["config"]),
            "enabled": bool(row["enabled"]),
            "last_sent_at": row["last_sent_at"],
            "last_error": row["last_error"],
        }

    def list_notifiers(self) -> list[dict[str, Any]]:
        return [self._notifier(r) for r in self._all("SELECT * FROM notifiers ORDER BY id")]

    def get_notifier(self, notifier_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM notifiers WHERE id=?", (notifier_id,))
        return self._notifier(row) if row else None

    def create_notifier(self, name: str, kind: str, config: dict[str, Any],
                        enabled: bool) -> int:
        cur = self._exec(
            "INSERT INTO notifiers(name, type, config, enabled) VALUES(?, ?, ?, ?)",
            (name, kind, json.dumps(config), int(enabled)),
        )
        return int(cur.lastrowid)

    def update_notifier(self, notifier_id: int, name: str, kind: str, config: dict[str, Any],
                        enabled: bool) -> None:
        self._exec(
            "UPDATE notifiers SET name=?, type=?, config=?, enabled=?, last_error='' WHERE id=?",
            (name, kind, json.dumps(config), int(enabled), notifier_id),
        )

    def set_notifier_result(self, notifier_id: int, error: str) -> None:
        if error:
            self._exec("UPDATE notifiers SET last_error=? WHERE id=?", (error, notifier_id))
        else:
            self._exec("UPDATE notifiers SET last_error='', last_sent_at=? WHERE id=?",
                       (time.time(), notifier_id))

    def delete_notifier(self, notifier_id: int) -> None:
        self._exec("DELETE FROM notifiers WHERE id=?", (notifier_id,))
