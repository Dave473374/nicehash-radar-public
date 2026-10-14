// Integration checks use the ACTUAL pinned legacy engine and math, with synthetic I/O.
import test from 'node:test';
import assert from 'node:assert/strict';
import {collect} from './dist/adapter.mjs';
import {annotateScheduledMath} from './dist/math.mjs';
import worker, {RadarCore} from './dist/worker.mjs';

test('assembled Worker exports the coordinator and safe public routes',async()=>{
  assert.equal(typeof RadarCore,'function');
  assert.equal((await worker.fetch(new Request('https://test/tick',{method:'POST'}),{})).status,405);
  assert.equal((await worker.fetch(new Request('https://test/radar'),{})).status,404);
});
test('pinned public engine fails closed on unavailable NiceHash; no private APIs',async()=>{
  const old=globalThis.fetch,urls=[];
  globalThis.fetch=async url=>{urls.push(String(url));return new Response('unavailable',{status:503});};
  try {const f=await collect({});assert.equal(f.ok,false);
    assert.deepEqual(urls,['https://api2.nicehash.com/hashpower/api/v2/public/solo/package']);
  } finally {globalThis.fetch=old;}
});
test('actual difficulty audit has PASS and CRITICAL at known input ratios',()=>{
  const lambda=1e9*3600/(1e6*2**32);
  const p={package_hashrate_hps:1e9,duration_seconds:3600,primary_chain:{
    currency:'BCH',algorithm:'SHA256ASICBOOST',network_difficulty:1e6,expected_blocks:lambda}};
  let f={packages:[p]};annotateScheduledMath(f);assert.equal(p.math_consistency_shadow.status,'PASS');
  p.primary_chain.expected_blocks=lambda*2;annotateScheduledMath(f);assert.equal(p.math_consistency_shadow.status,'CRITICAL');
});
