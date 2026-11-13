"""Offline integration smoke tests with a fabricated GTFS *fixture*, never shipped as transit data."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from wsgiref.util import setup_testing_defaults

os.environ['SIP_AUTOLOAD']='0'
os.environ['SIP_DATA_DIR']=tempfile.mkdtemp(prefix='sip-gzm-test-')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


def zcsv(z,name,headers,rows):
    f=io.StringIO();w=csv.writer(f);w.writerow(headers);w.writerows(rows)
    z.writestr(name,f.getvalue().encode('utf-8'))


def fixture(path):
    day=date.today().strftime('%Y%m%d')
    with zipfile.ZipFile(path,'w') as z:
        zcsv(z,'routes.txt',['route_id','route_short_name','route_long_name','route_type'],[
             ['r904','904N','Nocny test',3],['r808','808','Drugi bus',3],['t0','T1','Tramwaj',0]])
        zcsv(z,'stops.txt',['stop_id','stop_code','stop_name','stop_lat','stop_lon','location_type'],[
             ['st1','01','Pogoń Grota-Roweckiego',50.2968,19.1332,0],['st2','02','Rudna Hallera',50.3050,19.1280,0],
             ['st3','03','Piaski Skrzyżowanie',50.3090,19.1120,0],['st4','04','Czeladź Cmentarz',50.3160,19.0980,0],
             ['st5','05','Czeladź Stare Miasto',50.3190,19.0860,0]])
        zcsv(z,'trips.txt',['trip_id','route_id','service_id','trip_headsign','direction_id'],[
             ['trip1','r904','today','Będzin Kościuszki',0],['trip2','r904','today','Będzin Kościuszki',0],
             ['trip3','r808','today','Centrum',0],['trip4','t0','today','Pętla',0]])
        zcsv(z,'stop_times.txt',['trip_id','stop_id','arrival_time','departure_time','stop_sequence','pickup_type'],[
             ['trip1','st1','18:00:00','18:00:00',1,0],['trip1','st2','18:02:00','18:02:00',2,0],
             ['trip1','st3','18:05:00','18:05:00',3,0],['trip1','st4','18:07:00','18:07:00',4,0],['trip1','st5','18:09:00','18:09:00',5,0],
             ['trip2','st1','19:00:00','19:00:00',1,0],['trip2','st2','19:02:00','19:02:00',2,0],
             ['trip3','st2','18:20:00','18:20:00',1,0],['trip3','st3','18:25:00','18:25:00',2,0],
             ['trip4','st4','18:00:00','18:00:00',1,0],['trip4','st5','18:07:00','18:07:00',2,0]])
        zcsv(z,'calendar.txt',['service_id','monday','tuesday','wednesday','thursday','friday','saturday','sunday','start_date','end_date'],[
             ['today',1,1,1,1,1,1,1,'20260101','20300101']])
        zcsv(z,'feed_info.txt',['feed_start_date','feed_end_date','feed_version'],[[day,day,'fixture-test']])


def call(path):
    captured={}
    def start(status,headers):captured.update(status=status,headers=headers)
    env={'REQUEST_METHOD':'GET','SCRIPT_NAME':'','PATH_INFO':path.split('?')[0],'QUERY_STRING':path.split('?',1)[1] if '?' in path else '',
         'SERVER_NAME':'localhost','SERVER_PORT':'5000','wsgi.url_scheme':'http'}
    body=b''.join(app.app(env,start))
    return captured['status'],body

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=Path(os.environ['SIP_DATA_DIR'])/'fixture.zip'
        fixture(cls.temp)
        assert app.refresh_gtfs(cls.temp)

    def test_root_does_not_404(self):
        status,body=call('/');self.assertEqual(status,'200 OK');self.assertIn(b'Emulator SIP GZM',body)
    def test_routes_buses(self):
        status,body=call('/api/routes?mode=bus');self.assertEqual(status,'200 OK')
        data=json.loads(body);self.assertEqual({r['number'] for r in data['routes']},{'904N','808'})
    def test_routes_all_include_tram(self):
        status,body=call('/api/routes?mode=all');self.assertEqual(status,'200 OK')
        data=json.loads(body);self.assertIn('T1',{r['number'] for r in data['routes']})
    def test_map_stops(self):
        status,body=call('/api/stops?w=19&e=19.3&s=50.2&n=50.5&z=14');self.assertEqual(status,'200 OK')
        data=json.loads(body);self.assertEqual(data['total'],5);self.assertEqual(len(data['items']),5)
    def test_map_clusters(self):
        _,body=call('/api/stops?w=19&e=19.3&s=50.2&n=50.5&z=9');self.assertTrue(json.loads(body)['clustered'])
    def test_line_variants(self):
        status,body=call('/api/route/r904');self.assertEqual(status,'200 OK')
        data=json.loads(body);self.assertEqual(data['route']['number'],'904N');self.assertEqual(data['active_trips'],2)
    def test_trip(self):
        status,body=call('/api/trip/trip1');self.assertEqual(status,'200 OK')
        stops=json.loads(body)['stops'];self.assertEqual([s['min'] for s in stops],[0,2,5,7,9])
    def test_departures(self):
        ref=datetime(2026,11,12,17,59,tzinfo=app.WARSAW)
        data=app.stop_view('st1',ref,180);self.assertTrue(data['departures']);self.assertEqual(data['departures'][0]['minutes'],1)
    def test_health(self):
        status,body=call('/health');self.assertEqual(status,'200 OK');self.assertTrue(json.loads(body)['ok'])
    def test_missing_route_404(self):
        status,body=call('/api/route/nonexistent');self.assertEqual(status,'404 Not Found')
    def test_rt_failure_honest(self):
        data=app.fetch_vehicles();self.assertIn('available',data)

if __name__=='__main__':unittest.main()
