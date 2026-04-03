from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any


_DB_LOCK = Lock()


def _db_path() -> Path:
    # Keep persistence inside the app folder.
    # Directory might not exist yet, so create it on first init.
    return Path(__file__).with_name("data") / "trips.db"


def _to_sqlite_dt(dt: datetime) -> str:
    # Use the same format sqlite will compare lexicographically.
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS confirmed_trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    confirmed_at TEXT NOT NULL,
                    filter_name TEXT,
                    num_cars INTEGER,
                    num_employees INTEGER,
                    total_km REAL,
                    total_cost REAL,
                    cost_per_car REAL,
                    cost_per_km REAL,
                    summary TEXT,
                    routes_json TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_confirmed_at ON confirmed_trips(confirmed_at);"
            )
            conn.commit()
        finally:
            conn.close()


def save_confirmed_trip(
    *,
    filter_name: str,
    num_cars: int,
    num_employees: int,
    total_km: float,
    total_cost: float,
    cost_per_car: float,
    cost_per_km: float,
    summary: str,
    routes_json: Any,
) -> int:
    """
    Persist a confirmed trip.

    Returns the inserted row id.
    """
    init_db()

    confirmed_at = _to_sqlite_dt(datetime.now())
    routes_json_str = json.dumps(routes_json, ensure_ascii=False)

    db_path = _db_path()
    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO confirmed_trips (
                    confirmed_at,
                    filter_name,
                    num_cars,
                    num_employees,
                    total_km,
                    total_cost,
                    cost_per_car,
                    cost_per_km,
                    summary,
                    routes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    confirmed_at,
                    filter_name,
                    int(num_cars),
                    int(num_employees),
                    float(total_km),
                    float(total_cost),
                    float(cost_per_car),
                    float(cost_per_km),
                    summary,
                    routes_json_str,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


@dataclass(frozen=True)
class TripStats:
    trips_count: int
    total_km: float
    total_cost: float


def get_stats_in_range(start: datetime, end: datetime) -> TripStats:
    """
    Return aggregated stats for trips with confirmed_at in [start, end).
    """
    init_db()
    db_path = _db_path()

    start_s = _to_sqlite_dt(start)
    end_s = _to_sqlite_dt(end)

    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as trips_count,
                    COALESCE(SUM(total_km), 0) as total_km,
                    COALESCE(SUM(total_cost), 0) as total_cost
                FROM confirmed_trips
                WHERE confirmed_at >= ? AND confirmed_at < ?
                """,
                (start_s, end_s),
            ).fetchone()
            return TripStats(
                trips_count=int(row[0] or 0),
                total_km=float(row[1] or 0.0),
                total_cost=float(row[2] or 0.0),
            )
        finally:
            conn.close()


@dataclass(frozen=True)
class FilterStats:
    filter_name: str
    trips_count: int
    total_cost: float


def get_report_by_filter(start: datetime, end: datetime) -> list[FilterStats]:
    """
    Return per-filter_name aggregated stats for trips in [start, end).
    """
    init_db()
    db_path = _db_path()

    start_s = _to_sqlite_dt(start)
    end_s = _to_sqlite_dt(end)

    with _DB_LOCK:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(filter_name, 'Все') as fn,
                    COUNT(*) as cnt,
                    COALESCE(SUM(total_cost), 0) as cost
                FROM confirmed_trips
                WHERE confirmed_at >= ? AND confirmed_at < ?
                GROUP BY fn
                ORDER BY fn
                """,
                (start_s, end_s),
            ).fetchall()
            return [
                FilterStats(
                    filter_name=str(row[0]),
                    trips_count=int(row[1]),
                    total_cost=float(row[2]),
                )
                for row in rows
            ]
        finally:
            conn.close()


def get_today_stats() -> TripStats:
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return get_stats_in_range(start, end)


def get_month_stats() -> TripStats:
    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # move to next month
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return get_stats_in_range(start, end)

