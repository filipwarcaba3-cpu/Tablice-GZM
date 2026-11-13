"""Catalog should be usable while the big stop_times import is still running."""
import os, sys, tempfile, unittest, zipfile, io, csv
from pathlib import Path
os.environ['SIP_AUTOLOAD']='0'
os.environ['SIP_DATA_DIR']=tempfile.mkdtemp(prefix='sip-catalog-')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class CatalogTests(unittest.TestCase):
 def test_stops_and_routes_appear_without_full_database(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'small.zip'
   with zipfile.ZipFile(p,'w') as z:
    for name,head,rows in [
      ('routes.txt',['route_id','route_short_name','route_long_name','route_type'],[['bus','904N','Nocna',3],['tram','T1','Tramwaj',0]]),
      ('stops.txt',['stop_id','stop_code','stop_name','stop_lat','stop_lon','location_type'],[['s1','01','Przystanek',50.29,19.13,0]])]:
     f=io.StringIO();w=csv.writer(f);w.writerow(head);w.writerows(rows);z.writestr(name,f.getvalue())
   prior_ready=app.STATE['ready']
   db=app.DB
   try:
    app.STATE['ready']=False
    app.DB=Path(td)/'no-full-db.sqlite3'
    app.load_catalog(p)
    routes=app.routes_list()
    stops=app.stop_bbox(19,50,20,51,14)
    self.assertEqual([x['number'] for x in routes],['904N'])
    self.assertEqual(stops['total'],1)
    self.assertEqual(app.STATE['catalog_ready'],True)
   finally:
    app.DB=db
    app.STATE['ready']=prior_ready
