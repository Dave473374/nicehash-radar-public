import test from 'node:test';
import assert from 'node:assert/strict';
import {evaluate,advance,initialState,MAX_AGE_MS,identity,timestamp,message} from './policy.mjs';
import {coordinator,worker} from './runtime.mjs';
const T=Date.parse('2026-10-14T10:00:00Z');
const clone=structuredClone;
function fixture(at=T) {
  return {schema_version:12,checked_at:new Date(at).toISOString(),ok:true,status:'BUY FEED OK',history_saved:true,
    market_prices_eur:{USDT:{eur:.9,fresh:true,age_seconds:0},BTC:{eur:70000,fresh:true,age_seconds:0},
      BCH:{eur:250,fresh:true,age_seconds:0},LTC:{eur:55,fresh:true,age_seconds:0},
      DOGE:{eur:.07,fresh:true,age_seconds:0},KAS:{eur:.03,fresh:true,age_seconds:0}},
    packages:[{name:'Silver 20',size:'20',currency_market:'USDT',price_native:20,available:true,final_signal:'BUY NOW',
      primary_chain:{currency:'BCH',algorithm:'SHA256ASICBOOST_USDT',expected_blocks:.002},
      merge_chain:null,package_hashrate_hps:2e12,duration_seconds:3600,economics:{complete:true,package_cost_eur:18,ev_eur:17.28},
      profitability:{complete:true,expected_return_percent:96},
      history_trend:{sample_count_24h:24,coverage_hours_24h:23,expected_blocks_per_btc_vs_24h_percent:11},
      math_validation:{overall_pass:true},
      math_consistency_shadow:{status:'PASS',checks:[{chain:'PRIMARY',status:'PASS'}]}}]};
}
const decision=f=>evaluate(f,T).packages[0];
const ev=(f,v)=>{f.packages[0].profitability.expected_return_percent=v;f.packages[0].economics.ev_eur=18*v/100;return f;};
const math=(f,status)=>{f.packages[0].math_consistency_shadow.checks[0].status=status;return f;};
const stateReport=(at=T,signal='BUY NOW')=>{const f=fixture(at);f.packages[0].final_signal=signal;return evaluate(f,at);};

test('primary USDT package is not confused with BTC; negative EV is labelled',()=>{
  const p=decision(fixture());assert.equal(p.costEur,18);assert.equal(p.priority,'PRIMARY');
  assert.equal(p.actionable,true);assert.equal(p.negativeEV,true);assert.match(message(p,new Date(T).toISOString()),/Negativna/);
});
test('negative EV warning and EV is not HIT probability',()=>assert.match(message(decision(fixture()),'time'),/EV ni verjetnost/));
test('90-95 primary PASS is capped at GOOD',()=>assert.equal(decision(ev(fixture(),92)).signal,'GOOD'));
test('below 90 never actionable',()=>assert.equal(decision(ev(fixture(),89.9)).actionable,false));
test('secondary below 95 held',()=>{const f=ev(fixture(),92);f.packages[0].size='50';assert.equal(decision(f).actionable,false);});
test('WARNING can never be STRONG BUY',()=>{const f=math(fixture(),'WARNING');f.packages[0].final_signal='STRONG BUY';assert.equal(decision(f).signal,'BUY NOW');});
test('secondary GOOD WARNING held',()=>{const f=math(fixture(),'WARNING');f.packages[0].size='50';f.packages[0].final_signal='GOOD';assert.equal(decision(f).actionable,false);});
test('CRITICAL yields a safety block, not a buy',()=>assert.equal(decision(math(fixture(),'CRITICAL')).reason,'MATH_CRITICAL'));
test('UNKNOWN is data blocked',()=>assert.equal(decision(math(fixture(),'UNKNOWN')).state,'DATA_BLOCKED'));
test('merged UNKNOWN is not hidden by primary WARNING',()=>{
  const f=math(fixture(),'WARNING');f.packages[0].merge_chain={currency:'DOGE',algorithm:'SCRYPT',expected_blocks:.3};
  f.packages[0].math_consistency_shadow.checks.push({chain:'MERGE',status:'UNKNOWN'});
  assert.equal(decision(f).math,'UNKNOWN');assert.equal(decision(f).actionable,false);
});
test('unverified unsupported algorithm cannot use N/A to bypass math',()=>assert.equal(decision(math(fixture(),'NOT_APPLICABLE')).math,'UNKNOWN'));
test('known Kaspa N/A convention remains allowed',()=>{
  const f=math(fixture(),'NOT_APPLICABLE');f.packages[0].primary_chain={currency:'KAS',algorithm:'KHEAVYHASH',expected_blocks:50};
  assert.equal(decision(f).actionable,true);
});
test('missing merge leg for LTC is blocked',()=>{const f=fixture();f.packages[0].primary_chain.currency='LTC';assert.equal(decision(f).reason,'MERGED_LEG_MISSING');});
test('WAIT never promoted by attractive EV',()=>{const f=ev(fixture(),200);f.packages[0].final_signal='WAIT';assert.equal(decision(f).actionable,false);});
test('display limit not spending authority',()=>{const f=fixture();f.packages[0].price_native=200;f.packages[0].economics.package_cost_eur=180;f.packages[0].economics.ev_eur=172.8;assert.equal(decision(f).reason,'OUTSIDE_DISPLAY_LIMIT');});
for (const [name,mutate] of [
  ['stale',f=>f.checked_at=new Date(T-MAX_AGE_MS-1).toISOString()],
  ['future',f=>f.checked_at=new Date(T+1).toISOString()],
  ['no timezone',f=>f.checked_at='2026-10-14T10:00:00'],
  ['unknown schema',f=>f.schema_version=99],
  ['empty packages',f=>f.packages=[]],
  ['unhealthy',f=>f.ok=false],
  ['history not saved',f=>f.history_saved=false],
  ['duplicate packages',f=>f.packages.push(clone(f.packages[0]))],
  ['unknown currency',f=>f.packages[0].currency_market='ETH']]) {
  test(`${name} fails closed`,()=>{const f=fixture();mutate(f);const r=evaluate(f,T);assert.equal(r.state,'DATA_BLOCKED');assert.ok(!r.packages.some(p=>p.actionable));});
}
for (const [name,mutate] of [
  ['NaN cost',f=>f.packages[0].economics.package_cost_eur=NaN],
  ['Infinity EV',f=>f.packages[0].profitability.expected_return_percent=Infinity],
  ['string number',f=>f.packages[0].price_native='20'],
  ['FX missing',f=>delete f.market_prices_eur.USDT],
  ['FX stale after elapsed time',f=>f.market_prices_eur.USDT.age_seconds=901],
  ['currency mismatch',f=>f.packages[0].currency_market='BTC'],
  ['EV disagreement',f=>f.packages[0].economics.ev_eur=100],
  ['history insufficient',f=>f.packages[0].history_trend.sample_count_24h=17],
  ['base math failed',f=>f.packages[0].math_validation.overall_pass=false],
  ['missing math checks',f=>f.packages[0].math_consistency_shadow.checks=[]]]) {
  test(`${name} blocks affected package`,()=>{const f=fixture();mutate(f);assert.equal(decision(f).actionable,false);});
}
test('source input is never mutated',()=>{const f=fixture(),before=clone(f);evaluate(f,T);assert.deepEqual(f,before);});
test('offset timestamp accepted',()=>assert.equal(timestamp('2026-10-14T12:00:00+02:00'),T));
test('stale health still exposes computed source age',()=>assert.equal(evaluate(fixture(T-600000),T).sourceAgeSeconds,600));
test('policy queues once; same source cannot duplicate',()=>{
  const r=stateReport(),s=advance(initialState(),r,T);assert.equal(s.outbox.length,1);
  assert.equal(advance(s,r,T+1000).outbox.length,1);
});
test('valid exit then reentry creates a new episode',()=>{
  let s=advance(initialState(),stateReport(),T);s=advance(s,stateReport(T+300000,'WAIT'),T+300000);
  assert.equal(s.outbox.length,0);s=advance(s,stateReport(T+600000),T+600000);assert.equal(s.outbox.length,1);
});
test('stale evidence does not rearm or advance sourceAt',()=>{
  const s=advance(initialState(),stateReport(),T),r=evaluate(fixture(T),T+600000);
  const after=advance(s,r,T+600000);assert.deepEqual(after.observed,s.observed);assert.equal(after.sourceAt,s.sourceAt);
});
test('three failures create one notice and recovery is distinct',()=>{
  let s=initialState();const r=evaluate(null,T);
  for(let i=0;i<6;i++)s=advance(s,r,T+i);
  assert.equal(s.outbox.filter(e=>e.kind==='HEALTH').length,1);
  s=advance(s,stateReport(T+300000,'WAIT'),T+300000);
  assert.equal(s.outbox.filter(e=>e.kind==='HEALTH').length,0);assert.equal(s.outbox.filter(e=>e.kind==='RECOVERY').length,1);
});
test('partial data receives a deduped health notice',()=>{
  let s=initialState();for(let i=0;i<4;i++){const f=fixture(T+i);f.packages[0].history_trend.sample_count_24h=0;s=advance(s,evaluate(f,T+i),T+i);}
  assert.equal(s.outbox.filter(e=>e.kind==='HEALTH').length,1);
});
test('accepted signal is not repeated on a new source snapshot',()=>{
  let s=advance(initialState(),stateReport(),T);const e=s.outbox[0];s.accepted[e.key]=e.fingerprint;s.outbox=[];
  s=advance(s,stateReport(T+300000),T+300000);assert.equal(s.outbox.length,0);
});
test('never-attempted expired event can refresh from new evidence',()=>{
  let s=advance(initialState(),stateReport(),T);const id=s.outbox[0].id;
  s=advance(s,stateReport(T+600000),T+600000);assert.equal(s.outbox.length,1);assert.notEqual(s.outbox[0].id,id);
});
test('ambiguous expired event cannot be replaced with a new ID in same episode',()=>{
  let s=advance(initialState(),stateReport(),T);s.outbox[0].attempts=1;s.outbox[0].status='UNCERTAIN';
  s=advance(s,stateReport(T+600000),T+600000);assert.equal(s.outbox.length,0);assert.equal(Object.keys(s.uncertain).length,1);
});

class MemoryStorage {
  constructor(){this.data={};this.writes=0;}
  async get(k){return clone(this.data[k]);}
  async put(k,v){this.writes++;if(typeof k==='string')this.data[k]=clone(v);else Object.assign(this.data,clone(k));}
}
async function clocked(fn) {const old=Date.now;let now=T;Date.now=()=>now;try{await fn(v=>now=v);}finally{Date.now=old;}}
function harness({mode='observe',sender,collect}={}) {
  const storage=new MemoryStorage();let calls=0;
  const env={CORE_MODE:mode};if(sender)env.RADAR_NOTIFY={fetch:sender};
  const C=coordinator(async()=>{calls++;return collect ? collect() : fixture(Date.now());});
  const instance=new C({storage},env);
  return {instance,storage,env,calls:()=>calls,
    tick:()=>instance.tick({cron:'*/5 * * * *',scheduledTime:Date.now()}),
    read:path=>instance.fetch(new Request(`https://core.internal/${path}`)).then(r=>r.json())};
}
const accept=async(_url,init)=>{const p=JSON.parse(init.body);return new Response(JSON.stringify({accepted:true,event_id:p.event_id,receipt_id:'synthetic-receipt'}));};
test('HTTP read never collects or writes',()=>clocked(async()=>{
  const h=harness();await h.read('health');await h.read('buy-feed');assert.equal(h.calls(),0);assert.equal(h.storage.writes,0);
}));
test('public POST cannot trigger collection',async()=>assert.equal((await worker.fetch(new Request('https://test/tick',{method:'POST'}),{})).status,405));
test('missing binding is explicit 503',async()=>assert.equal((await worker.fetch(new Request('https://test/health'),{})).status,503));
test('disabled by default; no schedule silently activated',()=>clocked(async()=>{const h=harness({mode:'disabled'});await assert.rejects(h.tick(),/NOT_ACTIVATED/);assert.equal(h.calls(),0);}));
test('observe mode never contacts notification provider',()=>clocked(async()=>{
  let sent=0;const h=harness({sender:async()=>{sent++;throw Error();}});await h.tick();assert.equal(sent,0);assert.equal((await h.read('health')).delivery,'NOT_ACTIVATED');
}));
test('duplicate or older slot is collected only once',()=>clocked(async()=>{const h=harness();await h.tick();await h.tick();assert.equal(h.calls(),1);}));
test('concurrent internal ticks are serialized',()=>clocked(async()=>{
  const h=harness(),req=()=>new Request('https://core.internal/tick',{method:'POST',body:JSON.stringify({cron:'*/5 * * * *',scheduledTime:T})});
  await Promise.all([h.instance.fetch(req()),h.instance.fetch(req())]);assert.equal(h.calls(),1);
}));
test('missing provider is not marked delivered',()=>clocked(async()=>{const h=harness({mode:'active'});await h.tick();const r=await h.read('health');assert.equal(r.delivery,'UNCONFIGURED');assert.equal(r.acceptedCount,0);}));
test('HTTP 200 alone is not an acknowledgement',()=>clocked(async()=>{
  const h=harness({mode:'active',sender:async()=>new Response('{}')});await h.tick();const r=await h.read('health');assert.equal(r.delivery,'UNCERTAIN');assert.equal(r.acceptedCount,0);
}));
test('wrong event ID is not accepted',()=>clocked(async()=>{
  const h=harness({mode:'active',sender:async()=>new Response(JSON.stringify({accepted:true,event_id:'wrong',receipt_id:'test'}))});
  await h.tick();assert.equal((await h.read('health')).acceptedCount,0);
}));
test('matching explicit receipt means provider accepted, NOT phone received',()=>clocked(async()=>{
  const h=harness({mode:'active',sender:accept});await h.tick();const r=await h.read('health');assert.equal(r.acceptedCount,1);assert.equal(r.deviceReceived,'UNKNOWN');assert.equal(r.productionReady,false);
}));
test('network error retries same immutable ID after next actual collection',()=>clocked(async set=>{
  const ids=[];const h=harness({mode:'active',sender:async(u,i)=>{ids.push(JSON.parse(i.body).event_id);if(ids.length===1)throw Error('offline');return accept(u,i);}});
  await h.tick();set(T+300000);await h.tick();assert.equal(ids.length,2);assert.equal(ids[0],ids[1]);assert.equal((await h.read('health')).acceptedCount,1);
}));
test('expired recommendation is never delivered late',()=>clocked(async set=>{
  let requests=0;const h=harness({mode:'active',sender:async()=>{requests++;throw Error();}});
  await h.tick();set(T+600000);await h.tick();assert.equal(requests,1);assert.equal((await h.read('health')).delivery,'UNCERTAIN');
}));
test('collection exception preserves last source time and blocks displayed actions',()=>clocked(async set=>{
  let fail=false;const h=harness({collect:()=>{if(fail)throw Error('secret');return fixture(Date.now());}});
  await h.tick();fail=true;set(T+300000);await h.tick();const r=await h.read('buy-feed');
  assert.equal(r.sourceAt,new Date(T).toISOString());assert.equal(r.state,'DATA_BLOCKED');assert.ok(r.packages.every(p=>!p.actionable));assert.ok(!JSON.stringify(r).includes('secret'));
}));
test('source time regression cannot overwrite evidence',()=>clocked(async set=>{
  let t=T;const h=harness({collect:()=>fixture(t)});await h.tick();t=T-1;set(T+300000);await h.tick();
  assert.equal((await h.read('health')).sourceAt,new Date(T).toISOString());assert.deepEqual((await h.read('health')).reasons,['SOURCE_TIME_REGRESSION']);
}));
test('new coordinator instance resumes private durable state',()=>clocked(async()=>{
  const h=harness();await h.tick();const C=coordinator(async()=>{throw Error('must not recollect');});
  const restarted=new C({storage:h.storage},h.env);assert.equal((await restarted.tick({cron:'*/5 * * * *',scheduledTime:T})).state,'DUPLICATE_OR_OLDER_TICK_SKIPPED');
}));
test('unknown durable state version is not reset',()=>clocked(async()=>{
  const h=harness();h.storage.data.state={version:99,slot:0};await assert.rejects(h.tick(),/UNKNOWN_DURABLE/);assert.equal(h.storage.data.state.version,99);
}));
test('delayed scheduled event is rejected instead of backfilled',()=>clocked(async()=>{
  const h=harness();await assert.rejects(h.instance.tick({cron:'*/5 * * * *',scheduledTime:T-500000}),/INVALID_OR_DELAYED/);assert.equal(h.calls(),0);
}));
test('stale read never refreshes source time or claims ready',()=>clocked(async set=>{
  const h=harness();await h.tick();set(T+600000);const r=await h.read('health');assert.equal(r.data,'DATA_BLOCKED');assert.equal(r.sourceAt,new Date(T).toISOString());assert.equal(r.sourceAgeSeconds,600);
}));

test('all expired ambiguous package IDs remain blocked, not just first',()=>{
  const f=fixture();f.packages.push({...clone(f.packages[0]),name:'Silver S',size:'S'});
  let s=advance(initialState(),evaluate(f,T),T);assert.equal(s.outbox.length,2);
  s.outbox.forEach(e=>{e.attempts=1;e.status='UNCERTAIN';});
  f.checked_at=new Date(T+600000).toISOString();s=advance(s,evaluate(f,T+600000),T+600000);
  assert.equal(s.outbox.length,0);assert.equal(Object.keys(s.uncertain).length,2);
});
test('long outage reminder is rate limited to once per day',()=>{
  let s=initialState();for(let i=0;i<3;i++)s=advance(s,evaluate(null,T+i),T+i);
  assert.equal(s.outbox.length,1);s.outbox=[];
  s=advance(s,evaluate(null,T+3600000),T+3600000);assert.equal(s.outbox.length,0);
  s=advance(s,evaluate(null,T+86400003),T+86400003);assert.equal(s.outbox.length,1);
});
test('STRONG with negative EV cannot be forwarded as STRONG',()=>{
  const f=fixture();f.packages[0].final_signal='STRONG BUY';assert.equal(decision(f).signal,'BUY NOW');
});
test('nonfinite hash work blocked',()=>{
  const f=fixture();f.packages[0].package_hashrate_hps=1e308;assert.equal(decision(f).reason,'HASH_WORK_UNAVAILABLE');
});
