from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, send_file

try:
    from google.transit import gtfs_realtime_pb2
    HAS_GTFS_RT = True
except Exception:
    gtfs_realtime_pb2 = None
    HAS_GTFS_RT = False

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SIP_DATA_DIR", APP_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gzm_gtfs.sqlite3"
CUSTOM_PATH = DATA_DIR / "custom_routes.json"
FEED_URL = "https://github.com/TransportGZM-GTFS-mirror/TransportGZM-GTFS-extended-ver/releases/latest/download/TransportGZM-GTFS.zip"
CKAN_URL = "https://otwartedane.metropoliagzm.pl/api/3/action/package_show?id=rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona"
RT_BASE = "https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm"
MAX_CONTENT_LENGTH = 220 * 1024 * 1024

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("sip-gzm")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

STATE_LOCK = threading.RLock()
DB_LOCK = threading.RLock()
IMPORT_LOCK = threading.Lock()
CUSTOM_LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "ready": False,
    "loading": False,
    "error": None,
    "updated_at": None,
    "source": "Transport GZM / ZTM — otwarte dane GTFS",
    "routes": 0,
    "stops": 0,
    "trips": 0,
    "stop_times": 0,
    "feed_info": {},
}
VEHICLE_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
ALERT_CACHE: dict[str, Any] = {"at": 0.0, "data": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS routes (
 route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT, long_name TEXT,
 description TEXT, route_type INTEGER, color TEXT, text_color TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS stops (
 stop_id TEXT PRIMARY KEY, code TEXT, name TEXT, description TEXT,
 lat REAL, lon REAL, zone_id TEXT, location_type INTEGER, parent_station TEXT
);
CREATE TABLE IF NOT EXISTS trips (
 trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, headsign TEXT,
 short_name TEXT, direction_id INTEGER, block_id TEXT, shape_id TEXT
);
CREATE TABLE IF NOT EXISTS stop_times (
 trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT,
 stop_sequence INTEGER, pickup_type INTEGER, drop_off_type INTEGER,
 shape_dist_traveled REAL
);
CREATE TABLE IF NOT EXISTS calendar (
 service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER,
 thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
 start_date TEXT, end_date TEXT
);
CREATE TABLE IF NOT EXISTS calendar_dates (
 service_id TEXT, service_date TEXT, exception_type INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trips_route ON trips(route_id);
CREATE INDEX IF NOT EXISTS idx_trips_service ON trips(service_id);
CREATE INDEX IF NOT EXISTS idx_stop_times_trip_seq ON stop_times(trip_id, stop_sequence);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id);
CREATE INDEX IF NOT EXISTS idx_calendar_dates_date ON calendar_dates(service_date);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_float(value: Any):
    try:
        s = str(value).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def gtfs_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        h, m, s = [int(x) for x in value.strip().split(":")]
        return h * 3600 + m * 60 + s
    except Exception:
        return None


def format_gtfs_time(value: str | None) -> str:
    seconds = gtfs_seconds(value)
    if seconds is None:
        return "—"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


def open_db(path: Path | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(str(path or DB_PATH), timeout=25)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=25000")
    return db


@contextmanager
def db_conn():
    # Serializing DB access also lets Windows replace the old SQLite file safely during import.
    with DB_LOCK:
        conn = open_db()
        try:
            yield conn
        finally:
            conn.close()


def db_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 0


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def zip_member_map(zf: zipfile.ZipFile) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        base = name.replace("\\", "/").split("/")[-1].lower()
        if base not in found:
            found[base] = name
    return found


def read_gtfs_rows(zf: zipfile.ZipFile, member: str):
    with zf.open(member, "r") as raw:
        wrapper = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(wrapper)
        for row in reader:
            yield {str(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}


def import_gtfs_zip_bytes(blob: bytes, target_path: Path) -> dict[str, Any]:
    """Build a fresh SQLite GTFS database. Existing data is not touched until validation succeeds."""
    if not blob or len(blob) < 100:
        raise ValueError("Plik GTFS jest pusty albo za mały.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("To nie jest prawidłowy plik ZIP z GTFS.") from exc

    members = zip_member_map(zf)
    required = ("routes.txt", "stops.txt", "trips.txt", "stop_times.txt")
    missing = [name for name in required if name not in members]
    if missing:
        raise ValueError("W archiwum brakuje plików: " + ", ".join(missing))

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN")

        for row in read_gtfs_rows(zf, members["routes.txt"]):
            rid = row.get("route_id", "")
            if not rid:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO routes VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, row.get("agency_id", ""), row.get("route_short_name", ""),
                 row.get("route_long_name", ""), row.get("route_desc", ""),
                 as_int(row.get("route_type"), -1), row.get("route_color", ""),
                 row.get("route_text_color", ""), row.get("route_url", "")),
            )

        for row in read_gtfs_rows(zf, members["stops.txt"]):
            sid = row.get("stop_id", "")
            if not sid:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO stops VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, row.get("stop_code", ""), row.get("stop_name", ""),
                 row.get("stop_desc", ""), as_float(row.get("stop_lat")),
                 as_float(row.get("stop_lon")), row.get("zone_id", ""),
                 as_int(row.get("location_type"), 0), row.get("parent_station", "")),
            )

        batch = []
        for row in read_gtfs_rows(zf, members["trips.txt"]):
            tid = row.get("trip_id", "")
            if not tid:
                continue
            batch.append((tid, row.get("route_id", ""), row.get("service_id", ""),
                          row.get("trip_headsign", ""), row.get("trip_short_name", ""),
                          as_int(row.get("direction_id"), 0), row.get("block_id", ""),
                          row.get("shape_id", "")))
            if len(batch) >= 5000:
                conn.executemany("INSERT OR REPLACE INTO trips VALUES(?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT OR REPLACE INTO trips VALUES(?,?,?,?,?,?,?,?)", batch)

        batch = []
        for row in read_gtfs_rows(zf, members["stop_times.txt"]):
            tid, sid = row.get("trip_id", ""), row.get("stop_id", "")
            if not tid or not sid:
                continue
            batch.append((tid, row.get("arrival_time", ""), row.get("departure_time", ""),
                          sid, as_int(row.get("stop_sequence"), 0),
                          as_int(row.get("pickup_type"), 0), as_int(row.get("drop_off_type"), 0),
                          as_float(row.get("shape_dist_traveled"))))
            if len(batch) >= 12000:
                conn.executemany("INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?)", batch)

        if "calendar.txt" in members:
            for row in read_gtfs_rows(zf, members["calendar.txt"]):
                sid = row.get("service_id", "")
                if not sid:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO calendar VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sid, as_int(row.get("monday")), as_int(row.get("tuesday")),
                     as_int(row.get("wednesday")), as_int(row.get("thursday")),
                     as_int(row.get("friday")), as_int(row.get("saturday")),
                     as_int(row.get("sunday")), row.get("start_date", ""),
                     row.get("end_date", "")),
                )

        if "calendar_dates.txt" in members:
            for row in read_gtfs_rows(zf, members["calendar_dates.txt"]):
                sid, day = row.get("service_id", ""), row.get("date", "")
                if sid and day:
                    conn.execute("INSERT INTO calendar_dates VALUES(?,?,?)",
                                 (sid, day, as_int(row.get("exception_type"), 0)))

        feed_info = {}
        if "feed_info.txt" in members:
            feed_info = next(read_gtfs_rows(zf, members["feed_info.txt"]), {})
        meta = {
            "updated_at": utc_now(),
            "source": "Transport GZM / ZTM — rozszerzony GTFS",
            "feed_publisher_name": feed_info.get("feed_publisher_name", "ZTM / Transport GZM"),
            "feed_publisher_url": feed_info.get("feed_publisher_url", "https://transportgzm.pl"),
            "feed_lang": feed_info.get("feed_lang", "pl"),
            "feed_start_date": feed_info.get("feed_start_date", ""),
            "feed_end_date": feed_info.get("feed_end_date", ""),
            "feed_version": feed_info.get("feed_version", ""),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                         [(k, str(v)) for k, v in meta.items()])
        conn.commit()
        counts = {name: table_count(conn, name) for name in ("routes", "stops", "trips", "stop_times")}
        if not counts["routes"] or not counts["stops"] or not counts["trips"] or not counts["stop_times"]:
            raise ValueError("GTFS został odczytany, ale brakuje podstawowych danych tras, przystanków lub kursów.")
        return {"counts": counts, "feed_info": meta}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        zf.close()


def update_state_from_db() -> None:
    if not db_exists():
        return
    try:
        with db_conn() as conn:
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM meta")}
            counts = {name: table_count(conn, name) for name in ("routes", "stops", "trips", "stop_times")}
        with STATE_LOCK:
            STATE.update({
                "ready": True,
                "loading": False,
                "error": None,
                "updated_at": meta.get("updated_at"),
                "source": meta.get("source", "Transport GZM / ZTM — otwarte dane GTFS"),
                "routes": counts["routes"],
                "stops": counts["stops"],
                "trips": counts["trips"],
                "stop_times": counts["stop_times"],
                "feed_info": meta,
            })
    except Exception as exc:
        log.exception("Nie udało się odczytać lokalnej bazy GTFS")
        with STATE_LOCK:
            STATE.update({"ready": False, "error": f"Baza GTFS jest uszkodzona: {exc}"})


def download_latest_feed() -> tuple[bytes, str]:
    headers = {"User-Agent": "Emulator-SIP-GZM/1.0 (non-commercial prototype)"}
    errors = []

    # Prefer the official GZM open-data catalogue so the first import uses its newest published ZIP.
    try:
        response = requests.get(CKAN_URL, headers=headers, timeout=(15, 30))
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError("CKAN API nie zwróciło success=true")
        resources = [r for r in payload.get("result", {}).get("resources", [])
                     if str(r.get("url", "")).lower().split("?")[0].endswith(".zip")]
        resources.sort(key=lambda r: (r.get("last_modified") or "", r.get("name") or ""), reverse=True)
        for resource in resources[:12]:
            try:
                r = requests.get(resource["url"], headers=headers, timeout=(15, 90))
                r.raise_for_status()
                if r.content[:2] == b"PK":
                    with zipfile.ZipFile(io.BytesIO(r.content)) as test_zip:
                        names = {n.replace("\\", "/").split("/")[-1].lower() for n in test_zip.namelist()}
                    if {"routes.txt", "stops.txt", "trips.txt", "stop_times.txt"}.issubset(names):
                        return r.content, resource["url"]
                errors.append(f"CKAN {resource.get('name','ZIP')}: archiwum nie zawiera pełnego GTFS")
            except Exception as exc:
                errors.append(f"CKAN {resource.get('name','ZIP')}: {exc}")
    except Exception as exc:
        errors.append(f"Oficjalny katalog CKAN: {exc}")

    # Secondary mirror; useful if the CKAN catalogue is temporarily unavailable.
    try:
        response = requests.get(FEED_URL, headers=headers, timeout=(15, 90))
        response.raise_for_status()
        blob = response.content
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as test_zip:
                names = {n.replace("\\", "/").split("/")[-1].lower() for n in test_zip.namelist()}
            if {"routes.txt", "stops.txt", "trips.txt", "stop_times.txt"}.issubset(names):
                return blob, FEED_URL
        errors.append("Mirror GitHub zwrócił archiwum bez wymaganych plików GTFS.")
    except Exception as exc:
        errors.append(f"Mirror GitHub: {exc}")

    raise RuntimeError("Nie udało się pobrać aktualnego GTFS. " + " | ".join(errors[-5:]))


def refresh_gtfs(blob: bytes | None = None, source: str | None = None) -> dict[str, Any]:
    if not IMPORT_LOCK.acquire(blocking=False):
        raise RuntimeError("Aktualizacja danych już trwa.")
    with STATE_LOCK:
        STATE["loading"] = True
        STATE["error"] = None
    tmp_path = DB_PATH.with_name(DB_PATH.name + ".new")
    try:
        if blob is None:
            blob, source = download_latest_feed()
        result = import_gtfs_zip_bytes(blob, tmp_path)
        # Replace only after a complete, validated import.
        with DB_LOCK:
            os.replace(tmp_path, DB_PATH)
        update_state_from_db()
        with STATE_LOCK:
            STATE["source"] = source or STATE.get("source")
            STATE["error"] = None
        return result
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        with STATE_LOCK:
            STATE["loading"] = False
            STATE["error"] = str(exc)
        log.exception("Aktualizacja GTFS nie powiodła się")
        raise
    finally:
        IMPORT_LOCK.release()


def startup_load() -> None:
    if db_exists():
        update_state_from_db()
        # A cached feed remains usable; refresh it in the background if it is older than 20 h.
        try:
            stamp = STATE.get("updated_at")
            old = True
            if stamp:
                old = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() > 20 * 3600
            if old:
                refresh_gtfs()
        except Exception:
            log.warning("Pozostawiam ostatnią poprawną kopię GTFS; odświeżenie nie wyszło.")
    else:
        try:
            refresh_gtfs()
        except Exception:
            log.warning("Nie udało się automatycznie pobrać GTFS przy starcie.")


if os.environ.get("SIP_AUTOLOAD", "1") == "1":
    threading.Thread(target=startup_load, name="gzm-gtfs-startup", daemon=True).start()


def mode_name(route_type: int) -> str:
    return {
        0: "Tramwaj", 1: "Metro", 2: "Kolej", 3: "Autobus", 4: "Prom",
        5: "Tramwaj linowy", 6: "Kolejka linowa", 7: "Kolejka", 11: "Trolejbus",
        12: "Monorail",
    }.get(route_type, "Inny")


def custom_routes() -> list[dict[str, Any]]:
    with CUSTOM_LOCK:
        if not CUSTOM_PATH.exists():
            return []
        try:
            payload = json.loads(CUSTOM_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except Exception:
            log.exception("Plik własnych linii jest nieczytelny")
            return []


def save_custom_routes(items: list[dict[str, Any]]) -> None:
    with CUSTOM_LOCK:
        CUSTOM_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CUSTOM_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CUSTOM_PATH)


def clean_custom_route(data: dict[str, Any], route_id: str | None = None) -> dict[str, Any]:
    short_name = str(data.get("short_name", data.get("line", ""))).strip()[:16]
    headsign = str(data.get("headsign", data.get("direction", ""))).strip()[:120]
    name = str(data.get("name", "")).strip()[:160]
    raw_stops = data.get("stops", [])
    if not short_name:
        raise ValueError("Podaj numer lub oznaczenie linii.")
    if not headsign:
        raise ValueError("Podaj kierunek jazdy.")
    if not isinstance(raw_stops, list) or len(raw_stops) < 2 or len(raw_stops) > 120:
        raise ValueError("Linia musi mieć od 2 do 120 przystanków.")
    cleaned = []
    cumulative = 0
    for index, stop in enumerate(raw_stops):
        if not isinstance(stop, dict):
            raise ValueError("Nieprawidłowy zapis przystanku.")
        stop_name = str(stop.get("name", "")).strip()[:160]
        if not stop_name:
            raise ValueError(f"Przystanek {index + 1} nie ma nazwy.")
        interval = max(0, min(240, as_int(stop.get("minutes_from_previous", stop.get("interval", 0)), 0)))
        if index:
            cumulative += interval
        lat = as_float(stop.get("lat"))
        lon = as_float(stop.get("lon"))
        if lat is not None and not -90 <= lat <= 90:
            raise ValueError(f"Nieprawidłowa szerokość geograficzna przy przystanku {stop_name}.")
        if lon is not None and not -180 <= lon <= 180:
            raise ValueError(f"Nieprawidłowa długość geograficzna przy przystanku {stop_name}.")
        cleaned.append({
            "name": stop_name,
            "minutes_from_previous": interval,
            "minutes_from_start": cumulative,
            "lat": lat,
            "lon": lon,
        })
    return {
        "id": route_id or str(uuid.uuid4()),
        "short_name": short_name,
        "headsign": headsign,
        "name": name or f"Linia {short_name}",
        "route_type": 3,
        "stops": cleaned,
        "created_at": data.get("created_at") or utc_now(),
        "source": "Własna linia użytkownika",
    }


def active_services(conn: sqlite3.Connection, service_date: date) -> set[str]:
    day_key = service_date.strftime("%Y%m%d")
    weekday = service_date.weekday()  # Monday=0
    day_cols = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    col = day_cols[weekday]
    active = {r["service_id"] for r in conn.execute(
        f"SELECT service_id FROM calendar WHERE {col}=1 AND start_date<=? AND end_date>=?",
        (day_key, day_key))}
    for row in conn.execute("SELECT service_id,exception_type FROM calendar_dates WHERE service_date=?", (day_key,)):
        if row["exception_type"] == 1:
            active.add(row["service_id"])
        elif row["exception_type"] == 2:
            active.discard(row["service_id"])
    return active


def trip_detail(trip_id: str) -> dict[str, Any] | None:
    if not db_exists():
        return None
    with db_conn() as conn:
        trip = conn.execute(
            "SELECT t.*,r.short_name,r.long_name,r.route_type FROM trips t "
            "LEFT JOIN routes r ON r.route_id=t.route_id WHERE t.trip_id=?", (trip_id,)).fetchone()
        if not trip:
            return None
        rows = conn.execute(
            "SELECT st.*,s.name stop_name,s.code stop_code,s.lat stop_lat,s.lon stop_lon "
            "FROM stop_times st LEFT JOIN stops s ON s.stop_id=st.stop_id "
            "WHERE st.trip_id=? ORDER BY st.stop_sequence", (trip_id,)).fetchall()
    if not rows:
        return None
    first = gtfs_seconds(rows[0]["departure_time"] or rows[0]["arrival_time"]) or 0
    stops = []
    for row in rows:
        sec = gtfs_seconds(row["arrival_time"] or row["departure_time"])
        dep = gtfs_seconds(row["departure_time"] or row["arrival_time"])
        elapsed = max(0, round(((sec if sec is not None else dep or first) - first) / 60))
        stops.append({
            "stop_id": row["stop_id"], "name": row["stop_name"] or row["stop_id"],
            "code": row["stop_code"], "lat": row["stop_lat"], "lon": row["stop_lon"],
            "arrival": format_gtfs_time(row["arrival_time"]),
            "departure": format_gtfs_time(row["departure_time"]),
            "minutes_from_start": elapsed,
            "minutes_from_previous": elapsed - (stops[-1]["minutes_from_start"] if stops else 0),
            "sequence": row["stop_sequence"],
        })
    return {
        "trip_id": trip_id, "route_id": trip["route_id"],
        "route_short_name": trip["short_name"] or "",
        "headsign": trip["headsign"] or (stops[-1]["name"] if stops else ""),
        "route_type": trip["route_type"], "stops": stops,
    }


def route_detail(route_id: str, service_date: date) -> dict[str, Any] | None:
    if route_id.startswith("custom:"):
        rid = route_id.split(":", 1)[1]
        item = next((x for x in custom_routes() if x.get("id") == rid), None)
        if not item:
            return None
        stops = []
        for i, stop in enumerate(item["stops"]):
            stops.append({
                "stop_id": f"custom-stop-{i}", "name": stop["name"], "code": "",
                "lat": stop.get("lat"), "lon": stop.get("lon"),
                "arrival": "—", "departure": "—",
                "minutes_from_start": stop["minutes_from_start"],
                "minutes_from_previous": stop["minutes_from_previous"], "sequence": i + 1,
            })
        return {
            "route": {"route_id": route_id, "short_name": item["short_name"],
                      "long_name": item["name"], "route_type": 3, "mode": "Własna linia"},
            "variants": [{"id": "custom", "direction_id": 0, "headsign": item["headsign"],
                          "stop_count": len(stops), "trip_count": 1, "sample_trip_id": None,
                          "departures": [], "sample_stops": stops}],
            "custom": True,
        }

    if not db_exists():
        return None
    with db_conn() as conn:
        route = conn.execute("SELECT * FROM routes WHERE route_id=?", (route_id,)).fetchone()
        if not route:
            return None
        active = active_services(conn, service_date)
        query = conn.execute(
            "SELECT t.trip_id,t.service_id,t.direction_id,t.headsign,"
            "st.stop_id,st.stop_sequence,st.arrival_time,st.departure_time,"
            "s.name stop_name,s.code stop_code,s.lat stop_lat,s.lon stop_lon "
            "FROM trips t JOIN stop_times st ON st.trip_id=t.trip_id "
            "LEFT JOIN stops s ON s.stop_id=st.stop_id "
            "WHERE t.route_id=? ORDER BY COALESCE(t.direction_id,0),t.headsign,t.trip_id,st.stop_sequence",
            (route_id,))
        patterns: dict[tuple, dict[str, Any]] = {}
        current_trip = None
        current_meta = None
        current_stops: list[dict[str, Any]] = []

        def flush_trip():
            if not current_meta or not current_stops:
                return
            direction = current_meta["direction_id"]
            headsign = current_meta["headsign"] or current_stops[-1]["name"]
            stop_key = tuple(s["stop_id"] for s in current_stops)
            key = (direction, headsign, stop_key)
            pat = patterns.get(key)
            if pat is None:
                digest = hashlib.sha1((route_id + repr(key)).encode("utf-8")).hexdigest()[:14]
                pat = {
                    "id": "p_" + digest, "direction_id": direction, "headsign": headsign,
                    "stop_count": len(current_stops), "trip_count": 0,
                    "sample_trip_id": current_meta["trip_id"],
                    "sample_stops": current_stops.copy(), "trips": [],
                }
                patterns[key] = pat
            dep = current_stops[0].get("departure") or current_stops[0].get("arrival")
            dep_sec = gtfs_seconds(dep)
            pat["trip_count"] += 1
            if current_meta["service_id"] in active:
                pat["trips"].append({
                    "trip_id": current_meta["trip_id"],
                    "departure_seconds": dep_sec if dep_sec is not None else 0,
                    "departure": format_gtfs_time(dep),
                })

        for row in query:
            if row["trip_id"] != current_trip:
                flush_trip()
                current_trip = row["trip_id"]
                current_meta = {
                    "trip_id": row["trip_id"], "service_id": row["service_id"],
                    "direction_id": as_int(row["direction_id"], 0),
                    "headsign": row["headsign"] or "",
                }
                current_stops = []
            current_stops.append({
                "stop_id": row["stop_id"], "name": row["stop_name"] or row["stop_id"],
                "code": row["stop_code"], "lat": row["stop_lat"], "lon": row["stop_lon"],
                "arrival": row["arrival_time"], "departure": row["departure_time"],
                "sequence": row["stop_sequence"],
            })
        flush_trip()

    variants = []
    for pat in patterns.values():
        departures = sorted(pat.pop("trips"), key=lambda x: x["departure_seconds"])
        pat["departures"] = departures[:250]
        pat["sample_stops"] = _offset_stops(pat["sample_stops"])
        variants.append(pat)
    variants.sort(key=lambda x: (x["direction_id"], x["headsign"], -x["stop_count"]))
    return {
        "route": {
            "route_id": route["route_id"], "short_name": route["short_name"] or "—",
            "long_name": route["long_name"] or "", "description": route["description"] or "",
            "route_type": route["route_type"], "mode": mode_name(route["route_type"]),
            "color": route["color"] or "", "text_color": route["text_color"] or "",
        },
        "variants": variants,
        "custom": False,
    }


def _offset_stops(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    first = gtfs_seconds(rows[0].get("departure") or rows[0].get("arrival"))
    if first is None:
        first = 0
    out = []
    prev = 0
    for i, row in enumerate(rows):
        sec = gtfs_seconds(row.get("arrival") or row.get("departure"))
        if sec is None:
            elapsed = prev
        else:
            elapsed = max(0, round((sec - first) / 60))
        out.append({
            "stop_id": row.get("stop_id"), "name": row.get("name"), "code": row.get("code"),
            "lat": row.get("lat"), "lon": row.get("lon"),
            "arrival": format_gtfs_time(row.get("arrival")),
            "departure": format_gtfs_time(row.get("departure")),
            "minutes_from_start": elapsed,
            "minutes_from_previous": elapsed - prev if i else 0,
            "sequence": row.get("sequence"),
        })
        prev = elapsed
    return out


def routes_payload() -> list[dict[str, Any]]:
    results = []
    if db_exists():
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT r.*,COUNT(DISTINCT t.trip_id) trip_count "
                "FROM routes r LEFT JOIN trips t ON t.route_id=r.route_id "
                "GROUP BY r.route_id ORDER BY CAST(r.short_name AS INTEGER),r.short_name,r.long_name"
            ).fetchall()
            for row in rows:
                results.append({
                    "id": row["route_id"], "route_id": row["route_id"],
                    "short_name": row["short_name"] or "—", "long_name": row["long_name"] or "",
                    "route_type": row["route_type"], "mode": mode_name(row["route_type"]),
                    "trip_count": row["trip_count"], "custom": False,
                })
    for item in custom_routes():
        results.append({
            "id": "custom:" + item["id"], "route_id": "custom:" + item["id"],
            "short_name": item["short_name"], "long_name": item["headsign"],
            "route_type": 3, "mode": "Własna linia", "trip_count": 1, "custom": True,
        })
    return results


def fetch_realtime(endpoint: str, cache: dict[str, Any], ttl: int = 10) -> dict[str, Any]:
    now = time.time()
    if cache.get("data") is not None and now - cache.get("at", 0) < ttl:
        return cache["data"]
    if not HAS_GTFS_RT:
        result = {"available": False, "error": "Brak biblioteki gtfs-realtime-bindings.", "count": 0, "vehicles": [], "alerts": []}
        cache.update({"at": now, "data": result})
        return result
    try:
        response = requests.get(f"{RT_BASE}/{endpoint}", timeout=(8, 15),
                                headers={"User-Agent": "Emulator-SIP-GZM/1.0"})
        response.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        vehicles = []
        alerts = []
        route_map = {}
        if endpoint == "vehiclePositions" and db_exists():
            trip_ids = [e.vehicle.trip.trip_id for e in feed.entity
                        if e.HasField("vehicle") and e.vehicle.trip.trip_id]
            if trip_ids:
                with db_conn() as conn:
                    placeholders = ",".join("?" for _ in set(trip_ids))
                    for r in conn.execute(
                        f"SELECT t.trip_id,t.route_id,r.short_name FROM trips t LEFT JOIN routes r ON r.route_id=t.route_id WHERE t.trip_id IN ({placeholders})",
                        tuple(set(trip_ids))):
                        route_map[r["trip_id"]] = {"route_id": r["route_id"], "short_name": r["short_name"] or ""}
        for entity in feed.entity:
            if endpoint == "vehiclePositions" and entity.HasField("vehicle"):
                v = entity.vehicle
                if not v.HasField("position"):
                    continue
                trip_id = v.trip.trip_id if v.HasField("trip") else ""
                mapped = route_map.get(trip_id, {})
                vehicles.append({
                    "id": v.vehicle.id or entity.id,
                    "label": v.vehicle.label if v.HasField("vehicle") else "",
                    "route_id": v.trip.route_id if v.HasField("trip") and v.trip.route_id else mapped.get("route_id", ""),
                    "line": mapped.get("short_name", ""),
                    "trip_id": trip_id,
                    "lat": v.position.latitude, "lon": v.position.longitude,
                    "bearing": v.position.bearing if v.position.HasField("bearing") else None,
                    "speed": v.position.speed if v.position.HasField("speed") else None,
                    "timestamp": int(v.timestamp) if v.HasField("timestamp") else None,
                    "stop_sequence": int(v.current_stop_sequence) if v.HasField("current_stop_sequence") else None,
                })
            elif endpoint == "alerts" and entity.HasField("alert"):
                a = entity.alert
                def translated(translations):
                    for tr in translations:
                        if tr.language in ("pl", "", "pl-PL") and tr.text:
                            return tr.text
                    return translations[0].text if translations else ""
                alerts.append({"id": entity.id, "header": translated(a.header_text.translation),
                               "description": translated(a.description_text.translation)})
        result = {"available": True, "error": None, "count": len(vehicles),
                  "vehicles": vehicles, "alerts": alerts, "updated_at": utc_now()}
        cache.update({"at": now, "data": result})
        return result
    except Exception as exc:
        result = {"available": False, "error": str(exc), "count": 0, "vehicles": [], "alerts": []}
        cache.update({"at": now, "data": result})
        return result


@app.after_request
def add_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"ok": True, "gtfs_ready": bool(STATE.get("ready")), "version": "1.0.0"})


@app.route("/api/status")
def api_status():
    with STATE_LOCK:
        payload = dict(STATE)
    live = fetch_realtime("vehiclePositions", VEHICLE_CACHE, ttl=10)
    payload["live_available"] = live.get("available", False)
    payload["live_vehicles"] = live.get("count", 0)
    payload["live_error"] = live.get("error")
    payload["realtime_library"] = HAS_GTFS_RT
    return jsonify(payload)


@app.route("/api/routes")
def api_routes():
    routes = routes_payload()
    mode = request.args.get("mode", "all")
    if mode != "all":
        if mode == "bus":
            routes = [r for r in routes if r["route_type"] in (3, 11) or r["custom"]]
        elif mode == "tram":
            routes = [r for r in routes if r["route_type"] == 0]
        else:
            routes = [r for r in routes if r["mode"].lower() == mode.lower()]
    return jsonify({"routes": routes, "total": len(routes)})


@app.route("/api/route/<path:route_id>")
def api_route_detail(route_id: str):
    raw_date = request.args.get("date", date.today().isoformat())
    try:
        service_date = date.fromisoformat(raw_date)
    except ValueError:
        service_date = date.today()
    detail = route_detail(route_id, service_date)
    if detail is None:
        return jsonify({"error": "Nie znaleziono linii albo nie załadowano danych GTFS."}), 404
    return jsonify(detail)


@app.route("/api/trip/<path:trip_id>")
def api_trip(trip_id: str):
    detail = trip_detail(trip_id)
    if detail is None:
        return jsonify({"error": "Nie znaleziono kursu."}), 404
    return jsonify(detail)


@app.route("/api/vehicles")
def api_vehicles():
    return jsonify(fetch_realtime("vehiclePositions", VEHICLE_CACHE, ttl=10))


@app.route("/api/alerts")
def api_alerts():
    return jsonify(fetch_realtime("alerts", ALERT_CACHE, ttl=30))


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if STATE.get("loading"):
        return jsonify({"ok": True, "message": "Aktualizacja już trwa."})
    def worker():
        try:
            refresh_gtfs()
        except Exception:
            pass
    threading.Thread(target=worker, name="gzm-gtfs-refresh", daemon=True).start()
    return jsonify({"ok": True, "message": "Rozpoczęto pobieranie najnowszych danych GZM."})


@app.route("/api/import-gtfs", methods=["POST"])
def api_import_gtfs():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Wybierz plik ZIP z GTFS."}), 400
    try:
        result = refresh_gtfs(file.read(), source=f"Import ręczny: {file.filename}")
        return jsonify({"ok": True, "counts": result["counts"], "message": "Dane GTFS zostały zaimportowane."})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/custom-routes", methods=["GET", "POST"])
def api_custom_routes():
    if request.method == "GET":
        return jsonify({"routes": custom_routes()})
    data = request.get_json(silent=True) or {}
    try:
        item = clean_custom_route(data)
        items = custom_routes()
        items.append(item)
        save_custom_routes(items)
        return jsonify({"ok": True, "route": item}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/custom-routes/<route_id>", methods=["DELETE"])
def api_custom_route_delete(route_id: str):
    items = custom_routes()
    filtered = [x for x in items if x.get("id") != route_id]
    if len(filtered) == len(items):
        return jsonify({"error": "Nie znaleziono własnej linii."}), 404
    save_custom_routes(filtered)
    return jsonify({"ok": True})


@app.route("/api/custom-routes/export")
def api_custom_export():
    if not CUSTOM_PATH.exists():
        save_custom_routes([])
    return send_file(CUSTOM_PATH, as_attachment=True, download_name="wlasne_linie_gzm.json",
                     mimetype="application/json")


@app.route("/api/custom-routes/import", methods=["POST"])
def api_custom_import():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Wybierz plik JSON z własnymi liniami."}), 400
    try:
        payload = json.loads(file.read().decode("utf-8-sig"))
        incoming = payload.get("routes", []) if isinstance(payload, dict) else payload
        if not isinstance(incoming, list):
            raise ValueError("Plik JSON musi zawierać listę linii albo pole routes.")
        existing = {x.get("id"): x for x in custom_routes()}
        imported = 0
        for raw in incoming:
            if not isinstance(raw, dict):
                continue
            raw_id = str(raw.get("id") or uuid.uuid4())
            cleaned = clean_custom_route(raw, route_id=raw_id)
            existing[raw_id] = cleaned
            imported += 1
        save_custom_routes(list(existing.values()))
        return jsonify({"ok": True, "imported": imported, "message": f"Zaimportowano {imported} linii."})
    except Exception as exc:
        return jsonify({"error": f"Nie udało się wczytać JSON: {exc}"}), 400


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Plik jest za duży. Maksymalny rozmiar to 220 MB."}), 413


if __name__ == "__main__":
    # Local/phone-on-Wi-Fi mode. Use the PC's LAN address on a phone on the same Wi-Fi.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
