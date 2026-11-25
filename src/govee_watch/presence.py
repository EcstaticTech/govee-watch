from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from govee_watch.config import settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class PresenceStore:
    """SQLite-backed presence and RSSI telemetry store."""

    def __init__(self, db_path: Path | None = None, *, rolling_window: int = 20) -> None:
        self.db_path = db_path if db_path is not None else settings.presence_db_path
        self.rolling_window = rolling_window
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices_presence (
                    mac TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL,
                    last_rssi INTEGER,
                    avg_rssi REAL,
                    ad_count INTEGER NOT NULL DEFAULT 0,
                    adv_name TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rssi_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mac TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    rssi INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rssi_samples_mac_seen
                ON rssi_samples (mac, seen_at)
                """
            )

    def record_seen(
        self,
        mac: str,
        rssi: int | None,
        adv_name: str | None = None,
        *,
        seen_at: datetime | None = None,
        count_delta: int = 1,
    ) -> None:
        """Record a BLE advertisement observation for a device."""
        norm_mac = mac.strip().upper()
        timestamp = _to_iso(seen_at or _utc_now())
        delta = max(1, count_delta)

        with self._connect() as conn:
            if rssi is not None:
                conn.execute(
                    "INSERT INTO rssi_samples (mac, seen_at, rssi) VALUES (?, ?, ?)",
                    (norm_mac, timestamp, int(rssi)),
                )
                row = conn.execute(
                    """
                    SELECT AVG(rssi) AS avg_rssi
                    FROM (
                        SELECT rssi FROM rssi_samples
                        WHERE mac = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    """,
                    (norm_mac, self.rolling_window),
                ).fetchone()
                avg_rssi = float(row["avg_rssi"]) if row and row["avg_rssi"] is not None else float(rssi)
            else:
                existing = conn.execute(
                    "SELECT avg_rssi FROM devices_presence WHERE mac = ?",
                    (norm_mac,),
                ).fetchone()
                avg_rssi = float(existing["avg_rssi"]) if existing and existing["avg_rssi"] is not None else None

            conn.execute(
                """
                INSERT INTO devices_presence (mac, last_seen, last_rssi, avg_rssi, ad_count, adv_name)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    last_rssi = COALESCE(excluded.last_rssi, devices_presence.last_rssi),
                    avg_rssi = COALESCE(excluded.avg_rssi, devices_presence.avg_rssi),
                    ad_count = devices_presence.ad_count + excluded.ad_count,
                    adv_name = COALESCE(excluded.adv_name, devices_presence.adv_name)
                """,
                (norm_mac, timestamp, rssi, avg_rssi, delta, adv_name),
            )

    def get_status(
        self,
        mac: str,
        *,
        away_seconds: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return presence status for one MAC address, or None if never seen."""
        norm_mac = mac.strip().upper()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT mac, last_seen, last_rssi, avg_rssi, ad_count, adv_name FROM devices_presence WHERE mac = ?",
                (norm_mac,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_status(row, away_seconds=away_seconds, now=now)

    def list_statuses(
        self,
        *,
        away_seconds: int | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return presence status for all seen devices."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT mac, last_seen, last_rssi, avg_rssi, ad_count, adv_name FROM devices_presence ORDER BY last_seen DESC"
            ).fetchall()
        return [self._row_to_status(row, away_seconds=away_seconds, now=now) for row in rows]

    def prune_old_samples(
        self,
        retention_days: int | None = None,
        *,
        now: datetime | None = None,
    ) -> int:
        """Delete RSSI samples older than retention_days. Return deleted row count."""
        days = retention_days if retention_days is not None else settings.retention_days
        cutoff = _to_iso((now or _utc_now()) - timedelta(days=days))
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM rssi_samples WHERE seen_at < ?",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

    def _row_to_status(
        self,
        row: sqlite3.Row,
        *,
        away_seconds: int | None,
        now: datetime | None,
    ) -> dict[str, Any]:
        threshold = away_seconds if away_seconds is not None else settings.away_seconds
        ref_now = now or _utc_now()
        last_seen_dt = _from_iso(row["last_seen"])
        age_seconds = max(0.0, (ref_now - last_seen_dt).total_seconds())
        return {
            "mac": row["mac"],
            "last_seen": row["last_seen"],
            "last_rssi": row["last_rssi"],
            "avg_rssi": round(row["avg_rssi"], 1) if row["avg_rssi"] is not None else None,
            "ad_count": row["ad_count"],
            "adv_name": row["adv_name"],
            "age_seconds": round(age_seconds, 1),
            "is_present": age_seconds <= threshold,
        }


class PresenceDebouncer:
    """In-memory presence cache that throttles SQLite writes per device."""

    def __init__(
        self,
        store: PresenceStore,
        *,
        write_interval_seconds: float | None = None,
        rssi_delta_threshold: int = 10,
    ) -> None:
        self.store = store
        self.write_interval_seconds = (
            write_interval_seconds
            if write_interval_seconds is not None
            else settings.presence_write_interval
        )
        self.rssi_delta_threshold = rssi_delta_threshold
        self._live: dict[str, dict[str, Any]] = {}
        self._last_flushed_at: dict[str, datetime] = {}
        self._last_flushed_rssi: dict[str, int | None] = {}
        self._pending_counts: dict[str, int] = {}

    def observe(
        self,
        mac: str,
        rssi: int | None,
        adv_name: str | None = None,
        *,
        seen_at: datetime | None = None,
    ) -> bool:
        """Update in-memory state and persist to SQLite only when throttle allows.

        Returns True if a SQLite write occurred.
        """
        norm_mac = mac.strip().upper()
        now = seen_at or _utc_now()
        self._live[norm_mac] = {
            "mac": norm_mac,
            "last_seen": now,
            "last_rssi": rssi,
            "adv_name": adv_name,
        }
        self._pending_counts[norm_mac] = self._pending_counts.get(norm_mac, 0) + 1

        last_write = self._last_flushed_at.get(norm_mac)
        last_rssi = self._last_flushed_rssi.get(norm_mac)
        interval_elapsed = (
            last_write is None
            or (now - last_write).total_seconds() >= self.write_interval_seconds
        )
        rssi_jumped = (
            rssi is not None
            and last_rssi is not None
            and abs(rssi - last_rssi) >= self.rssi_delta_threshold
        )

        if interval_elapsed or rssi_jumped:
            self._persist(norm_mac, now)
            return True
        return False

    def _persist(self, norm_mac: str, seen_at: datetime) -> None:
        state = self._live[norm_mac]
        count = self._pending_counts.pop(norm_mac, 0)
        if count <= 0:
            return
        self.store.record_seen(
            norm_mac,
            state["last_rssi"],
            state["adv_name"],
            seen_at=state["last_seen"],
            count_delta=count,
        )
        self._last_flushed_at[norm_mac] = seen_at
        self._last_flushed_rssi[norm_mac] = state["last_rssi"]

    def get_live_status(
        self,
        mac: str,
        *,
        away_seconds: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Merge in-memory observation state with persisted SQLite metrics."""
        norm_mac = mac.strip().upper()
        persisted = self.store.get_status(norm_mac, away_seconds=away_seconds, now=now)
        live = self._live.get(norm_mac)
        if persisted is None and live is None:
            return None

        threshold = away_seconds if away_seconds is not None else settings.away_seconds
        ref_now = now or _utc_now()
        if live is not None:
            last_seen_dt = live["last_seen"]
            age_seconds = max(0.0, (ref_now - last_seen_dt).total_seconds())
            pending = self._pending_counts.get(norm_mac, 0)
            return {
                "mac": norm_mac,
                "last_seen": _to_iso(last_seen_dt),
                "last_rssi": live["last_rssi"],
                "avg_rssi": persisted["avg_rssi"] if persisted else live["last_rssi"],
                "ad_count": (persisted["ad_count"] if persisted else 0) + pending,
                "adv_name": live["adv_name"] or (persisted["adv_name"] if persisted else None),
                "age_seconds": round(age_seconds, 1),
                "is_present": age_seconds <= threshold,
            }
        return persisted

    def flush(self) -> int:
        """Flush all pending in-memory observations to SQLite."""
        flushed = 0
        now = _utc_now()
        for norm_mac in list(self._pending_counts):
            if self._pending_counts.get(norm_mac, 0) > 0:
                self._persist(norm_mac, now)
                flushed += 1
        return flushed


def log_unknown_device(
    mac: str,
    adv_name: str,
    rssi: int | None,
    mfg_data: dict[Any, Any],
    *,
    log_path: Path | None = None,
    seen_at: datetime | None = None,
) -> None:
    """Append an unregistered device observation to unknown_devices.jsonl."""
    target = log_path if log_path is not None else settings.unknown_log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    norm_mac = mac.strip().upper()
    encoded_mfg = {
        str(k): v.hex() if isinstance(v, (bytes, bytearray)) else str(v)
        for k, v in mfg_data.items()
    }
    entry = {
        "timestamp": _to_iso(seen_at or _utc_now()),
        "mac": norm_mac,
        "adv_name": adv_name,
        "rssi": rssi,
        "mfg_data": encoded_mfg,
        "promotion_template": {
            "mac": norm_mac,
            "name": adv_name,
            "model": _infer_model(adv_name),
            "role": "power_target",
            "location": "",
            "write_char": settings.ble_write_char,
            "packet_profile": "default",
            "notes": "Promoted from unknown_devices.jsonl",
        },
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _infer_model(adv_name: str) -> str:
    upper = adv_name.upper()
    for model in settings.target_models:
        if model in upper:
            return model
    return "UNKNOWN"
