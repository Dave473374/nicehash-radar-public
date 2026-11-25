// Integration checks use the ACTUAL pinned legacy engine and math, with synthetic I/O.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
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
    // Reuse the approved origin from the exact reviewed source, rather than
    // defining another network origin outside the repository's host policy.
    // All I/O above is mocked; the source digest is independently checked here.
    const source=readFileSync(new URL('../recovery/worker.js',import.meta.url));
    assert.equal(createHash('sha256').update(source).digest('hex'),
      'd151375be73e45b51294dca1706a5b88dcffb70952762fe295cbe32f87d5c1f2');
    const expected=source.toString('utf8').match(/^const NICEHASH_URL = "([^"]+)";$/m)?.[1];
    assert.ok(expected);
    assert.equal(new URL(expected).protocol,'https:');
    assert.equal(new URL(expected).pathname,'/hashpower/api/v2/public/solo/package');
    assert.deepEqual(urls,[expected]);
  } finally {globalThis.fetch=old;}
});
test('actual difficulty audit has PASS and CRITICAL at known input ratios',()=>{
  const lambda=1e9*3600/(1e6*2**32);
  const p={package_hashrate_hps:1e9,duration_seconds:3600,primary_chain:{
    currency:'BCH',algorithm:'SHA256ASICBOOST',network_difficulty:1e6,expected_blocks:lambda}};
  let f={packages:[p]};annotateScheduledMath(f);assert.equal(p.math_consistency_shadow.status,'PASS');
  p.primary_chain.expected_blocks=lambda*2;annotateScheduledMath(f);assert.equal(p.math_consistency_shadow.status,'CRITICAL');
});
