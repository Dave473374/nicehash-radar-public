import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import worker, {radarCore, collectScheduled, readScheduledFeed, scheduledHealth, annotateScheduledMath} from './worker-scheduled.mjs';
const NOW=Date.parse('2026-10-08T20:05:02Z'), H=3600000, KEY='runtime:buy-feed:scheduled';
let clock=NOW;
const RealDate=Date, realFetch=globalThis.fetch;
globalThis.Date=class extends RealDate { constructor(...args){super(...(args.length?args:[clock]));} static now(){return clock;} };
const quote={name:'Palladium S',id:'fixture',price:.0001,currencyMarket:'BTC',status:'A',available:true,
  projectedSpeed:.01,duration:3600,probabilityPrecision:1/(-Math.expm1(-.012)),probability:84,
  currencyAlgo:{currency:'LTC',miningAlgorithm:'SCRYPT',networkHashpower:2e13,networkDifficulty:1e8,blockReward:6.1875,blockRewardWithNhFee:6.25,blockTime:150}};
function mockFetch(fail=false) {
 let requests=0;
 globalThis.fetch=async url=>{
  requests++;
  if(String(url).includes('public/solo/package')) return fail ? new Response('Unavailable',{status:503}) : Response.json({packages:[quote]});
  if(String(url).includes('api.kraken.com')) {
   const pair=new URL(url).searchParams.get('pair');return Response.json({error:[],result:{[pair]:{c:[String(pair==='XBTEUR'?100000:200)]}}});
  }
  throw Error('Unexpected external request');
 };
 return ()=>requests;
}
function fixture(n=168) {
 const store=new Map(), counters={get:0,list:0,put:0}, writes=[];
 for(let i=1;i<=n;i++) {
  const ts=clock-i*H;
  store.set('snapshot:'+ts,{captured_at_ms:ts,captured_at:new Date(ts).toISOString(),packages:[{
   name:'Palladium S',price_btc:.0001,available:true,primary_expected_blocks:.01,
   model_hit_probability_percent:-Math.expm1(-.01)*100,primary_expected_reward_native:.01*6.1875}]});
 }
 const kv={store,counters,writes,
  async list({prefix,cursor}) {
   counters.list++;
   const all=[...store.keys()].filter(k=>k.startsWith(prefix)).sort(), start=Number(cursor||0), end=Math.min(start+1000,all.length);
   return {keys:all.slice(start,end).map(name=>({name})),list_complete:end===all.length,cursor:end===all.length?'':String(end)};
  },
  async get(k) {counters.get++;return structuredClone(store.get(k) ?? null);},
  async put(k,value,options) {counters.put++;writes.push({key:k,options});store.set(k,JSON.parse(value));}
 };
 return {RADAR_HISTORY:kv};
}
function event() {return {cron:'*/5 * * * *',scheduledTime:Math.floor(clock/300000)*300000};}
function reset() {clock=NOW;mockFetch();}
const get=env=>worker.fetch(new Request('https://test.invalid/buy-feed'),env).then(r=>r.json());

test('HTTP before first Cron cannot fabricate or collect data',async()=>{
 reset();const env=fixture(), requests=mockFetch();const result=await get(env);
 assert.equal(result.ok,false);assert.equal(result.production_health.state,'NOT_STARTED');assert.equal(result.checked_at,null);
 assert.equal(env.RADAR_HISTORY.counters.put,0);assert.equal(env.RADAR_HISTORY.counters.list,0);assert.equal(requests(),0);
});
test('scheduled handler captures, persists, computes Q and serves actual identical source time',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});const saved=env.RADAR_HISTORY.store.get(KEY);
 assert.equal(saved.collector.role,'CLOUDFLARE_CRON_SINGLE_PRODUCER');assert.equal(saved.history_saved,true);
 assert.equal(saved.production_health.state,'READY');assert.equal(saved.packages[0].final_signal,'STRONG BUY');
 assert.equal(saved.packages[0].history_trend.expected_blocks_per_btc_vs_24h_percent,20);
 assert.equal(saved.packages[0].math_consistency_shadow.production_override,false);
 assert.equal(env.RADAR_HISTORY.writes.length,2); // one hourly history key plus one runtime result
 clock+=60000;
 const calls=mockFetch();const before={...env.RADAR_HISTORY.counters};
 const served=await get(env);assert.equal(served.checked_at,saved.checked_at);assert.equal(served.collector.age_seconds,60);
 assert.equal(env.RADAR_HISTORY.counters.put,before.put);assert.equal(env.RADAR_HISTORY.counters.list,before.list);assert.equal(calls(),0);
});
test('repeated and older event do not append another history observation',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});const n=env.RADAR_HISTORY.counters.put;
 await worker.scheduled(event(),env,{});clock-=300000;await worker.scheduled(event(),env,{});assert.equal(env.RADAR_HISTORY.counters.put,n);
});
test('overlapping callbacks in one isolate cannot create two producers',async()=>{
 reset();const env=fixture();const results=await Promise.all([worker.scheduled(event(),env,{}),worker.scheduled(event(),env,{})]);
 assert.equal(env.RADAR_HISTORY.counters.put,2);assert.ok(results.some(r=>r.state==='SAME_ISOLATE_OVERLAP_SKIPPED'));
});
test('bad schedule and future event refused before external collection',async()=>{
 reset();const env=fixture();const calls=mockFetch();
 await assert.rejects(worker.scheduled({...event(),cron:'* * * * *'},env,{}),/UNEXPECTED_CRON/);
 await assert.rejects(worker.scheduled({...event(),scheduledTime:clock+1},env,{}),/INVALID_SCHEDULED_TIME/);
 assert.equal(env.RADAR_HISTORY.counters.put,0);assert.equal(calls(),0);
});
test('warming history remains explicit and cannot become artificial BUY',async()=>{
 reset();const env=fixture(4);await worker.scheduled(event(),env,{});const f=await get(env);
 assert.equal(f.collector.state,'RUNNING');assert.equal(f.production_health.state,'WARMING_UP');assert.equal(f.ok,false);
 assert.equal(f.packages[0].final_signal,'WAIT');assert.ok(f.production_health.reasons.length);
});
test('stale history can collect itself back into fresh warming state without HTTP writes',async()=>{
 reset();const env=fixture(0);const ts=clock-5*H;
 env.RADAR_HISTORY.store.set('snapshot:'+ts,{captured_at_ms:ts,captured_at:new Date(ts).toISOString(),packages:[]});
 await worker.scheduled(event(),env,{});assert.equal((await get(env)).production_health.state,'DEGRADED');
 clock+=300000;await worker.scheduled(event(),env,{});assert.equal((await get(env)).production_health.state,'WARMING_UP');
});
test('7 minute expiry blocks actionable cached data but never advances source timestamp',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});const source=(await get(env)).checked_at;
 clock+=420001;const f=await get(env);assert.equal(f.ok,false);assert.equal(f.checked_at,source);
 assert.equal(f.packages[0].final_signal,'WAIT');assert.equal(f.packages[0].final_signal_reason,'COLLECTOR_STALE');
 assert.equal(env.RADAR_HISTORY.store.get(KEY).packages[0].final_signal,'STRONG BUY');
});
test('first event after outage recovers without reset or backdating observations',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});clock+=600000;
 await worker.scheduled(event(),env,{});const f=await get(env);assert.equal(f.ok,true);
 assert.equal(f.collector.previous_start_gap_seconds,600);assert.equal(f.checked_at,new Date(clock).toISOString());
});
test('upstream failure persists an explicit failure and leaves historical observations intact',async()=>{
 reset();const env=fixture();const old=[...env.RADAR_HISTORY.store.keys()];mockFetch(true);
 await assert.rejects(worker.scheduled(event(),env,{}),/COLLECTOR_FAILED/);
 const f=await get(env);assert.equal(f.ok,false);assert.equal(f.packages.length,0);
 for(const k of old) assert.ok(env.RADAR_HISTORY.store.has(k));assert.equal(env.RADAR_HISTORY.writes.length,1);
 clock+=300000;mockFetch();await worker.scheduled(event(),env,{});assert.equal((await get(env)).ok,true);
});
test('KV write failure never claims successful publication',async()=>{
 reset();const env=fixture();env.RADAR_HISTORY.put=async()=>{throw Error('KV denied');};
 await assert.rejects(worker.scheduled(event(),env,{}),/KV denied/);assert.equal((await get(env)).ok,false);
});
test('missing binding or corrupt stored payload fail closed',async()=>{
 reset();assert.equal((await get({})).ok,false);await assert.rejects(worker.scheduled(event(),{},{}),/BINDING/);
 const env=fixture();env.RADAR_HISTORY.store.set(KEY,{ok:true,packages:[]});assert.equal((await get(env)).ok,false);
});
test('cached response cannot pretend an old deploy is the new source',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});
 env.RADAR_HISTORY.store.get(KEY).source_revision='old';assert.equal((await get(env)).ok,false);
});
test('public URL parameters and HTTP method cannot invoke producer',async()=>{
 reset();const env=fixture();await worker.fetch(new Request('https://test.invalid/buy-feed?cron=1&refresh=1'),env);
 const r=await worker.fetch(new Request('https://test.invalid/buy-feed',{method:'POST'}),env);
 assert.equal(r.status,405);assert.equal(env.RADAR_HISTORY.counters.put,0);
});
test('health endpoint is one read and reports actual acquisition versus decision state',async()=>{
 reset();const env=fixture(4);await worker.scheduled(event(),env,{});const n=env.RADAR_HISTORY.counters.get;
 const f=await (await worker.fetch(new Request('https://test.invalid/health'),env)).json();
 assert.equal(env.RADAR_HISTORY.counters.get-n,1);assert.equal(f.collector.state,'RUNNING');assert.equal(f.production_health.state,'WARMING_UP');
});
test('no-KV shadow endpoint remains independent from producer storage',async()=>{
 reset();const env={RADAR_HISTORY:new Proxy({}, {get:()=>{throw Error('SHADOW_TOUCHED_KV');}})};
 const f=await (await worker.fetch(new Request('https://test.invalid/buy-feed-shadow'),env)).json();
 assert.equal(f.shadow_sampling_mode,'NO_KV');assert.equal(f.history_saved,false);
});
test('cold module instance reuses runtime storage',async()=>{
 reset();const env=fixture();await worker.scheduled(event(),env,{});
 const cold=await import('./worker-scheduled.mjs?cold=1');const f=await(await cold.default.fetch(new Request('https://test.invalid/buy-feed'),env)).json();
 assert.equal(f.ok,true);assert.equal(env.RADAR_HISTORY.writes.length,2);
});
test('18 hour condition preserved over a simulated 24 hour five-minute collector run',async()=>{
 reset();const env=fixture(0);let firstReady=null;
 for(let i=0;i<288;i++) {
  await worker.scheduled(event(),env,{});const f=await get(env);
  if(f.ok && firstReady===null) firstReady=i;
  clock+=300000;
 }
 assert.ok(firstReady>=216,'cannot bypass eighteen hours');assert.ok(firstReady<230);
 assert.equal(env.RADAR_HISTORY.counters.put,576);
 assert.ok([...env.RADAR_HISTORY.store.keys()].filter(k=>k.startsWith('snapshot:')).length<=25);
});
test('operational replay does not change CURRENT engine economics on mature inputs',async()=>{
 reset();const env=fixture();const direct=await(await radarCore.fetch(new Request('https://test.invalid/buy-feed'),env)).json();
 const env2=fixture();await worker.scheduled(event(),env2,{});const f=await get(env2);
 assert.equal(f.packages[0].final_signal,direct.packages[0].final_signal);
 assert.deepEqual(f.packages[0].profitability,direct.packages[0].profitability);
});
test('JS math audit parity with unchanged production Python on representative and edge-case inputs',()=>{
 reset();
 const cases=[];
 for(const algorithm of ['SCRYPT','SHA256ASICBOOST','SHA256ASICBOOST_USDT','KHEAVYHASH'])
 for(const multiplier of [1,1.049,1.051,1.10,1.149,1.151,.8,NaN]) {
  const h=1e10,t=3600,d=1e8;
  cases.push({name:'fixture',final_signal:'GOOD',package_hashrate_hps:h,duration_seconds:t,
   primary_chain:{algorithm,currency:algorithm==='SCRYPT'?'LTC':algorithm==='KHEAVYHASH'?'KAS':'BTC',
    network_difficulty:d,expected_blocks:Number.isFinite(multiplier)?h/d*(t/2**32)*multiplier:null,
    network_hashpower_hps:2e13,block_time_seconds:150}});
 }
 for(const value of [true,null,'0x10','1e400',0,-1,'10000000000']) cases.push({name:'fixture',final_signal:'GOOD',
  package_hashrate_hps:value,duration_seconds:3600,primary_chain:{algorithm:'SCRYPT',currency:'DOGE',network_difficulty:1e8,expected_blocks:.08}});
 const feed={status:'BUY FEED OK',ok:true,packages:cases};
 const input=JSON.stringify(feed);
 const py=spawnSync('python3',['-c',"import sys,json;sys.path.insert(0,'scripts');from apply_math_consistency_shadow import annotate;print(json.dumps(annotate(json.load(sys.stdin))))"],{input,encoding:'utf8',cwd:new URL('../../',import.meta.url),timeout:10000});
 assert.equal(py.status,0,py.stderr);const expected=JSON.parse(py.stdout), actual=annotateScheduledMath(feed);
 function same(a,b,path='') {
  if(typeof a==='number'&&typeof b==='number') assert.ok(Math.abs(a-b)<=Math.max(1e-6,Math.abs(b)*1e-12),path);
  else if(Array.isArray(a)){assert.equal(a.length,b.length);a.forEach((x,i)=>same(x,b[i],path+'/'+i));}
  else if(a && typeof a==='object'){assert.deepEqual(Object.keys(a).sort(),Object.keys(b).sort());for(const k of Object.keys(a)) same(a[k],b[k],path+'/'+k);}
  else assert.equal(a,b,path);
 }
 same(actual,expected);
});
process.on('exit',()=>{globalThis.Date=RealDate;globalThis.fetch=realFetch;});
