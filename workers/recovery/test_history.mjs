import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import * as fixed from './worker.mjs';
const originalSource = gunzipSync(readFileSync(new URL('./base-worker-v2.9.0.js.gz', import.meta.url))).toString();
const original = await import('data:text/javascript;base64,' + Buffer.from(originalSource + '\nexport {buildHistoryAnalysis, combineMiningAndProfitability, analysePackageHistory};').toString('base64'));
const H = 3600000, NOW = Date.parse('2026-10-05T18:00:52.451Z');
const realNow = Date.now;
Date.now = () => NOW;
const price = .0001;
const pkg = {name:'Palladium S', size:'S', available:true, price_btc:price, price_btc_equiv:price, package_hashrate_hps:1e10,
 primary_chain:{currency:'LTC', expected_blocks:.012, model_hit_probability:-Math.expm1(-.012), expected_reward_native:.07425},
 economics:{ev_cost_percent:101}, nicehash_odds:{denominator:100}};
function snapshot(ts, blocks=.01) {
 return {captured_at_ms:ts,captured_at:new Date(ts).toISOString(),packages:[{name:pkg.name, price_btc:price,available:true,
 primary_expected_blocks:blocks, model_hit_probability_percent:-Math.expm1(-blocks)*100, primary_expected_reward_native:blocks*6.1875}]};
}
function key(ts) {return 'snapshot:' + String(ts).padStart(13,'0');}
function kvFor(entries, pageSize=1000, startEmpty=false) {
 const store = new Map(entries), calls = [], writes=[];
 return {store,calls,writes,
  async list(opts) {
   calls.push(opts); assert.equal(opts.prefix,'snapshot:');
   if(startEmpty && !opts.cursor) return {keys:[],list_complete:false,cursor:'offset:0'};
   const offset=opts.cursor ? Number(opts.cursor.split(':')[1]) : 0;
   const all=[...store.keys()].filter(k=>k.startsWith(opts.prefix)).sort();
   const end=Math.min(offset+pageSize,all.length);
   return {keys:all.slice(offset,end).map(name=>({name})),list_complete:end===all.length,cursor:end===all.length?'':'offset:'+end};
  },
  async get(k) {return structuredClone(store.get(k) ?? null);},
  async put(k,value,opts) {writes.push({k,opts});store.set(k,JSON.parse(value));}
 };
}
function recent(n=168) {return Array.from({length:n},(_,i)=>{const ts=NOW-(i+1)*H;return [key(ts),snapshot(ts)];});}
function env(kv) {return {RADAR_HISTORY:kv};}

test('regression: old first page produces zero original hours; pagination restores 168 actual observations',async()=>{
 const old=Array.from({length:177},(_,i)=>{const ts=NOW-180*H+i*1000;return [key(ts),snapshot(ts)];});
 const kv=kvFor([...old,...recent()],177);
 const before=await original.buildHistoryAnalysis(env(kv),[pkg]);
 assert.equal(before.sample_count,177);assert.equal(before.hourly_sample_count,0);assert.equal(before.ok,true);
 const after=await fixed.buildHistoryAnalysis(env(kv),[pkg]);
 assert.equal(after.ok,true);assert.equal(after.hourly_sample_count,168);assert.equal(after.status,'7D READY');
 assert.equal(after.by_package[pkg.name].sample_count_24h,24);
 assert.equal(after.by_package[pkg.name].sample_count_7d,168);
 assert.equal(after.by_package[pkg.name].expected_blocks_per_btc_vs_24h_percent,20);
 assert.equal(after.by_package[pkg.name].expected_blocks_per_btc_vs_7d_percent,20);
 assert.ok(after.diagnostics.listed_pages>1);
});
test('empty incomplete KV pages must advance',async()=>{
 const kv=kvFor(recent(),1000,true), r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);
 assert.equal(r.status,'7D READY');assert.equal(kv.calls.length,2);
});
test('more than 1000 keys follows all pages with same prefix',async()=>{
 const data=Array.from({length:1200},(_,i)=>{const t=NOW-1-i*50000;return [key(t),snapshot(t)];});
 const kv=kvFor(data), r=await fixed.listAllHistoryKeys(kv);assert.equal(r.keys.length,1200);assert.equal(r.pages,2);
});
test('repeated cursor fails closed, not an endless loop',async()=>{
 const kv={list:async()=>({keys:[],list_complete:false,cursor:'same'})};
 const r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);assert.equal(r.ok,false);assert.match(r.error,/CURSOR/);
});
test('missing cursor fails closed',async()=>{
 const kv={list:async()=>({keys:[],list_complete:false})};
 const r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);assert.equal(r.ok,false);assert.match(r.error,/CURSOR/);
});
test('bounded pagination fails closed on budget exhaustion',async()=>{
 let n=0;const kv={list:async()=>({keys:[],list_complete:false,cursor:String(++n)})};
 const r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);assert.equal(r.ok,false);assert.equal(n,64);
});
test('24h ready without seven days is legitimate, Q24 still useful',async()=>{
 const r=await fixed.buildHistoryAnalysis(env(kvFor(recent(24))),[pkg]);assert.equal(r.status,'24H READY');
 assert.equal(r.by_package[pkg.name].expected_blocks_per_btc_vs_24h_percent,20);
});
test('initial empty history is warming up, not fabricated data',async()=>{
 const r=await fixed.buildHistoryAnalysis(env(kvFor([])),[pkg]);assert.equal(r.ok,true);assert.equal(r.status,'HISTORY WARMING UP');
 assert.equal(r.by_package[pkg.name].expected_blocks_per_btc_vs_24h_percent,null);
});
test('many listed keys but no recent observations cannot be labelled saved/healthy',async()=>{
 const entries=Array.from({length:30},(_,i)=>{const t=NOW-180*H-i*1000;return [key(t),snapshot(t)];});
 const r=await fixed.buildHistoryAnalysis(env(kvFor(entries)),[pkg]);assert.equal(r.ok,false);assert.equal(r.status,'HISTORY INVALID');
});
test('future keys fail closed',async()=>{
 const t=NOW+H;const r=await fixed.buildHistoryAnalysis(env(kvFor([...recent(),[key(t),snapshot(t)]])),[pkg]);assert.equal(r.ok,false);
});
test('malformed milliseconds payload fails closed',async()=>{
 const entries=recent();entries[0][1].captured_at_ms=Math.floor(entries[0][1].captured_at_ms/1000);
 const r=await fixed.buildHistoryAnalysis(env(kvFor(entries)),[pkg]);assert.equal(r.ok,false);
});
test('timezone-aware timestamp fallback keeps equivalent observation',async()=>{
 const entries=recent();delete entries[0][1].captured_at_ms;
 entries[0][1].captured_at='2026-10-05T19:00:52.451+02:00';
 const r=await fixed.buildHistoryAnalysis(env(kvFor(entries)),[pkg]);assert.equal(r.ok,true);assert.equal(r.hourly_sample_count,168);
});
test('missing selected value fails closed, no false partial history',async()=>{
 const kv=kvFor(recent());const g=kv.get;kv.get=async k=>k===recent()[0][0]?null:g(k);
 const r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);assert.equal(r.ok,false);
});
test('KV error fails closed',async()=>{
 const kv={list:async()=>{throw Error('KV quota');}};const r=await fixed.buildHistoryAnalysis(env(kv),[pkg]);assert.equal(r.ok,false);assert.match(r.error,/quota/);
});
test('no KV binding fails closed',async()=>{assert.equal((await fixed.buildHistoryAnalysis({},[pkg])).ok,false);});
test('stale newest observation prevents old data pretending to be ready',async()=>{
 const entries=recent().map(([k,s])=>{s.captured_at_ms-=3*H;s.captured_at=new Date(s.captured_at_ms).toISOString();return [key(s.captured_at_ms),s];});
 const r=await fixed.buildHistoryAnalysis(env(kvFor(entries)),[pkg]);assert.equal(r.ok,false);assert.equal(r.status,'HISTORY STALE');
});
test('legacy and rounded-hour snapshots do not double count; newest payload wins',async()=>{
 const entries=recent();const ts=NOW-H+5000;const rounded=Math.floor(ts/H)*H;
 entries.push([key(rounded),snapshot(ts,.01)]);
 const r=await fixed.buildHistoryAnalysis(env(kvFor(entries)),[pkg]);assert.equal(r.hourly_sample_count,168);assert.equal(r.ok,true);
});
test('snapshot persistence reuses an hourly key without deleting legacy keys',async()=>{
 const kv=kvFor(recent());await fixed.storeHistorySnapshot(env(kv),[pkg],{provider:'TEST'});
 await fixed.storeHistorySnapshot(env(kv),[pkg],{provider:'TEST'});
 assert.equal(kv.writes.length,2);assert.equal(kv.writes[0].k,kv.writes[1].k);assert.equal(kv.store.size,169);
 assert.equal(kv.writes[0].opts.expirationTtl,8*24*3600);
});
test('write errors are explicit',async()=>{
 const kv={put:async()=>{throw Error('write failure');}};
 const r=await fixed.storeHistorySnapshot(env(kv),[pkg],{provider:'TEST'});assert.equal(r.ok,false);
});
test('cold worker instance reuses persisted KV, not memory-only baseline',async()=>{
 const kv=kvFor(recent());const a=await fixed.buildHistoryAnalysis(env(kv),[pkg]);
 const freshModule=await import('./worker.mjs?cold=1');const b=await freshModule.buildHistoryAnalysis(env(kv),[pkg]);
 assert.deepEqual(a.by_package,b.by_package);
});
test('signal table unchanged for every combination',()=>{
 for(const m of ['HISTORY WARMING UP','UNKNOWN','NO BUY','WAIT','GOOD','BUY NOW','STRONG BUY'])
 for(const e of ['UNKNOWN','POOR','MARGINAL','FAIR','POSITIVE'])
 assert.equal(fixed.combineMiningAndProfitability(m,e),original.combineMiningAndProfitability(m,e));
});
test('positive EV cannot create BUY without historical quality',()=>{
 assert.equal(fixed.combineMiningAndProfitability('HISTORY WARMING UP','POSITIVE'),'WAIT');
 assert.equal(fixed.combineMiningAndProfitability('WAIT','POSITIVE'),'WAIT');
 assert.equal(fixed.combineMiningAndProfitability('STRONG BUY','POSITIVE'),'STRONG BUY');
});
test('reason separates history outage from economic rejection',()=>{
 assert.equal(fixed.recoverySignalReason(pkg,{ok:false}),'HISTORY_UNAVAILABLE');
 assert.equal(fixed.recoverySignalReason({...pkg,mining_signal:'HISTORY WARMING UP'},{ok:true}),'HISTORY_WARMING_UP');
});
process.on('exit',()=>{Date.now=realNow;});

async function exerciseRoute(kv, route='/buy-feed', rawChanges={}) {
 const previous=globalThis.fetch;
 const raw={name:'Palladium S',id:'fixture',price:price,currencyMarket:'BTC',status:'A',available:true,
 projectedSpeed:.01,duration:3600,probabilityPrecision:1/(-Math.expm1(-.012)),probability:84,
 currencyAlgo:{currency:'LTC',miningAlgorithm:'SCRYPT',networkHashpower:2e13,networkDifficulty:1e8,blockReward:6.1875,blockRewardWithNhFee:6.25,blockTime:150},...rawChanges};
 globalThis.fetch=async url=>{
  if(String(url).includes('public/solo/package')) return Response.json({packages:[raw]});
  if(String(url).includes('api.kraken.com')) {
   const pair=new URL(url).searchParams.get('pair');const value=pair==='XBTEUR'?100000:200;
   return Response.json({error:[],result:{[pair]:{c:[String(value)]}}});
  }
  throw Error('Unexpected network access in an offline test: '+url);
 };
 try {return await (await fixed.default.fetch(new Request('https://fixture.invalid'+route),env(kv))).json();}
 finally {globalThis.fetch=previous;}
}
test('whole Worker route: restored history -> Q24/Q7 -> unchanged economics -> STRONG BUY on qualifying synthetic fixture',async()=>{
 const r=await exerciseRoute(kvFor(recent()));assert.equal(r.ok,true);assert.equal(r.production_health.signal_engine_ready,true);
 assert.equal(r.packages[0].history_trend.expected_blocks_per_btc_vs_24h_percent,20);
 assert.equal(r.packages[0].final_signal,'STRONG BUY');assert.equal(r.packages[0].final_signal_reason,'CONFIRMED_QUALITY_AND_POSITIVE_EV');
});
test('whole Worker route: a successful write cannot hide a broken read',async()=>{
 const kv=kvFor([]);kv.list=async()=>{throw Error('read failed');};
 const r=await exerciseRoute(kv);assert.equal(r.history_saved,true);assert.equal(r.ok,false);assert.equal(r.production_health.state,'DEGRADED');
 assert.equal(r.packages[0].final_signal,'WAIT');assert.equal(r.packages[0].final_signal_reason,'HISTORY_UNAVAILABLE');
});
test('whole Worker route: write failure is not a healthy feed',async()=>{
 const kv=kvFor(recent());kv.put=async()=>{throw Error('write failed');};
 const r=await exerciseRoute(kv);assert.equal(r.ok,false);assert.equal(r.production_health.signal_engine_ready,false);
});
test('shadow endpoint still performs zero KV operations',async()=>{
 const kv=new Proxy({}, {get:()=>()=>{throw Error('SHADOW_TOUCHED_KV');}});
 const r=await exerciseRoute(kv,'/buy-feed-shadow');assert.equal(r.shadow_sampling_mode,'NO_KV');
 assert.equal(r.history_status,'HISTORY BYPASSED');assert.equal(r.production_health.state,'SHADOW_ONLY');
});
test('whole Worker route: invalid candidate set cannot be healthy by vacuous every()',async()=>{
 const r=await exerciseRoute(kvFor(recent()),'/buy-feed',{available:false});assert.equal(r.ok,false);assert.equal(r.packages.length,0);
});
