import csv, io, os, zipfile, threading, time
from datetime import datetime, date
from urllib.request import Request, urlopen
from flask import Flask, jsonify, render_template
import requests

app = Flask(__name__)

GTFS_URL = os.environ.get(
    "GTFS_URL",
    "https://github.com/TransportGZM-GTFS-mirror/TransportGZM-GTFS-extended-ver/releases/latest/download/TransportGZM-GTFS.zip"
)
RT_VEHICLES = "https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/vehiclePositions"
RT_TRIPS = "https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/tripUpdates"

_data = {}
_loaded_at = 0
_lock = threading.Lock()
CACHE_SECONDS = 6 * 60 * 60

def rows_from_zip(zf, filename):
    try:
        raw = zf.read(filename).decode("utf-8-sig")
    except KeyError:
        return []
    return list(csv.DictReader(io.StringIO(raw)))

def load_gtfs(force=False):
    global _data, _loaded_at
    with _lock:
        if _data and not force and time.time() - _loaded_at < CACHE_SECONDS:
            return
        req = Request(GTFS_URL, headers={"User-Agent": "SIP-GZM-Emulator/1.0"})
        with urlopen(req, timeout=60) as response:
            content = response.read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = set(zf.namelist())
            routes = rows_from_zip(zf, "routes.txt")
            stops = rows_from_zip(zf, "stops.txt")
            trips = rows_from_zip(zf, "trips.txt")
            stop_times = rows_from_zip(zf, "stop_times.txt")
            calendar = rows_from_zip(zf, "calendar.txt")
            exceptions = rows_from_zip(zf, "calendar_dates.txt")
        _data = {
            "routes": routes, "stops": {s["stop_id"]: s for s in stops},
            "trips": trips, "stop_times": stop_times,
            "calendar": calendar, "exceptions": exceptions,
            "has_calendar": bool(calendar or exceptions)
        }
        _loaded_at = time.time()

def today_services():
    d = date.today()
    ds = d.strftime("%Y%m%d")
    active = set()
    weekday = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][d.weekday()]
    for row in _data["calendar"]:
        if row.get("start_date","") <= ds <= row.get("end_date","") and row.get(weekday) == "1":
            active.add(row["service_id"])
    for row in _data["exceptions"]:
        if row.get("date") == ds:
            if row.get("exception_type") == "1":
                active.add(row["service_id"])
            elif row.get("exception_type") == "2":
                active.discard(row["service_id"])
    # If a feed has no calendar info, show the available trips instead of returning nothing.
    return active if _data["has_calendar"] else None

def gtfs_seconds(value):
    try:
        parts = value.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        return 999999

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/routes")
def api_routes():
    try:
        load_gtfs()
        routes = []
        for r in _data["routes"]:
            rid = r.get("route_id")
            routes.append({
                "id": rid,
                "short_name": r.get("route_short_name") or r.get("route_long_name") or rid,
                "long_name": r.get("route_long_name",""),
                "type": r.get("route_type","")
            })
        routes.sort(key=lambda r: (not r["short_name"].isdigit(), int(r["short_name"]) if r["short_name"].isdigit() else 999999, r["short_name"]))
        return jsonify({"routes": routes, "source": "GTFS ZTM GZM", "updated": datetime.now().isoformat(timespec="seconds")})
    except Exception as e:
        return jsonify({"error": "Nie udało się pobrać rozkładu GTFS.", "details": str(e)}), 502

@app.route("/api/trips/<path:route_id>")
def api_trips(route_id):
    try:
        load_gtfs()
        active = today_services()
        trips = [t for t in _data["trips"] if t.get("route_id") == route_id and (active is None or t.get("service_id") in active)]
        trips.sort(key=lambda t: (t.get("trip_headsign",""), t.get("trip_short_name",""), t.get("trip_id","")))
        # Keep the UI responsive on very large feeds.
        result = []
        for t in trips[:1000]:
            result.append({
                "id": t.get("trip_id"),
                "headsign": t.get("trip_headsign") or t.get("trip_short_name") or t.get("trip_id"),
                "short_name": t.get("trip_short_name","")
            })
        return jsonify({"trips": result, "date": date.today().isoformat()})
    except Exception as e:
        return jsonify({"error": "Nie udało się pobrać kursów.", "details": str(e)}), 502

@app.route("/api/trip/<path:trip_id>")
def api_trip(trip_id):
    try:
        load_gtfs()
        trip = next((t for t in _data["trips"] if t.get("trip_id") == trip_id), None)
        if not trip:
            return jsonify({"error": "Nie znaleziono kursu."}), 404
        times = [x for x in _data["stop_times"] if x.get("trip_id") == trip_id]
        times.sort(key=lambda x: int(x.get("stop_sequence") or 0))
        result = []
        for st in times:
            stop = _data["stops"].get(st.get("stop_id"), {})
            result.append({
                "stop_id": st.get("stop_id"),
                "name": stop.get("stop_name") or st.get("stop_id"),
                "arrival": st.get("arrival_time") or st.get("departure_time") or "",
                "departure": st.get("departure_time") or st.get("arrival_time") or "",
                "sequence": st.get("stop_sequence",""),
                "lat": stop.get("stop_lat",""), "lon": stop.get("stop_lon","")
            })
        return jsonify({"trip": {
            "id": trip_id, "route_id": trip.get("route_id"),
            "headsign": trip.get("trip_headsign",""),
            "short_name": trip.get("trip_short_name","")
        }, "stops": result})
    except Exception as e:
        return jsonify({"error": "Nie udało się pobrać przystanków kursu.", "details": str(e)}), 502

@app.route("/api/realtime")
def api_realtime():
    # GTFS-RT is protobuf. The optional package is used to parse vehicle positions.
    try:
        from google.transit import gtfs_realtime_pb2
        r = requests.get(RT_VEHICLES, timeout=12, headers={"User-Agent": "SIP-GZM-Emulator/1.0"})
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        vehicles = []
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle
            vehicles.append({
                "id": entity.id,
                "label": v.vehicle.label if v.HasField("vehicle") else "",
                "trip_id": v.trip.trip_id if v.HasField("trip") else "",
                "route_id": v.trip.route_id if v.HasField("trip") else "",
                "lat": v.position.latitude if v.HasField("position") else None,
                "lon": v.position.longitude if v.HasField("position") else None,
                "timestamp": int(v.timestamp) if v.HasField("timestamp") else None
            })
        return jsonify({"vehicles": vehicles, "count": len(vehicles), "source": "GTFS-Realtime ZTM GZM"})
    except Exception as e:
        return jsonify({"error": "Dane na żywo są chwilowo niedostępne.", "details": str(e), "vehicles": []}), 502

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
