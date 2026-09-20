import assert from 'node:assert/strict';
import {parseItems,itemWeight} from '../lib/packing.ts';
import {GET} from '../app/api/flights/route.ts';
const sample=parseItems('운동화 2켤레, 속옷 3개\n시계, 알수없는물건');
assert.equal(sample.length,4);assert.equal(sample[0].qty,2);assert.equal(itemWeight(sample[0])*sample[0].qty,1.4);assert.equal(sample[1].qty,3);assert.equal(itemWeight(sample[2]),null);assert.ok(sample[2].options.includes('스마트워치'));
assert.equal(itemWeight({...sample[3],comparison:'구두',ratio:'두 배 정도'}),1.6);
assert.equal(itemWeight({...sample[3],comparison:'구두',ratio:'모르겠음'}),null);
assert.equal(parseItems('팬티')[0].name,'속옷');assert.equal(parseItems(' , \n ').length,0);
delete process.env.AIRPORT_API_KEY;
const missing=await GET(new Request('http://localhost/api/flights?direction=departures&date=2026-10-01'));assert.match((await missing.json()).message,/인증키/);
process.env.AIRPORT_API_KEY='synthetic-test-key';let urlSeen='';const originalFetch=globalThis.fetch;
globalThis.fetch=async url=>{urlSeen=String(url);return Response.json({response:{header:{resultCode:'00'},body:{totalCount:2,items:{item:[{airline:'Sample',airlineCode:'AA',airportCode:'NRT',flightId:'TEST',st:'1200',firstdate:'20260101',lastdate:'20261231',ynThu:'Y'},{airline:'Other',airlineCode:'XX',airportCode:'NRT',flightId:'OTHER',st:'1300',firstdate:'20260101',lastdate:'20261231',ynThu:'Y'}]}}}})};
for(const direction of ['departures','arrivals']){const r=await GET(new Request(`http://localhost/api/flights?direction=${direction}&date=2026-10-01`));assert.equal((await r.json()).flights.length,2);assert.ok(urlSeen.includes(direction==='arrivals'?'getSPaxFlt4TripPlatformArrivals':'getSPaxFlt4TripPlatformDepartures'));}
globalThis.fetch=async()=>{throw new Error('timeout')};const failed=await GET(new Request('http://localhost/api/flights?direction=arrivals&date=2026-10-01'));assert.match((await failed.json()).message,/직접 입력/);globalThis.fetch=originalFetch;delete process.env.AIRPORT_API_KEY;
console.log('PASS: packing quantities, category selection, unknown weights, comparison ratios, both API directions, date filtering and unrestricted airlines, missing key and network failure. Live API not tested.');
