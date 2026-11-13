"""Emulator SIP GZM 3.2 — WSGI + standard Python server.

Sources: GZM open data (CKAN GTFS) and official GTFS Realtime.
No invented GTFS or vehicle positions. All times use Europe/Warsaw.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo

import requests

try:
    from google.transit import gtfs_realtime_pb2
except Exception:
    gtfs_realtime_pb2 = None

LOG = logging.getLogger('sip-gzm')
logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'))
HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SIP_DATA_DIR', str(HERE/'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA/'gtfs.sqlite3'
CATALOG = DATA/'catalog.sqlite3'
ARCHIVE_CACHE = DATA/'gtfs-latest.zip'
WARSAW = ZoneInfo('Europe/Warsaw')
CKAN = 'https://otwartedane.metropoliagzm.pl/api/3/action/package_show?id=rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona'
MIRROR = 'https://github.com/TransportGZM-GTFS-mirror/TransportGZM-GTFS-extended-ver/releases/latest/download/TransportGZM-GTFS.zip'
LIVE = 'https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm'
UA = {'User-Agent': 'SIP-GZM-Edukacyjny/3.2 (GTFS viewer; CC-BY-4.0)'}
GATE = threading.RLock()
LOAD_LOCK = threading.Lock()
RT_LOCK = threading.Lock()
STATE = {'ready': False, 'loading': False, 'error': None, 'source': None,
         'updated_at': None, 'feed_start_date': None, 'feed_end_date': None,
         'stage': 'Oczekiwanie', 'attempt_started_at': None, 'catalog_ready': False, 'processed_stop_times': 0,
         'routes': 0, 'stops': 0, 'trips': 0, 'stop_times': 0}
RT = {'at': 0.0, 'vehicles': None, 'error': 'Jeszcze nie pobrano',
      'updated_at': None, 'sample_timestamp': None, 'trip_updates': {}, 'trip_at': 0.0,
      'trip_error': None, 'trip_timestamp': None}
LAST_REFRESH_ATTEMPT = 0.0
RETRY_SECONDS = int(os.environ.get('SIP_RETRY_SECONDS', '90'))
GTFS_URL = os.environ.get('SIP_GTFS_URL', '').strip()

SCHEMA = '''
PRAGMA journal_mode=DELETE;
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE routes(route_id TEXT PRIMARY KEY,short_name TEXT,long_name TEXT,route_type INTEGER,agency_id TEXT);
CREATE TABLE stops(stop_id TEXT PRIMARY KEY,code TEXT,name TEXT,lat REAL,lon REAL,location_type INTEGER,parent_station TEXT);
CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id INTEGER,shape_id TEXT);
CREATE TABLE stop_times(trip_id TEXT,stop_id TEXT,arrival TEXT,departure TEXT,seq INTEGER,pickup INTEGER);
CREATE TABLE calendar(service_id TEXT PRIMARY KEY,mon INTEGER,tue INTEGER,wed INTEGER,thu INTEGER,fri INTEGER,sat INTEGER,sun INTEGER,start TEXT,end TEXT);
CREATE TABLE calendar_dates(service_id TEXT,day TEXT,exception INTEGER);
-- Indexes are created after bulk insert to speed up first-time load.
'''


def now_pl() -> datetime:
    return datetime.now(WARSAW)


def connect(path: Path | None = None):
    con = sqlite3.connect(str(path or DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    return con


def is_ready():
    return bool(STATE['ready'] and DB.exists())


def number(x, default=None):
    try:
        return float(x) if str(x or '').strip() else default
    except (TypeError, ValueError):
        return default


def integer(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def gtfs_seconds(value):
    if not value:
        return None
    try:
        p = str(value).split(':')
        if len(p) != 3: return None
        h, m, s = map(int, p)
        return h*3600 + m*60 + s if h >= 0 and 0 <= m < 60 and 0 <= s < 60 else None
    except (TypeError, ValueError):
        return None


def gtfs_clock(value):
    sec = gtfs_seconds(value)
    return f'{(sec//3600)%24:02d}:{(sec//60)%60:02d}' if sec is not None else '—'


def member_names(z):
    return {n.replace('\\', '/').split('/')[-1].lower(): n for n in z.namelist() if not n.endswith('/')}


def csv_rows(z, name):
    with z.open(name) as f:
        wrapper = io.TextIOWrapper(f, encoding='utf-8-sig', errors='replace', newline='')
        for row in csv.DictReader(wrapper):
            yield {str(k or '').strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}




def load_catalog(archive: Path):
    """Publish stops and bus line list *before* the large timetable import."""
    stage = CATALOG.with_suffix('.new')
    stage.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(archive) as z:
            names = member_names(z)
            con = connect(stage)
            try:
                con.execute('PRAGMA synchronous=OFF')
                con.executescript('''
                    CREATE TABLE routes(route_id TEXT PRIMARY KEY,short_name TEXT,long_name TEXT,route_type INTEGER);
                    CREATE TABLE stops(stop_id TEXT PRIMARY KEY,code TEXT,name TEXT,lat REAL,lon REAL,location_type INTEGER);
                ''')
                def rows(name, fn):
                    batch=[]
                    for r in csv_rows(z, names[name]):
                        v=fn(r)
                        if v is not None:batch.append(v)
                        if len(batch)>=10000:
                            con.executemany('INSERT OR REPLACE INTO '+('routes' if name=='routes.txt' else 'stops')+' VALUES ('+(','.join('?'*len(batch[0])))+')',batch);batch=[]
                    if batch:con.executemany('INSERT OR REPLACE INTO '+('routes' if name=='routes.txt' else 'stops')+' VALUES ('+(','.join('?'*len(batch[0])))+')',batch)
                rows('routes.txt', lambda r: (r['route_id'],r.get('route_short_name',''),r.get('route_long_name',''),integer(r.get('route_type'),3)))
                rows('stops.txt', lambda r: (r['stop_id'],r.get('stop_code',''),r.get('stop_name',''),number(r.get('stop_lat')),number(r.get('stop_lon')),integer(r.get('location_type'))))
                con.execute('CREATE INDEX ix_coords ON stops(lat,lon)')
                con.commit()
                counts={key:con.execute('SELECT COUNT(*) FROM '+key).fetchone()[0] for key in ('routes','stops')}
            finally:con.close()
        if not all(counts.values()):raise ValueError('Niekompletne dane przystanków lub linii')
        with GATE:
            os.replace(stage,CATALOG)
            STATE.update({'catalog_ready':True,**counts,'stage':'Przystanki i linie gotowe; import rozkładów'})
        LOG.info('Katalog gotowy: %s',counts)
    finally:stage.unlink(missing_ok=True)


def restore_catalog():
    if not CATALOG.is_file():return False
    try:
        con=connect(CATALOG)
        try:counts={key:con.execute('SELECT COUNT(*) FROM '+key).fetchone()[0] for key in ('routes','stops')}
        finally:con.close()
        if not all(counts.values()):return False
        STATE.update({'catalog_ready':True,**counts})
        return True
    except Exception:
        LOG.exception('Niepoprawny katalog przystanków')
        return False

def import_zip(zip_path: Path, target: Path):
    """Parse genuine GTFS to SQLite atomically; file kept on disk, not copied to RAM."""
    with zipfile.ZipFile(zip_path) as z:
        files = member_names(z)
        required = {'routes.txt', 'stops.txt', 'trips.txt', 'stop_times.txt'}
        if not required.issubset(files):
            raise ValueError('Archiwum nie jest GTFS: brak ' + ', '.join(sorted(required-set(files))))
        if target.exists(): target.unlink()
        con = connect(target)
        try:
            con.executescript(SCHEMA)
            con.execute('PRAGMA synchronous=OFF')
            con.execute('PRAGMA temp_store=FILE')
            con.execute('PRAGMA cache_size=-8192')
            con.execute('BEGIN')
            def bulk(file, sql, fields, transform=lambda r:r):
                if file not in files: return
                batch = []
                for r in csv_rows(z, files[file]):
                    rr = transform(r)
                    batch.append(tuple(rr.get(k) for k in fields))
                    if len(batch) >= 12000:
                        con.executemany(sql, batch)
                        batch = []
                        if file=='stop_times.txt':
                            STATE['processed_stop_times']+=12000
                            STATE['stage']='Import rozkładów: '+str(STATE['processed_stop_times'])+' wierszy'
                if batch:
                    con.executemany(sql, batch)
                    if file=='stop_times.txt':STATE['processed_stop_times']+=len(batch)
            bulk('routes.txt','INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?)',
                 ['route_id','route_short_name','route_long_name','route_type','agency_id'],
                 lambda r:{**r,'route_type':integer(r.get('route_type'),3)})
            bulk('stops.txt','INSERT OR REPLACE INTO stops VALUES (?,?,?,?,?,?,?)',
                 ['stop_id','stop_code','stop_name','stop_lat','stop_lon','location_type','parent_station'],
                 lambda r:{**r, 'stop_lat':number(r.get('stop_lat')), 'stop_lon':number(r.get('stop_lon')),
                           'location_type':integer(r.get('location_type'))})
            bulk('trips.txt','INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?)',
                 ['trip_id','route_id','service_id','trip_headsign','direction_id','shape_id'],
                 lambda r:{**r,'direction_id':integer(r.get('direction_id'))})
            bulk('stop_times.txt','INSERT INTO stop_times VALUES (?,?,?,?,?,?)',
                 ['trip_id','stop_id','arrival_time','departure_time','stop_sequence','pickup_type'],
                 lambda r:{**r, 'stop_sequence':integer(r.get('stop_sequence')),
                           'pickup_type':integer(r.get('pickup_type'))})
            bulk('calendar.txt','INSERT OR REPLACE INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)',
                 ['service_id','monday','tuesday','wednesday','thursday','friday','saturday','sunday','start_date','end_date'],
                 lambda r:{**r, **{x:integer(r.get(x)) for x in
                            ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')}})
            bulk('calendar_dates.txt','INSERT INTO calendar_dates VALUES (?,?,?)',
                 ['service_id','date','exception_type'],
                 lambda r:{**r,'exception_type':integer(r.get('exception_type'))})
            feed = next(csv_rows(z, files['feed_info.txt']), {}) if 'feed_info.txt' in files else {}
            meta = {'feed_start_date':feed.get('feed_start_date') or '',
                    'feed_end_date':feed.get('feed_end_date') or '',
                    'feed_version':feed.get('feed_version') or '',
                    'updated_at': datetime.now(timezone.utc).isoformat()}
            con.executemany('INSERT INTO meta VALUES(?,?)', list(meta.items()))
            STATE['stage']='Indeksowanie rozkładów'
            con.executescript('''
            CREATE INDEX ix_st_stop ON stop_times(stop_id);
            CREATE INDEX ix_st_trip ON stop_times(trip_id,seq);
            CREATE INDEX ix_tr_route ON trips(route_id);
            CREATE INDEX ix_tr_service ON trips(service_id);
            CREATE INDEX ix_cd_day ON calendar_dates(day);
            CREATE INDEX ix_stop_coords ON stops(lat,lon);
            ''')
            con.commit()
            counts = {k:con.execute('SELECT count(*) FROM '+k).fetchone()[0]
                      for k in ('routes','stops','trips','stop_times')}
            if not all(counts.values()): raise ValueError('GTFS zawiera puste podstawowe tabele')
            return counts, meta
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def state_from_db():
    if not DB.is_file(): return False
    try:
        with GATE:
            con = connect()
            try:
                meta = dict(con.execute('SELECT key,value FROM meta').fetchall())
                counts = {k:con.execute('SELECT count(*) FROM '+k).fetchone()[0]
                          for k in ('routes','stops','trips','stop_times')}
            finally: con.close()
            STATE.update({'ready':True,'catalog_ready':True,'loading':False,'error':None,'stage':'Gotowe',
                          'updated_at':meta.get('updated_at'),
                          'feed_start_date':meta.get('feed_start_date'),
                          'feed_end_date':meta.get('feed_end_date'),**counts})
        return True
    except Exception as exc:
        LOG.exception('Niewłaściwa baza GTFS')
        STATE.update({'ready':False,'error':str(exc)})
        return False


def download_gtfs() -> tuple[Path,str]:
    """Fast bounded discovery: explicit URL, newest CKAN resource, trusted mirror.

    Never iterate through a long list of past schedules while users wait.
    """
    if ARCHIVE_CACHE.is_file() and time.time()-ARCHIVE_CACHE.stat().st_mtime<4*3600:
        return ARCHIVE_CACHE,'lokalny cache archiwum GTFS'
    sources=[]
    problems=[]
    if GTFS_URL and GTFS_URL.startswith('https://'):
        sources.append((GTFS_URL,'konfigurowany SIP_GTFS_URL'))
    try:
        STATE['stage']='Sprawdzanie katalogu GZM'
        r=requests.get(CKAN, headers=UA, timeout=(5,9)); r.raise_for_status()
        data=r.json()
        if not data.get('success'): raise ValueError('CKAN success=false')
        resources=[x for x in data['result']['resources']
                   if str(x.get('url','')).lower().split('?')[0].endswith('.zip')]
        resources.sort(key=lambda x:(x.get('created') or x.get('last_modified') or '', x.get('name') or ''),reverse=True)
        if resources:
            sources.append((resources[0]['url'],resources[0].get('name','GTFS GZM')))
    except Exception as exc:
        problems.append('katalog GZM: '+str(exc))
    sources.append((MIRROR,'mirror GTFS GZM'))
    seen=set()
    for url,label in sources:
        if url in seen or not url.startswith('https://'):continue
        seen.add(url)
        file=None
        try:
            STATE['stage']='Pobieranie: '+label[:70]
            with requests.get(url,headers=UA,stream=True,timeout=(7,35),allow_redirects=True) as r:
                r.raise_for_status()
                fd,filename=tempfile.mkstemp(suffix='.zip',dir=DATA);file=Path(filename)
                count=0
                with os.fdopen(fd,'wb') as out:
                    for chunk in r.iter_content(chunk_size=262144):
                        if chunk:
                            count+=len(chunk)
                            if count>260*1024*1024:raise ValueError('GTFS >260 MB')
                            out.write(chunk)
            STATE['stage']='Sprawdzanie archiwum GTFS'
            with zipfile.ZipFile(file) as z:
                if not {'routes.txt','stops.txt','trips.txt','stop_times.txt'}.issubset(member_names(z)):
                    raise ValueError('niekompletne GTFS')
            return file,url
        except Exception as exc:
            problems.append(label+': '+str(exc))
            if file:file.unlink(missing_ok=True)
    raise RuntimeError('Nie udało się pobrać GTFS: '+'; '.join(problems[-3:]))


def refresh_gtfs(provided: Path | None=None):
    if not LOAD_LOCK.acquire(False):return False
    STATE['loading']=True
    STATE['error']=None
    STATE['stage']='Rozpoczęcie pobierania GTFS'
    STATE['attempt_started_at']=now_pl().isoformat()
    temp_db=DB.with_suffix('.new')
    feed_file = provided
    source = 'ręczny import GTFS' if provided else None
    try:
        if feed_file is None:feed_file,source=download_gtfs()
        STATE['stage']='Tworzenie szybkiej mapy przystanków i linii'
        STATE['processed_stop_times']=0
        try:load_catalog(feed_file)
        except Exception:
            LOG.exception('Nie udało się przygotować szybkiego katalogu')
        STATE['stage']='Przetwarzanie kursów i godzin odjazdów'
        counts,meta=import_zip(feed_file,temp_db)
        with GATE:os.replace(temp_db,DB)
        state_from_db()
        STATE['source']=source
        LOG.info('GTFS loaded: %s',counts)
        return True
    except Exception as exc:
        LOG.exception('Błąd ładowania GTFS')
        STATE['error']=str(exc)
        STATE['stage']='Błąd pobierania; ponowna próba za '+str(RETRY_SECONDS)+' s'
        STATE['loading']=False
        return False
    finally:
        temp_db.unlink(missing_ok=True)
        if provided is None and feed_file and feed_file!=ARCHIVE_CACHE:
            try:
                if STATE['ready']:os.replace(feed_file,ARCHIVE_CACHE)
                else:feed_file.unlink(missing_ok=True)
            except OSError:feed_file.unlink(missing_ok=True)
        LOAD_LOCK.release()


def feed_date_outdated():
    now=now_pl().date().strftime('%Y%m%d')
    end=STATE.get('feed_end_date')
    if end and len(end)==8 and end.isdigit():return end < now
    start=STATE.get('feed_start_date')
    if start and len(start)==8 and start.isdigit():return start > now
    stamp=STATE.get('updated_at')
    if not stamp:return True
    try: return (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()>18*3600
    except ValueError:return True


def loop_loader():
    global LAST_REFRESH_ATTEMPT
    if DB.exists():state_from_db()
    if not is_ready():restore_catalog()
    while True:
        try:
            if (not is_ready() or feed_date_outdated()) and time.monotonic()-LAST_REFRESH_ATTEMPT>RETRY_SECONDS:
                LAST_REFRESH_ATTEMPT=time.monotonic()
                refresh_gtfs()
        except Exception: LOG.exception('Błąd pętli aktualizacji')
        time.sleep(10)


def services_on(con, day:date):
    daystr=day.strftime('%Y%m%d'); name=('mon','tue','wed','thu','fri','sat','sun')[day.weekday()]
    current={r[0] for r in con.execute(
        f'SELECT service_id FROM calendar WHERE {name}=1 AND start<=? AND end>=?',(daystr,daystr))}
    for service,exc in con.execute('SELECT service_id,exception FROM calendar_dates WHERE day=?',(daystr,)):
        if exc==1:current.add(service)
        elif exc==2:current.discard(service)
    return current


def routes_list(bus=True):
    if not is_ready() and not STATE['catalog_ready']:return []
    with GATE:
        con=connect(DB if is_ready() else CATALOG)
        try:
            if not is_ready():
                rows=con.execute('SELECT route_id,short_name,long_name,route_type,0 AS trips FROM routes ORDER BY short_name COLLATE NOCASE').fetchall()
            else:
                rows=con.execute('''SELECT r.route_id,r.short_name,r.long_name,r.route_type,
                (SELECT COUNT(*) FROM trips t WHERE t.route_id=r.route_id) trips
                FROM routes r ORDER BY r.short_name COLLATE NOCASE''').fetchall()
            ret=[]
            for r in rows:
                kind=int(r['route_type']) if r['route_type'] is not None else 3
                if bus and not (kind==3 or kind==11 or 700<=kind<800):continue
                ret.append({'id':r['route_id'],'number':r['short_name'] or '—',
                            'name':r['long_name'] or '', 'type':kind,'trip_count':r['trips']})
            return ret
        finally:con.close()


def stop_bbox(w,s,e,n,zoom):
    if not is_ready() and not STATE['catalog_ready']:return {'items':[],'total':0,'clustered':False}
    w=max(-180,min(180,w));e=max(-180,min(180,e));s=max(-90,min(90,s));n=max(-90,min(90,n))
    if e<=w or n<=s:return {'items':[],'total':0,'clustered':False}
    with GATE:
        con=connect(DB if is_ready() else CATALOG)
        try:
            total=con.execute('SELECT COUNT(*) FROM stops WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND location_type=0',
                              (w,e,s,n)).fetchone()[0]
            # At low zoom group nearby platforms into visual clusters; zooming in exposes each stop.
            if zoom < 13 or total>2500:
                cell=0.18 if zoom<=8 else 0.10 if zoom<=9 else 0.06 if zoom<=10 else 0.03 if zoom<=11 else 0.016 if zoom<=12 else 0.009
                rows=con.execute('''SELECT AVG(lat),AVG(lon),COUNT(*) FROM stops
                    WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND location_type=0
                    GROUP BY CAST(lat/? AS INT), CAST(lon/? AS INT) LIMIT 900''',
                    (w,e,s,n,cell,cell)).fetchall()
                return {'items':[{'lat':r[0],'lon':r[1],'count':r[2],'cluster':True} for r in rows],
                        'total':total,'clustered':True}
            rows=con.execute('''SELECT stop_id,code,name,lat,lon FROM stops
                WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND location_type=0 LIMIT 2501''',
                (w,e,s,n)).fetchall()
            return {'items':[{'id':r[0],'code':r[1],'name':r[2],'lat':r[3],'lon':r[4]} for r in rows],
                    'total':total,'clustered':False}
        finally:con.close()


def trip_view(con, trip_id):
    t=con.execute('''SELECT t.*,r.short_name FROM trips t JOIN routes r ON r.route_id=t.route_id WHERE t.trip_id=?''',
                  (trip_id,)).fetchone()
    if not t:return None
    rows=con.execute('''SELECT st.*,s.name,s.code,s.lat,s.lon FROM stop_times st
        LEFT JOIN stops s ON st.stop_id=s.stop_id WHERE st.trip_id=? ORDER BY st.seq''',(trip_id,)).fetchall()
    if not rows:return None
    start=gtfs_seconds(rows[0]['departure'] or rows[0]['arrival']) or 0
    stops=[]
    for r in rows:
        sec=gtfs_seconds(r['arrival'] or r['departure'])
        delta=max(0,round(((sec if sec is not None else start)-start)/60))
        stops.append({'id':r['stop_id'],'name':r['name'] or r['stop_id'],
                      'lat':r['lat'],'lon':r['lon'],'code':r['code'],
                      'min':delta,'clock':gtfs_clock(r['departure'] or r['arrival']),
                      'sequence':r['seq']})
    return {'id':trip_id,'route_id':t['route_id'],'number':t['short_name'],
            'headsign':t['headsign'] or stops[-1]['name'], 'stops':stops}


def route_view(route_id, day):
    if not is_ready():return None
    with GATE:
        con=connect()
        try:
            route=con.execute('SELECT * FROM routes WHERE route_id=?',(route_id,)).fetchone()
            if route is None:return None
            active=services_on(con,day)
            trips=con.execute('SELECT trip_id,headsign,direction_id,service_id FROM trips WHERE route_id=?',(route_id,)).fetchall()
            active_trips=[t for t in trips if t['service_id'] in active]
            trip_stops=defaultdict(list)
            if active_trips:
                for row in con.execute('''SELECT st.trip_id,st.stop_id,st.departure,st.arrival
                    FROM stop_times st JOIN trips t ON t.trip_id=st.trip_id
                    WHERE t.route_id=? ORDER BY st.trip_id,st.seq''',(route_id,)):
                    trip_stops[row['trip_id']].append(row)
            # Keep a representative of each stop pattern, plus departures on requested day.
            by_pattern={}
            for t in active_trips:
                rows=trip_stops.get(t['trip_id'],[])
                if not rows:continue
                signature=(t['direction_id'],t['headsign'],tuple(r['stop_id'] for r in rows))
                if signature not in by_pattern:
                    key=hashlib.sha1(repr(signature).encode()).hexdigest()[:12]
                    by_pattern[signature]={'id':key,'trip_id':t['trip_id'],
                      'name':t['headsign'] or rows[-1]['stop_id'],'count':len(rows),'departures':[],
                      'direction':t['direction_id']}
                dep=rows[0]['departure'] or rows[0]['arrival']
                by_pattern[signature]['departures'].append({'trip_id':t['trip_id'],'clock':gtfs_clock(dep),
                                                             'seconds':gtfs_seconds(dep) or 0})
            selected_day=day
            if not by_pattern:
                # If selected day has no service, present the next day that really runs,
                # clearly labelled as a different day, never as today's departures.
                for shift in range(1,36):
                    candidate=day+timedelta(days=shift)
                    active_next=services_on(con,candidate)
                    avail=[t for t in trips if t['service_id'] in active_next]
                    if not avail:continue
                    selected_day=candidate
                    for t in avail:
                        rows=con.execute('SELECT stop_id,departure,arrival FROM stop_times WHERE trip_id=? ORDER BY seq',(t['trip_id'],)).fetchall()
                        if not rows:continue
                        signature=(t['direction_id'],t['headsign'],tuple(r['stop_id'] for r in rows))
                        if signature not in by_pattern:
                            key=hashlib.sha1(repr(signature).encode()).hexdigest()[:12]
                            by_pattern[signature]={'id':key,'trip_id':t['trip_id'],
                                'name':t['headsign'] or rows[-1]['stop_id'],'count':len(rows),
                                'departures':[],'direction':t['direction_id']}
                        dep=rows[0]['departure'] or rows[0]['arrival']
                        by_pattern[signature]['departures'].append({'trip_id':t['trip_id'],
                            'clock':gtfs_clock(dep),'seconds':gtfs_seconds(dep) or 0})
                    break
            if not by_pattern and trips:
                # GTFS sometimes advertises a route but contains no service within the
                # current validity window. Show an actual stored trip as a route
                # preview, NEVER claim it is a departure today.
                selected_day=None
                for t in trips[:250]:
                    rows=con.execute('SELECT stop_id,departure,arrival FROM stop_times WHERE trip_id=? ORDER BY seq',(t['trip_id'],)).fetchall()
                    if not rows:continue
                    signature=(t['direction_id'],t['headsign'],tuple(r['stop_id'] for r in rows))
                    if signature not in by_pattern:
                        key=hashlib.sha1(repr(signature).encode()).hexdigest()[:12]
                        by_pattern[signature]={'id':key,'trip_id':t['trip_id'],
                            'name':t['headsign'] or rows[-1]['stop_id'],'count':len(rows),
                            'departures':[],'direction':t['direction_id']}
                    dep=rows[0]['departure'] or rows[0]['arrival']
                    by_pattern[signature]['departures'].append({'trip_id':t['trip_id'],
                        'clock':gtfs_clock(dep),'seconds':gtfs_seconds(dep) or 0})
                # Distinguish the preview from a real scheduled date in the response.
            variants=sorted(by_pattern.values(),key=lambda p:(p['direction'],p['name'], -p['count']))
            for v in variants:
                v['departures'].sort(key=lambda d:d['seconds'])
                v['departures']=v['departures'][:100]
            return {'route':{'id':route_id,'number':route['short_name'],'name':route['long_name'],
                             'type':route['route_type']},'variants':variants[:55],
                    'date':selected_day.isoformat() if selected_day else None,'requested_date':day.isoformat(),
                    'preview_only':selected_day is None,'active_trips':sum(len(v['departures']) for v in variants) if selected_day else 0}
        finally:con.close()


def fetch_trip_predictions(force=False):
    """Retrieve GTFS-RT predictions; if unavailable keep timetable-only departures."""
    if gtfs_realtime_pb2 is None:
        return False
    now = time.monotonic()
    with RT_LOCK:
        if not force and now - RT['trip_at'] < 25:
            return RT['trip_error'] is None
        RT['trip_at'] = now
        try:
            resp = requests.get(LIVE+'/tripUpdates', headers=UA, timeout=(6, 15))
            resp.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            ts = int(feed.header.timestamp) if feed.header.HasField('timestamp') else 0
            if ts and abs(time.time()-ts) > 300:
                raise ValueError('Zbyt stary GTFS-RT tripUpdates')
            entries={}
            for entity in feed.entity:
                if not entity.HasField('trip_update'):continue
                tu=entity.trip_update
                trip_id=tu.trip.trip_id if tu.HasField('trip') else ''
                if not trip_id:continue
                for st in tu.stop_time_update:
                    if not st.stop_id:continue
                    event=st.departure if st.HasField('departure') else st.arrival if st.HasField('arrival') else None
                    if event is None:continue
                    moment=int(event.time) if event.HasField('time') else 0
                    delay=int(event.delay) if event.HasField('delay') else None
                    if moment or delay is not None:
                        entries[(trip_id,st.stop_id)]={'time':moment,'delay':delay}
            RT.update({'trip_updates':entries,'trip_timestamp':ts,'trip_error':None})
            return True
        except Exception as exc:
            RT.update({'trip_updates':{},'trip_error':str(exc)})
            return False


def live_predictions_loop():
    while True:
        try:fetch_trip_predictions(force=True)
        except Exception:LOG.exception('RT trip updates failed')
        time.sleep(35)


def stop_view(stop_id, ref=None, minutes=720):
    if not is_ready():return None
    current=ref or now_pl()
    window_end=current+timedelta(minutes=minutes)
    with GATE:
        con=connect()
        try:
            stop=con.execute('SELECT * FROM stops WHERE stop_id=?',(stop_id,)).fetchone()
            if stop is None:return None
            rows=con.execute('''SELECT st.trip_id,st.stop_id,st.seq,st.departure,st.arrival,st.pickup,
                   t.route_id,t.service_id,t.headsign,r.short_name
                   FROM stop_times st JOIN trips t ON st.trip_id=t.trip_id
                   JOIN routes r ON r.route_id=t.route_id
                   WHERE st.stop_id=? AND COALESCE(st.pickup,0) != 1''',(stop_id,)).fetchall()
            active={d:services_on(con,d) for d in
                    (current.date()-timedelta(days=1), current.date(),current.date()+timedelta(days=1))}
            departures=[]
            seen=set()
            for row in rows:
                sec=gtfs_seconds(row['departure'] or row['arrival'])
                if sec is None:continue
                for day,enabled in active.items():
                    if row['service_id'] not in enabled:continue
                    planned=datetime.combine(day,dt_time.min,tzinfo=WARSAW)+timedelta(seconds=sec)
                    if not current-timedelta(seconds=40)<=planned<=window_end:continue
                    identity=(row['trip_id'],row['seq'],day)
                    if identity in seen:continue
                    seen.add(identity)
                    departures.append({'trip_id':row['trip_id'],'route_id':row['route_id'],
                       'number':row['short_name'],'destination':row['headsign'] or '—',
                       'stop_id':row['stop_id'],'sequence':row['seq'],
                       'planned_iso':planned.isoformat(),'planned':planned.strftime('%H:%M'),
                       'minutes':max(0,math.ceil((planned-current).total_seconds()/60)),
                       'realtime':False})
            # Only label a departure live if GTFS-RT has a specific matching trip + stop.
            with RT_LOCK:
                estimates = dict(RT['trip_updates']) if (RT['trip_error'] is None and time.monotonic()-RT['trip_at']<100) else {}
            for item in departures:
                prediction=estimates.get((item['trip_id'],item['stop_id']))
                if not prediction:continue
                planned_dt=datetime.fromisoformat(item['planned_iso'])
                if prediction['time']:
                    eta=datetime.fromtimestamp(prediction['time'],WARSAW)
                elif prediction['delay'] is not None:
                    eta=planned_dt+timedelta(seconds=prediction['delay'])
                else:continue
                if abs((eta-planned_dt).total_seconds())>6*3600:continue
                item['predicted_iso']=eta.isoformat()
                item['predicted']=eta.strftime('%H:%M')
                item['minutes']=max(0,math.ceil((eta-current).total_seconds()/60))
                item['realtime']=True
            departures.sort(key=lambda x:x.get('predicted_iso',x['planned_iso']))
            departures=departures[:60]
            return {'stop':{'id':stop['stop_id'],'name':stop['name'],'code':stop['code'],
                            'lat':stop['lat'],'lon':stop['lon']},
                    'departures':departures,'as_of':current.isoformat(),'window_minutes':minutes,'feed_start_date':STATE.get('feed_start_date'),'feed_end_date':STATE.get('feed_end_date')}
        finally:con.close()


def fetch_vehicles(force=False):
    now=time.monotonic()
    with RT_LOCK:
        if not force and RT['vehicles'] is not None and now-RT['at']<20:
            return dict(available=True, vehicles=RT['vehicles'], updated_at=RT['updated_at'],
                        sample_timestamp=RT['sample_timestamp'],count=len(RT['vehicles']))
        if not force and RT['vehicles'] is None and now-RT['at']<30:
            return {'available':False,'vehicles':[],'count':0,'error':RT['error']}
        RT['at']=now
        if gtfs_realtime_pb2 is None:
            RT['error']='Brakuje biblioteki GTFS-RT (zainstaluj requirements.txt)'
            return {'available':False,'vehicles':[],'count':0,'error':RT['error']}
        try:
            r=requests.get(LIVE+'/vehiclePositions',timeout=(6,15),headers=UA)
            r.raise_for_status()
            feed=gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(r.content)
            vehicles=[]
            for ent in feed.entity:
                if not ent.HasField('vehicle'):continue
                v=ent.vehicle
                if not v.HasField('position'):continue
                stamp=int(v.timestamp) if v.HasField('timestamp') else 0
                # Don't present old positions as live.
                if stamp and abs(time.time()-stamp)>300:continue
                trip_id=v.trip.trip_id if v.HasField('trip') else ''
                route_id=v.trip.route_id if v.HasField('trip') else ''
                if not (-90<=v.position.latitude<=90 and -180<=v.position.longitude<=180):continue
                vehicles.append({'id':v.vehicle.id or ent.id,
                                 'label':v.vehicle.label if v.HasField('vehicle') else '',
                                 'trip_id':trip_id,'route_id':route_id,
                                 'lat':v.position.latitude,'lon':v.position.longitude,
                                 'timestamp':stamp or None})
            ids=list(set(x['trip_id'] for x in vehicles if x['trip_id']))
            mapping={}
            if is_ready() and ids:
                with GATE:
                    con=connect()
                    try:
                        for i in range(0,len(ids),400):
                            chunk=ids[i:i+400]
                            sql='SELECT t.trip_id,t.route_id,r.short_name FROM trips t JOIN routes r ON r.route_id=t.route_id WHERE t.trip_id IN ('+','.join('?'*len(chunk))+')'
                            for row in con.execute(sql,chunk):mapping[row['trip_id']]=(row['route_id'],row['short_name'])
                    finally:con.close()
            for v in vehicles:
                route=mapping.get(v['trip_id'])
                v['route_id']=v['route_id'] or (route[0] if route else '')
                v['number']=route[1] if route else (v['route_id'] or '?')
            RT.update({'vehicles':vehicles,'error':None,'updated_at':now_pl().isoformat(),
                       'sample_timestamp':int(feed.header.timestamp) if feed.header.HasField('timestamp') else None})
            return {'available':True,'vehicles':vehicles,'count':len(vehicles),
                    'updated_at':RT['updated_at'],'sample_timestamp':RT['sample_timestamp']}
        except Exception as exc:
            # Drop old data instead of showing stale positions as live.
            RT.update({'vehicles':None,'error':str(exc)})
            return {'available':False,'vehicles':[],'count':0,'error':str(exc)}


def parse_qs_1(environ,key,default=''):
    return parse_qs(environ.get('QUERY_STRING','')).get(key,[default])[0]


def json_response(start_response,payload,status='200 OK'):
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    start_response(status,[('Content-Type','application/json; charset=utf-8'),
                           ('Content-Length',str(len(body))),('Cache-Control','no-store'),
                           ('X-Content-Type-Options','nosniff')])
    return [body]


def app(environ,start_response):
    method=environ.get('REQUEST_METHOD','GET')
    path=unquote(environ.get('PATH_INFO','/'))
    try:
        if method=='GET' and path in ('/','/index.html'):
            body=(HERE/'templates'/'index.html').read_bytes()
            start_response('200 OK',[('Content-Type','text/html; charset=utf-8'),('Content-Length',str(len(body))),
                                     ('Cache-Control','no-cache')]);return [body]
        if method=='GET' and path=='/health':
            return json_response(start_response,{'ok':True,'version':'3.7','gtfs_ready':is_ready(),'catalog_ready':STATE['catalog_ready']})
        if method=='GET' and path=='/api/status':
            return json_response(start_response,{**STATE,'version':'3.7',
                    'today':now_pl().date().isoformat(),'timezone':'Europe/Warsaw'})
        if method=='GET' and path=='/api/routes':
            data=routes_list(parse_qs_1(environ,'mode','bus')!='all')
            return json_response(start_response,{'routes':data,'count':len(data)})
        if method=='GET' and path=='/api/stops':
            q=lambda key,default:float(parse_qs_1(environ,key,str(default)))
            return json_response(start_response,stop_bbox(q('w',18.5),q('s',49.8),q('e',19.7),q('n',50.8),q('z',10)))
        if method=='GET' and path.startswith('/api/stop/') and not is_ready():
            return json_response(start_response,{'error':'Odjazdy są jeszcze importowane; to nie oznacza, że autobus nie kursuje.','stage':STATE.get('stage'),'processed_stop_times':STATE.get('processed_stop_times',0)},'503 Service Unavailable')
        if method=='GET' and path.startswith('/api/stop/'):
            result=stop_view(path[len('/api/stop/'):],minutes=max(15,min(1440,integer(parse_qs_1(environ,'minutes','720'),720))))
            return json_response(start_response,result if result else {'error':'Nie znaleziono przystanku'},
                                 '200 OK' if result else '404 Not Found')
        if method=='GET' and path.startswith('/api/trip/') and not is_ready():
            return json_response(start_response,{'error':'Kursy są jeszcze importowane.'},'503 Service Unavailable')
        if method=='GET' and path.startswith('/api/trip/'):
            with GATE:
                if is_ready():
                    con=connect()
                    try:res=trip_view(con,path[len('/api/trip/'):])
                    finally:con.close()
                else:res=None
            return json_response(start_response,res if res else {'error':'Nie znaleziono kursu'},
                                 '200 OK' if res else '404 Not Found')
        if method=='GET' and path.startswith('/api/route/') and not is_ready():
            return json_response(start_response,{'error':'Trasy są jeszcze importowane. Lista linii jest już dostępna.','stage':STATE.get('stage')},'503 Service Unavailable')
        if method=='GET' and path.startswith('/api/route/'):
            raw=parse_qs_1(environ,'date',now_pl().date().isoformat())
            try:day=date.fromisoformat(raw)
            except ValueError:day=now_pl().date()
            result=route_view(path[len('/api/route/'):],day)
            return json_response(start_response,result if result else {'error':'Nie znaleziono linii'},
                                 '200 OK' if result else '404 Not Found')
        if method=='GET' and path=='/api/vehicles':return json_response(start_response,fetch_vehicles())
        if method=='POST' and path=='/api/refresh':
            global LAST_REFRESH_ATTEMPT
            if STATE['loading']:
                return json_response(start_response,{'ok':False,'error':'Aktualizacja już trwa'},'409 Conflict')
            if time.monotonic()-LAST_REFRESH_ATTEMPT<RETRY_SECONDS:
                return json_response(start_response,{'ok':False,'error':'Odświeżenie można ponowić za kilka minut'},'429 Too Many Requests')
            LAST_REFRESH_ATTEMPT=time.monotonic()
            threading.Thread(target=refresh_gtfs,daemon=True).start()
            return json_response(start_response,{'ok':True,'message':'Pobieranie danych uruchomione'})
        return json_response(start_response,{'error':'Nie znaleziono ścieżki: '+path},'404 Not Found')
    except Exception as exc:
        LOG.exception('Błąd serwera: %s %s',method,path)
        return json_response(start_response,{'error':'Błąd serwera: '+str(exc)},'500 Internal Server Error')


if os.environ.get('SIP_AUTOLOAD','1')=='1':
    threading.Thread(target=loop_loader,name='gtfs-auto-update',daemon=True).start()
    if gtfs_realtime_pb2 is not None:
        threading.Thread(target=live_predictions_loop,name='gzm-rt-updates',daemon=True).start()

if __name__=='__main__':
    port=int(os.environ.get('PORT','5000'))
    print(f'Emulator SIP GZM 3.7: http://127.0.0.1:{port}',flush=True)
    with make_server('0.0.0.0',port,app) as srv:srv.serve_forever()
