// The ONLY notification policy for the replacement core. No network or storage.
export const POLICY = 'BUY_RADAR_CORE_1';
export const MAX_AGE_MS = 420_000;
export const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const num = x => typeof x === 'number' && Number.isFinite(x);
const positive = x => num(x) && x > 0;
export function timestamp(x) {
  return typeof x === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(x)
    ? Date.parse(x) : NaN;
}
const actions = ['GOOD', 'BUY NOW', 'STRONG BUY'];
const signals = ['NO BUY', 'WAIT', ...actions];
export const identity = p => JSON.stringify([p.name, p.currency_market]);
const primary = p => p.size === 'S' || (p.currency_market === 'USDT' && ['5', '20'].includes(String(p.size)));

function mathStatus(p) {
  const checks = p.math_consistency_shadow?.checks;
  const chains = [p.primary_chain, p.merge_chain].filter(object);
  if (!chains.length || !Array.isArray(checks) || checks.length !== chains.length) return 'UNKNOWN';
  const statuses = chains.map((c, i) => {
    const check = checks.find(x => x.chain === (i === 0 ? 'PRIMARY' : 'MERGE'));
    if (!check || !['PASS','WARNING','CRITICAL','UNKNOWN','NOT_APPLICABLE'].includes(check.status)) return 'UNKNOWN';
    if (check.status === 'NOT_APPLICABLE' &&
        !(['KAS:KHEAVYHASH','ZEC:EQUIHASH'].includes(`${c.currency}:${c.algorithm}`))) return 'UNKNOWN';
    return check.status;
  });
  // A missing merged-leg check MUST NOT be hidden by a primary WARNING/PASS.
  return ['CRITICAL','UNKNOWN','WARNING','PASS','NOT_APPLICABLE'].find(s => statuses.includes(s));
}

function decide(p, feed, age) {
  const out = {key:identity(p), package:p.name, currency:p.currency_market,
    priority:primary(p) ? 'PRIMARY' : 'SECONDARY', sourceSignal:p.final_signal,
    signal:null, state:'DATA_BLOCKED', reason:null, math:'UNKNOWN', observed:false,
    actionable:false, costEur:null, expectedReturnPercent:null, negativeEV:null,
    nativePrice:p.price_native, durationSeconds:p.duration_seconds, hashWork:null,
    primaryCoin:p.primary_chain?.currency??null, modelPrimaryHitPercent:null, q24:null, q7:null};
  const block = (reason, observed=false) => ({...out, state:'DATA_BLOCKED', actionable:false, signal:null, reason, observed});
  if (p.available === false) return {...out, state:'UNAVAILABLE', observed:true, reason:'PACKAGE_UNAVAILABLE'};
  if (p.available !== true || !signals.includes(p.final_signal)) return block('INVALID_PACKAGE');
  const e = p.economics, pr = p.profitability, h = p.history_trend;
  if (!object(e) || !object(pr) || e.complete !== true || pr.complete !== true ||
      !positive(e.package_cost_eur) || !positive(p.price_native) || !num(pr.expected_return_percent) ||
      pr.expected_return_percent < 0 || !num(e.ev_eur) || e.ev_eur < 0) return block('INCOMPLETE_ECONOMICS');
  const coins = new Set([p.currency_market, p.primary_chain?.currency]);
  if (p.merge_chain) coins.add(p.merge_chain.currency);
  if (p.primary_chain?.currency === 'LTC' && p.merge_chain?.currency !== 'DOGE') return block('MERGED_LEG_MISSING');
  for (const coin of coins) {
    const fx = feed.market_prices_eur?.[coin];
    if (!object(fx) || fx.fresh !== true || !positive(fx.eur) || !num(fx.age_seconds) ||
        fx.age_seconds < 0 || fx.age_seconds * 1000 + age > 900_000) return block('MARKET_PRICE_UNAVAILABLE');
  }
  for (const chain of [p.primary_chain,p.merge_chain].filter(object)) {
    if (!positive(chain.expected_blocks)) return block('BLOCK_MODEL_UNAVAILABLE');
  }
  if (!object(p.primary_chain)) return block('PRIMARY_CHAIN_MISSING');
  if (!positive(p.package_hashrate_hps) || !positive(p.duration_seconds) ||
      !positive(p.package_hashrate_hps*p.duration_seconds)) return block('HASH_WORK_UNAVAILABLE');
  out.hashWork=p.package_hashrate_hps*p.duration_seconds;
  out.modelPrimaryHitPercent=-Math.expm1(-p.primary_chain.expected_blocks)*100;
  const expectedCost = p.price_native * feed.market_prices_eur[p.currency_market].eur;
  if (!positive(expectedCost)) return block('CURRENCY_COST_MISMATCH');
  if (Math.abs(expectedCost - e.package_cost_eur) > Math.max(0.0002, expectedCost * .001)) return block('CURRENCY_COST_MISMATCH');
  const ratio = 100 * e.ev_eur / e.package_cost_eur;
  if (Math.abs(ratio - pr.expected_return_percent) > .25 + pr.expected_return_percent * .001) return block('EV_FIELDS_DISAGREE');
  out.costEur = e.package_cost_eur; out.expectedReturnPercent = pr.expected_return_percent;
  out.negativeEV = pr.expected_return_percent < 100;
  out.math = mathStatus(p);
  if (!object(h) || !num(h.sample_count_24h) || h.sample_count_24h < 18 ||
      !num(h.coverage_hours_24h) || h.coverage_hours_24h < 18 ||
      !num(h.expected_blocks_per_btc_vs_24h_percent)) return block('HISTORY_NOT_READY');
  out.q24=h.expected_blocks_per_btc_vs_24h_percent;
  out.q7=num(h.expected_blocks_per_btc_vs_7d_percent)?h.expected_blocks_per_btc_vs_7d_percent:null;
  out.observed = true; out.state = 'READY';
  if (!actions.includes(p.final_signal)) return {...out, reason:'NO_OPPORTUNITY'};
  if (out.math === 'CRITICAL') return {...out, state:'SAFETY_BLOCKED', reason:'MATH_CRITICAL'};
  if (out.math === 'UNKNOWN') return block('MATH_UNKNOWN');
  if (p.math_validation?.overall_pass !== true) return block('BASE_MATH_NOT_VALIDATED');
  if (e.package_cost_eur > 100) return {...out, reason:'OUTSIDE_DISPLAY_LIMIT'};
  const ev = pr.expected_return_percent;
  if (ev < 90) return {...out, reason:'EV_BELOW_90'};
  if (ev < 95 && !(out.priority === 'PRIMARY' && out.math === 'PASS')) return {...out, reason:'EV_OR_MATH_GATE'};
  if (out.math === 'WARNING' && p.final_signal === 'GOOD' && out.priority === 'SECONDARY') return {...out, reason:'SECONDARY_WARNING'};
  out.signal = ev < 95 ? 'GOOD' : (out.math === 'WARNING' || ev < 100) && p.final_signal === 'STRONG BUY' ? 'BUY NOW' : p.final_signal;
  return {...out, actionable:true, reason:out.math === 'WARNING' ? 'CAUTION_MATH_WARNING' : 'VERIFIED_BY_POLICY'};
}

export function evaluate(feed, now=Date.now()) {
  if (!num(now)) throw new Error('INVALID_CLOCK');
  const result = {schema:POLICY, evaluatedAt:new Date(now).toISOString(), sourceAt:feed?.checked_at ?? null,
    sourceAgeSeconds:null, state:'DATA_BLOCKED', reasons:[], packages:[], automaticPurchase:false};
  const reject = reason => ({...result, reasons:[reason]});
  if (!object(feed) || feed.schema_version !== 12) return reject('UNKNOWN_SOURCE_SCHEMA');
  const age = now - timestamp(feed.checked_at);
  result.sourceAgeSeconds = num(age) ? age / 1000 : null;
  if (!num(age) || age < 0 || age > MAX_AGE_MS) return reject('STALE_OR_INVALID_SOURCE_TIME');
  if (feed.ok !== true || feed.status !== 'BUY FEED OK') return reject('SOURCE_UNHEALTHY');
  if (feed.history_saved !== true) return reject('HISTORY_NOT_PERSISTED');
  if (!Array.isArray(feed.packages) || !feed.packages.length || feed.packages.length>64) return reject('PACKAGES_MISSING_OR_TOO_MANY');
  const seen = new Set();
  for (const p of feed.packages) {
    if (!object(p) || typeof p.name !== 'string' || !/^[A-Za-z0-9 -]{1,64}$/.test(p.name) ||
        !['BTC','USDT'].includes(p.currency_market)) return reject('INVALID_PACKAGE_IDENTITY');
    if (seen.has(identity(p))) return reject('DUPLICATE_PACKAGE');
    seen.add(identity(p));
  }
  result.packages = feed.packages.map(p => decide(p,feed,age));
  result.packages.sort((a,b) => (a.priority === 'PRIMARY' ? 0 : 1) - (b.priority === 'PRIMARY' ? 0 : 1) || a.package.localeCompare(b.package));
  result.reasons = [...new Set(result.packages.filter(p => p.state === 'DATA_BLOCKED').map(p => p.reason))];
  result.state = result.reasons.length ? 'PARTIAL_DATA' : 'READY';
  return result;
}

export function message(p, sourceAt) {
  const heading = p.reason === 'MATH_CRITICAL' ? `NE KUPUJ — ${p.package} — MATH CRITICAL`
    : `${p.math === 'WARNING' ? 'PREVIDNO — ' : ''}${p.signal} — ${p.package}`;
  return `${heading}\n${p.priority} · ${p.nativePrice} ${p.currency} (~${p.costEur?.toFixed(2)} EUR) · pričakovano povračilo ${p.expectedReturnPercent?.toFixed(2)}%\n` +
    `MATH ${p.math} · podatki ${sourceAt}\n` +
    (p.negativeEV ? 'Negativna pričakovana donosnost. ' : '') +
    'EV ni verjetnost zadetka in ni jamstvo dobička. Brez samodejnega nakupa.';
}

export function initialState() {
  return {version:1, slot:-1, sourceAt:null, failures:0, impaired:false, observed:{}, outbox:[],
    accepted:{}, uncertain:{}, lastHealthAt:null, acceptedCount:0, lastAccepted:null, lastError:null};
}
export function advance(previous, report, now) {
  if (previous.version !== 1) throw new Error('UNKNOWN_DURABLE_STATE_VERSION');
  const state = structuredClone(previous);
  // Mark EVERY expired ambiguous event before pruning, not only the first package.
  for (const e of state.outbox) {
    if (e.key && e.expiresAt<=now && e.attempts>0) state.uncertain[e.key]=e.fingerprint;
  }
  state.outbox=state.outbox.filter(e=>e.expiresAt>now);
  const enqueue = e => { if (!state.outbox.some(x=>x.id===e.id)) state.outbox.push(e); };
  // Stale, malformed and repeated evidence may not re-arm the signal state.
  if (report.state === 'DATA_BLOCKED' || (state.sourceAt && timestamp(report.sourceAt) < timestamp(state.sourceAt))) {
    state.failures++;
    if (state.failures >= 3 && (!state.impaired || now-(state.lastHealthAt??0)>=86400_000)) {
      enqueue({id:`${POLICY}:health:${report.evaluatedAt}`, kind:'HEALTH', createdAt:now,
        expiresAt:now+86400_000, status:'PENDING', attempts:0,
        text:'BUY Radar — SPREMLJANJE OMEJENO. Tišina ni dokaz odsotnosti priložnosti.'});
      state.impaired=true; state.lastHealthAt=now;
    }
    return state;
  }
  if (state.impaired && report.state === 'READY') {
    // Do not send a queued outage after the data have recovered.
    state.outbox = state.outbox.filter(e=>e.kind!=='HEALTH');
    enqueue({id:`${POLICY}:recovery:${report.sourceAt}`, kind:'RECOVERY', createdAt:now,
      expiresAt:now+86400_000, status:'PENDING', attempts:0, text:'BUY Radar — PODATKI OBNOVLJENI. Dostava obvestil se preverja ločeno.'});
    state.impaired=false;
  }
  state.failures = report.state === 'READY' ? 0 : state.failures + 1;
  if (state.failures >= 3 && (!state.impaired || now-(state.lastHealthAt??0)>=86400_000)) {
    enqueue({id:`${POLICY}:partial:${report.evaluatedAt}`, kind:'HEALTH', createdAt:now,
      expiresAt:now+86400_000, status:'PENDING', attempts:0,
      text:'BUY Radar — DELNO SPREMLJANJE. Nekateri paketi nimajo popolnih preverjenih podatkov.'});
    state.impaired=true; state.lastHealthAt=now;
  }
  if (state.sourceAt === report.sourceAt) return state;
  state.sourceAt=report.sourceAt;
  for (const p of report.packages) {
    if (!p.observed) continue;
    const fingerprint = `${p.actionable ? p.signal : p.reason}|${p.math}`;
    const changed = state.observed[p.key] !== fingerprint;
    if (changed) {
      delete state.accepted[p.key]; delete state.uncertain[p.key];
      state.outbox=state.outbox.filter(e=>e.key!==p.key);
    }
    state.observed[p.key]=fingerprint;
    if (state.accepted[p.key]===fingerprint || state.uncertain[p.key]===fingerprint ||
        state.outbox.some(e=>e.key===p.key)) continue;
    if (p.actionable || p.reason === 'MATH_CRITICAL') {
      enqueue({id:`${POLICY}:${p.key}:${fingerprint}:${report.sourceAt}`, key:p.key,
        kind:p.actionable?'SIGNAL':'SAFETY', fingerprint, sourceAt:report.sourceAt,
        createdAt:now, expiresAt:timestamp(report.sourceAt)+MAX_AGE_MS, status:'PENDING', attempts:0,
        text:message(p,report.sourceAt)});
    }
  }
  // The outbox is bounded; a single snapshot has at most one event per package.
  state.outbox=state.outbox.slice(-64);
  return state;
}
