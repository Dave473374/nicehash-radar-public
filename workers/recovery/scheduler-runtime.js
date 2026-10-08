// A single Cloudflare Cron producer; HTTP reads cannot collect or write history.
// The CURRENT economics/quality engine remains radarCore (unchanged functions).
const COLLECTOR_CRON = '*/5 * * * *';
const COLLECTOR_KEY = 'runtime:buy-feed:scheduled';
const COLLECTOR_FRESH_MS = 420000;
const COLLECTOR_TTL = 8 * 86400;
const COLLECTOR_ROLE = 'CLOUDFLARE_CRON_SINGLE_PRODUCER';
const collectorFlights = new WeakMap(); // same-isolate overlap only; KV is not a global lock
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const finiteNumber = value => typeof value === 'number' && Number.isFinite(value);
const sourceTime = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? Date.parse(value) : NaN;

function unavailableFeed(reason, previous = null) {
  const out = record(previous) ? structuredClone(previous) : {
    relay_version: RELAY_VERSION, source_revision: SCHEDULED_SOURCE_REVISION,
    schema_version: SCHEMA_VERSION, checked_at: null, packages: []
  };
  out.status = 'BUY FEED WARNING';
  out.ok = false;
  out.served_at = new Date().toISOString(); // NEVER advance checked_at on an HTTP read
  out.production_health = {
    ...out.production_health, state: reason === 'COLLECTOR_NOT_STARTED' ? 'NOT_STARTED' : 'DEGRADED',
    signal_engine_ready: false, reasons: [reason], revision: 'scheduler-recovery.2'
  };
  if (!record(out.collector)) out.collector = { role: COLLECTOR_ROLE, state: 'NOT_STARTED', expected_interval_seconds: 300 };
  out.collector.fresh = false;
  if (!Array.isArray(out.packages)) out.packages = [];
  for (const p of out.packages) {
    if (!record(p)) continue;
    if (['GOOD','BUY NOW','STRONG BUY'].includes(p.final_signal)) {
      p.upstream_final_signal = p.final_signal;
      p.final_signal = 'WAIT';
    }
    p.final_signal_reason = reason;
  }
  return out;
}

function scheduledHealth(feed) {
  const reasons = [];
  const warming = [];
  const ps = Array.isArray(feed.packages) ? feed.packages : [];
  if (feed.ok !== true || feed.status !== 'BUY FEED OK') reasons.push('UPSTREAM_OR_HISTORY_UNHEALTHY');
  if (!ps.length) reasons.push('NO_PACKAGES');
  if (feed.history_saved !== true) reasons.push('HISTORY_WRITE_FAILED');
  if (!finiteNumber(feed.history_hourly_samples)) reasons.push('HISTORY_UNAVAILABLE');
  let ready = 0;
  const seen = new Set();
  for (const p of ps) {
    if (!record(p)) { reasons.push('INVALID_PACKAGE'); continue; }
    const identity = JSON.stringify([p.name, p.currency_market]);
    if (seen.has(identity)) reasons.push('DUPLICATE_PACKAGE');
    seen.add(identity);
    if (p.available !== true) continue;
    const h = record(p.history_trend) ? p.history_trend : {};
    const e = record(p.economics) ? p.economics : {};
    const pr = record(p.profitability) ? p.profitability : {};
    if (e.complete !== true || pr.complete !== true || !finiteNumber(e.package_cost_eur) ||
        e.package_cost_eur <= 0 || !finiteNumber(pr.expected_return_percent)) reasons.push(p.name + ':ECONOMICS_UNAVAILABLE');
    const mature = finiteNumber(h.sample_count_24h) && h.sample_count_24h >= HISTORY_24H_MIN_SAMPLES &&
      finiteNumber(h.coverage_hours_24h) && h.coverage_hours_24h >= HISTORY_24H_MIN_COVERAGE_HOURS &&
      finiteNumber(h.expected_blocks_per_btc_vs_24h_percent) &&
      ['NO BUY','WAIT','GOOD','BUY NOW','STRONG BUY'].includes(h.quality_signal);
    if (mature) ready++; else warming.push(p.name + ':HISTORY_24H_NOT_READY');
    if (h.trend_status === '7D READY' && !finiteNumber(h.expected_blocks_per_btc_vs_7d_percent)) reasons.push(p.name + ':INVALID_7D_BASELINE');
  }
  const healthyData = reasons.length === 0;
  const isReady = healthyData && warming.length === 0 && ready > 0;
  return {state: isReady ? 'READY' : healthyData ? 'WARMING_UP' : 'DEGRADED',
    signal_engine_ready: isReady, collector_data_ok: healthyData,
    ready_packages: ready, packages_checked: ps.length,
    reasons: [...new Set([...reasons, ...warming])], revision: 'scheduler-recovery.2'};
}

async function readScheduledFeed(env) {
  if (!env?.RADAR_HISTORY) return unavailableFeed('KV_BINDING_MISSING');
  let feed;
  try { feed = await env.RADAR_HISTORY.get(COLLECTOR_KEY, 'json'); }
  catch { return unavailableFeed('COLLECTOR_READ_FAILED'); }
  if (feed === null) return unavailableFeed('COLLECTOR_NOT_STARTED');
  if (!record(feed) || feed.collector?.role !== COLLECTOR_ROLE || !Array.isArray(feed.packages) ||
      feed.relay_version !== RELAY_VERSION || feed.source_revision !== SCHEDULED_SOURCE_REVISION) {
    return unavailableFeed('COLLECTOR_RECORD_INVALID_OR_VERSION_MISMATCH');
  }
  const age = Date.now() - sourceTime(feed.checked_at);
  if (!Number.isFinite(age) || age < 0 || age > COLLECTOR_FRESH_MS) return unavailableFeed('COLLECTOR_STALE', feed);
  // Validate the actual decision gate, even if stored top-level flags disagree.
  if (feed.ok === true && feed.production_health?.signal_engine_ready !== true) return unavailableFeed('DECISION_GATE_INCONSISTENT', feed);
  const result = structuredClone(feed);
  result.served_at = new Date().toISOString();
  result.collector.age_seconds = age / 1000;
  result.collector.fresh = true;
  return result;
}

async function collectScheduled(controller, env) {
  if (controller?.cron !== COLLECTOR_CRON) throw new Error('UNEXPECTED_CRON_USE_EXACT_FIVE_MINUTE_SCHEDULE');
  if (!finiteNumber(controller.scheduledTime) || controller.scheduledTime > Date.now()) throw new Error('INVALID_SCHEDULED_TIME');
  if (!env?.RADAR_HISTORY) throw new Error('KV_BINDING_MISSING');
  const kv = env.RADAR_HISTORY;
  const slot = Math.floor(controller.scheduledTime / 300000);
  const previous = await kv.get(COLLECTOR_KEY, 'json');
  if (record(previous) && previous.collector?.role === COLLECTOR_ROLE &&
      previous.source_revision === SCHEDULED_SOURCE_REVISION && previous.collector.scheduled_slot >= slot) {
    return {state:'DUPLICATE_OR_OLDER_TICK_SKIPPED'};
  }
  const started = Date.now();
  let feed, unexpectedError = null;
  try {
    // In-process invocation, NOT a loopback HTTP fetch and not a public trigger.
    const response = await radarCore.fetch(new Request('https://radar.internal/buy-feed'), env);
    feed = await response.json();
    if (!record(feed)) throw new Error('INVALID_CORE_FEED');
    if (feed.ok === true && Array.isArray(feed.packages) && feed.packages.length) annotateScheduledMath(feed);
    feed.production_health = scheduledHealth(feed);
    if (!feed.production_health.signal_engine_ready) {
      feed.status = 'BUY FEED WARNING'; feed.ok = false;
      for (const p of feed.packages || []) if (record(p) && ['GOOD','BUY NOW','STRONG BUY'].includes(p.final_signal)) {
        p.upstream_final_signal = p.final_signal; p.final_signal = 'WAIT';
        p.final_signal_reason = feed.production_health.state === 'WARMING_UP' ? 'HISTORY_WARMING_UP' : 'DATA_QUALITY_UNAVAILABLE';
      }
    }
  } catch (err) {
    unexpectedError = err;
    feed = unavailableFeed('COLLECTION_FAILED');
    // This is the time of an error observation, with NO quotes or market values.
    feed.checked_at = new Date().toISOString();
    feed.source_checked_at = null;
  }
  feed.relay_version = RELAY_VERSION;
  feed.source_revision = SCHEDULED_SOURCE_REVISION;
  feed.schema_version = SCHEMA_VERSION;
  if (!Array.isArray(feed.packages)) feed.packages = [];
  const completed = Date.now();
  const previousTime = sourceTime(previous?.collector?.started_at);
  feed.collector = {
    role: COLLECTOR_ROLE, expected_interval_seconds: 300, cron: COLLECTOR_CRON,
    scheduled_slot: slot, scheduled_for: new Date(controller.scheduledTime).toISOString(),
    started_at: new Date(started).toISOString(), completed_at: new Date(completed).toISOString(),
    scheduling_delay_seconds: (started - controller.scheduledTime) / 1000,
    previous_start_gap_seconds: Number.isFinite(previousTime) ? (started - previousTime) / 1000 : null,
    duration_seconds: (completed - started) / 1000,
    history_write_ok: feed.history_saved === true,
    state: feed.history_saved === true ? 'RUNNING' : 'DEGRADED',
    fresh: true, automatic_purchase: false, public_request_can_write_history: false,
    exactly_once_guaranteed: false
  };
  // This extra read avoids overwriting an already visible later tick, but is not CAS.
  const current = await kv.get(COLLECTOR_KEY, 'json');
  if (record(current) && current.source_revision === SCHEDULED_SOURCE_REVISION && current.collector?.scheduled_slot > slot)
    return {state:'SUPERSEDED_TICK_SKIPPED'};
  await kv.put(COLLECTOR_KEY, JSON.stringify(feed), {expirationTtl:COLLECTOR_TTL});
  console.log(JSON.stringify({event:'RADAR_COLLECTOR', state:feed.collector.state,
    decision_state:feed.production_health.state, checked_at:feed.checked_at,
    packages:feed.packages.length, history_saved:feed.history_saved === true}));
  if (unexpectedError || feed.history_saved !== true) throw new Error('COLLECTOR_FAILED_SEE_PUBLISHED_HEALTH');
  return {state:feed.collector.state, decision_state:feed.production_health.state};
}

export default {
  async scheduled(controller, env, ctx) {
    if (!env?.RADAR_HISTORY) throw new Error('KV_BINDING_MISSING');
    if (collectorFlights.has(env.RADAR_HISTORY)) return {state:'SAME_ISOLATE_OVERLAP_SKIPPED'};
    const promise = collectScheduled(controller, env);
    collectorFlights.set(env.RADAR_HISTORY, promise);
    try { return await promise; }
    finally { collectorFlights.delete(env.RADAR_HISTORY); }
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (!['GET','HEAD'].includes(request.method)) return new Response('Read-only HTTP endpoints', {status:405});
    let response;
    if (url.pathname === '/buy-feed' || url.pathname === '/health') {
      const feed = await readScheduledFeed(env);
      response = jsonResponse(url.pathname === '/health' ? {
        status:feed.status, ok:feed.ok, relay_version:RELAY_VERSION, source_revision:SCHEDULED_SOURCE_REVISION,
        checked_at:feed.checked_at, served_at:feed.served_at, collector:feed.collector,
        production_health:feed.production_health, history_diagnostics:feed.history_diagnostics ?? null,
        history_hourly_samples:feed.history_hourly_samples ?? 0,
        history_coverage_hours:feed.history_coverage_hours ?? 0, automatic_purchase:false
      } : feed);
    } else if (['/','/prices','/history','/test','/radar','/schema','/buy-feed-shadow'].includes(url.pathname)) {
      response = await radarCore.fetch(request, env);
    } else response = new Response('Not found', {status:404});
    return request.method === 'HEAD' ? new Response(null, response) : response;
  }
};
