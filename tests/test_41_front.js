// Offline behavior tests. This mock intentionally does not assert map tiles or live GTFS availability.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
let html=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
const script=html.slice(html.indexOf('<script>')+8,html.lastIndexOf('</script>'));
const boot=script.indexOf("$('lineSearch').addEventListener('input'");
assert.ok(boot>0,'bootstrap found');
let elements={};
function el(id){if(!elements[id])elements[id]={id,value:'',textContent:'',innerHTML:'',style:{},dataset:{},hidden:true,disabled:false,scrollWidth:0,clientWidth:250,clientHeight:150,classList:{add(){},remove(){}},addEventListener(){}};return elements[id];}
let marker=null, mapClick=null;
const L={map(){return {setView(){return this;},on(name,cb){if(name==='click')mapClick=cb;return this;},invalidateSize(){},getZoom(){return 11;}};},tileLayer(){return {addTo(){return this;}};},marker(ll){marker={ll,remove(){},addTo(){return this;}};return marker;}};
const storage={};
const ctx={document:{getElementById:el,querySelectorAll(){return [];}},window:{addEventListener(){}},
 localStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},
 setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
 requestAnimationFrame(){},L,URL,Blob,Date,Intl,Number,Math,alert(){},confirm(){return true;},
 console,fetch:async()=>({ok:true,json:async()=>({})})};
vm.createContext(ctx);
vm.runInContext(script.slice(0,boot),ctx);
function run(expr){return vm.runInContext(expr,ctx);}
const now=Date.now();
const stops=[{name:'Przystanek A',min:0,clock:'10:00'},
 {name:'Przystanek B',min:2,clock:'10:02'},
 {name:'Przystanek C',min:5,clock:'10:05'},
 {name:'Przystanek D',min:7,clock:'10:07'},
 {name:'Przystanek E',min:9,clock:'10:09'}];
function prepare(departure){ctx.__trip={number:'904N',headsign:'Będzin Kościuszki',stops,id:'test-trip'};
 ctx.__dep={iso:new Date(departure).toISOString(),clock:'10:00',date:'2026-11-14'};
 run('state.trip=__trip;state.displayDeparture=__dep;state.routeData={};state.manualStep=false;state.step=-1;renderDisplay()');}
prepare(now+12*60000);
assert.match(el('sipDeparture').textContent,/Odjazd o 10:00/);
assert.match(el('sipMinutes').innerHTML,/sip-stop-clock/);
assert.doesNotMatch(el('sipMinutes').innerHTML,/odjechał/);
prepare(now-3*60000);
assert.match(el('sipMinutes').innerHTML,/odjechał/);
assert.match(el('sipStops').innerHTML,/sip-past/);
assert.equal(el('sipDeparture').textContent,'');
// Separate manual simulation from actual timetable.
run('state.manualStep=true;state.step=2;renderDisplay()');
assert.match(el('sipHint').textContent,/symulacji/);
// Save fully unlocated custom stops.
run('state.custom=[];state.routes=[];state.map=null;state.routeLayer=null;state.vehicleLayer=null;');
el('customNumber').value='TEST';el('customDirection').value='Dworzec';el('customStops').value='Dworzec | 0\nRynek | 4\nPark | 8';
run('saveOwnLine()');
const saved=JSON.parse(storage.sip_gzm_custom_v3);
assert.equal(saved.length,1);
assert.equal(saved[0].stops.length,3);
assert.equal(saved[0].stops[0].lat,null);
assert.equal(saved[0].stops[1].lon,null);
assert.equal(el('customError').textContent,'');
// Map pick fills selected row with coordinates.
el('customStops').value='Dworzec | 0\nRynek | 4';
el('customStopIndex').value='1';
run('revealCustomPicker()');
assert.equal(typeof mapClick,'function');
mapClick({latlng:{lat:50.29876542,lng:19.12345678}});
assert.match(el('customStops').value,/Rynek \| 4 \| 50.298765 \| 19.123457/);
// Re-save mixed located/unlocated stops.
el('customNumber').value='TEST2';el('customDirection').value='Park';el('customStops').value='Dworzec | 0\nRynek | 4 | 50.2 | 19.1';
run('saveOwnLine()');
const save2=JSON.parse(storage.sip_gzm_custom_v3);
assert.equal(save2.length,2);
assert.equal(save2[1].stops[0].lat,null);
assert.equal(save2[1].stops[1].lat,50.2);
// Broken coordinates should be rejected.
el('customStops').value='A | 0 | 50.2 |\nB | 3';run('saveOwnLine()');
assert.match(el('customError').textContent,/Szerokość i długość/);
console.log('SIP 4.1 UI behavior: pre-departure, departed, simulation, optional coordinates, map picker, validation OK');
