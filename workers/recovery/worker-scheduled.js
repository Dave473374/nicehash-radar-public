const SCHEDULED_SOURCE_REVISION = "scheduler-recovery.2-sha256:38054c9b1d66a4855bdd29f85b4b3199e16638f1b3253113e5b5c0154bb37329";
const NICEHASH_URL = "https://api2.nicehash.com/hashpower/api/v2/public/solo/package";
const COINPAPRIKA_BASE_URL = "https://api.coinpaprika.com/v1/tickers";
const KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker";

const TIMEOUT_MS = 9000;
const MARKET_TIMEOUT_MS = 7000;
const MARKET_MAX_AGE_SECONDS = 15 * 60;
const RELAY_VERSION = "2.9.0-history-recovery.2";
const SCHEMA_VERSION = 12;
const HISTORY_RETENTION_SECONDS = 8 * 24 * 60 * 60;
const HISTORY_LIST_LIMIT = 1000;
const HISTORY_WINDOW_24H_MS = 24 * 60 * 60 * 1000;
const HISTORY_WINDOW_7D_MS = 7 * 24 * 60 * 60 * 1000;
const HISTORY_24H_MIN_SAMPLES = 18;
const HISTORY_24H_MIN_COVERAGE_HOURS = 18;
const HISTORY_7D_MIN_SAMPLES = 144;
const HISTORY_7D_MIN_COVERAGE_HOURS = 156;

const SANITY_RELATIVE_TOLERANCE = 0.15;
const SANITY_ABSOLUTE_TOLERANCE = 0.001;

const EV_THRESHOLDS = {
NO_BUY_BELOW: 85,
WAIT_BELOW: 90,
GOOD_BELOW: 95,
BUY_NOW_BELOW: 100
};

// Mining timing: quality versus the package's own historical baseline.
const QUALITY_THRESHOLDS = {
NO_BUY_BELOW: -5,
WAIT_BELOW: 5,
GOOD_BELOW: 10,
BUY_NOW_BELOW: 15
};

// v2.9.0: profitability remains the primary gate; break-even probability adds a downside-risk cap for negative-EV packages.
// These thresholds are expressed as expected BTC-equivalent return / ticket BTC cost.
const PROFITABILITY_THRESHOLDS = {
POSITIVE_MIN_RETURN_MULTIPLE: 1.00,
FAIR_MIN_RETURN_MULTIPLE: 1 / 1.20, // >= 83.33%
MARGINAL_MIN_RETURN_MULTIPLE: 1 / 1.50 // >= 66.67%
};

// v2.9.0 risk caps. These apply ONLY to negative-EV setups.
// Important: a low-lambda / jackpot package is NOT rejected merely because its
// break-even probability is small when expected BTC-equivalent return is >=100%.
const BREAK_EVEN_RISK_THRESHOLDS = {
ULTRA_LOW_PROBABILITY_PERCENT: 1.0,
LOW_PROBABILITY_PERCENT: 5.0,
DEEP_NEGATIVE_EV_RETURN_MULTIPLE: 0.90,
MODERATE_NEGATIVE_EV_RETURN_MULTIPLE: 0.95,
NEAR_BREAK_EVEN_MULTIPLE_MAX: 1.15
};

const COIN_IDS = {
BTC: "btc-bitcoin",
BCH: "bch-bitcoin-cash",
LTC: "ltc-litecoin",
DOGE: "doge-dogecoin",
ZEC: "zec-zcash",
KAS: "kas-kaspa",
USDT: "usdt-tether"
};

const radarCore = {
async fetch(request, env) {
const url = new URL(request.url);

try {
if (url.pathname === "/prices") return await pricesEndpoint();
if (url.pathname === "/history") return await historyEndpoint(env);

const started = Date.now();
const response = await fetchWithTimeout(NICEHASH_URL, TIMEOUT_MS, {
Accept: "application/json",
"User-Agent": `NiceHash-EasyMining-Relay/${RELAY_VERSION}`
});
const latency = Date.now() - started;

if (!response.ok) {
return fail(`NiceHash upstream HTTP ${response.status}`, {
upstream_status: response.status,
upstream_latency_ms: latency
});
}

const text = await response.text();
if (!text || !text.trim()) {
return fail("NiceHash returned an empty response", {
upstream_status: response.status,
upstream_latency_ms: latency
});
}

let raw;
try {
raw = JSON.parse(text);
} catch {
return fail("NiceHash response is not valid JSON", {
upstream_status: response.status,
upstream_latency_ms: latency
});
}

const packages = extractPackages(raw);
if (!packages.length) {
return fail("No EasyMining packages found", {
upstream_status: response.status,
upstream_latency_ms: latency
});
}

const analysed = packages.map(analysePackage);

if (url.pathname === "/test") {
const silverS = analysed.find(p => /^silver\s+s$/i.test(p.name));
const silverM = analysed.find(p => /^silver\s+m$/i.test(p.name));

if (!silverS || !silverM) {
return fail("Silver S/M health packages missing", {
package_count: analysed.length,
upstream_status: response.status,
upstream_latency_ms: latency
});
}

return jsonResponse({
status: "TEST OK",
ok: true,
source: "NiceHash EasyMining",
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
upstream_status: response.status,
upstream_latency_ms: latency,
package_count: analysed.length,
packages: [healthView(silverS), healthView(silverM)],
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
});
}

if (url.pathname === "/radar") {
const radar = analysed
.filter(p => isRadarPackage(p.name))
.map(p => ({
...p,
buy_eligible:
p.available === true &&
p.status === "A" &&
p.data_quality === "COMPLETE" &&
p.math_validation.overall_pass === true
}));

const buyable = radar.filter(p => p.buy_eligible);

return jsonResponse({
status: "RADAR OK",
ok: true,
source: "NiceHash EasyMining",
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
upstream_status: response.status,
upstream_latency_ms: latency,
package_count: analysed.length,
radar_package_count: radar.length,
buyable_package_count: buyable.length,
market_value_status: "EXTERNAL_PRICE_REQUIRED",
mining_math: "HASHRATE_DERIVED",
packages: radar,
comparisons: buildComparisons(buyable),
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
});
}

const shadowNoKv = url.pathname === "/buy-feed-shadow";
if (url.pathname === "/buy-feed" || shadowNoKv) {
const candidates = analysed.filter(
p => isRadarPackage(p.name) && p.available === true && p.status === "A"
);

const valid = candidates.filter(
p => p.data_quality === "COMPLETE" && p.math_validation.overall_pass
);

const warnings = candidates.filter(
p => p.data_quality !== "COMPLETE" || !p.math_validation.overall_pass
);

const marketSnapshot = await fetchMarketSnapshot();
const economicPackages = valid.map(pkg => addEconomicValue(pkg, marketSnapshot.market));

const marketOk = marketSnapshot.ok === true;
const economicsOk = marketOk && economicPackages.length > 0 && economicPackages.every(pkg => pkg.economics?.complete === true);

// Read production history only for the ordinary /buy-feed route.
// /buy-feed-shadow is deliberately KV-free for high-cadence research sampling.
const historyAnalysis = shadowNoKv
? buildHistoryUnavailable("HISTORY BYPASSED", "No-KV shadow endpoint; production history is not read")
: economicsOk
? await buildHistoryAnalysis(env, economicPackages)
: buildHistoryUnavailable("HISTORY SKIPPED", "Market/economic data is not complete");

const packagesWithTrend = economicPackages.map(pkg =>
attachHistoryTrend(pkg, historyAnalysis.by_package?.[pkg.name] ?? null)
);

// v2.9.0 profitability + break-even risk gate.
const packagesWithDecision = packagesWithTrend.map(pkg =>
attachProfitabilityDecision(pkg, marketSnapshot.market)
);

const historyWrite = shadowNoKv
? { ok: false, status: "HISTORY BYPASSED", error: null }
: economicsOk
? await storeHistorySnapshot(env, packagesWithDecision, marketSnapshot)
: { ok: false, status: "HISTORY SKIPPED", error: "Market/economic data is not complete" };

return jsonResponse({
status: economicsOk && (shadowNoKv || (historyAnalysis.ok && historyWrite.ok)) ? "BUY FEED OK" : "BUY FEED WARNING",
ok: economicsOk && (shadowNoKv || (historyAnalysis.ok && historyWrite.ok)),
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
upstream_status: response.status,
upstream_latency_ms: latency,
mining_math: "HASHRATE_DERIVED",
market_value_status: marketOk
? (economicsOk ? "BTC_EQUIVALENT_PROFITABILITY_READY" : "ECONOMIC_DATA_INCOMPLETE")
: "MARKET WARNING",
market_provider: marketSnapshot.provider,
market_latency_ms: marketSnapshot.provider_latency_ms,
market_status: marketSnapshot.status,
market_prices_eur: compactMarketPrices(marketSnapshot.market),
decision_engine: "PROFITABILITY_AND_BREAK_EVEN_GATED_MINING_QUALITY_VS_HISTORY",
market_value_role: "BTC_NEUTRAL_PROFITABILITY_GATE",
quality_thresholds_vs_24h_percent: {
no_buy: "<-5",
wait: "-5-<5",
good: "5-<10",
buy_now: "10-<15",
strong_buy: ">=15 with confirmation"
},
profitability_thresholds: {
poor: "<66.67% expected BTC-equivalent return",
marginal: "66.67%-<83.33%",
fair: "83.33%-<100%",
positive: ">=100%"
},
break_even_risk_thresholds: {
ultra_low_probability: "<1%",
low_probability: "<5%",
deep_negative_ev: "<90% expected return",
moderate_negative_ev: "<95% expected return",
rule: "Negative-EV packages can be capped by break-even risk; expected return >=100% is exempt from a low-probability-only cap."
},
strong_buy_rule: "STRONG BUY requires historically confirmed STRONG mining quality AND POSITIVE expected BTC-equivalent profitability. EUR ticket price by itself never determines the signal.",
profitability_rule: "Profitability is evaluated in BTC-equivalent terms from current coin/BTC ratios. Mining quality improves timing, but weak expected profitability caps or blocks BUY signals.",
trend_engine_status: historyAnalysis.status,
history_samples_loaded: historyAnalysis.sample_count ?? 0,
history_hourly_samples: historyAnalysis.hourly_sample_count ?? 0,
history_coverage_hours: historyAnalysis.coverage_hours ?? 0,
history_status: shadowNoKv ? "HISTORY BYPASSED" : historyAnalysis.status,
history_write_status: historyWrite.status,
history_diagnostics: historyAnalysis.diagnostics ?? null,
production_health: { state: shadowNoKv ? "SHADOW_ONLY" : !(economicsOk && historyAnalysis.ok && historyWrite.ok) ? "DEGRADED" : ["24H READY", "7D READY"].includes(historyAnalysis.status) ? "READY" : "WARMING_UP", signal_engine_ready: !shadowNoKv && economicsOk && historyAnalysis.ok && historyWrite.ok && ["24H READY", "7D READY"].includes(historyAnalysis.status), history_write_ok: historyWrite.ok, revision: "history-recovery.1" },
history_saved: historyWrite.ok,
history_key: historyWrite.key ?? null,
history_error: historyAnalysis.error ?? historyWrite.error ?? null,
...(shadowNoKv ? { shadow_sampling_mode: "NO_KV", shadow_history_access: "NONE" } : {}),
valid_package_count: valid.length,
warning_package_count: warnings.length,
packages: packagesWithDecision.map(pkg => ({ ...compactBuyView(pkg), final_signal_reason: recoverySignalReason(pkg, historyAnalysis) })),
warnings: warnings.map(warningView),
comparisons: buildEconomicComparisons(packagesWithDecision),
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
});
}

if (url.pathname === "/schema") {
return jsonResponse({
status: "SCHEMA OK",
ok: true,
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
hashrate_units: {
SHA256ASICBOOST: "EH/s",
SCRYPT: "TH/s",
EQUIHASH: "GH/s",
KHEAVYHASH: "PH/s"
},
hashrate_multipliers: {
SHA256ASICBOOST: 1e18,
SCRYPT: 1e12,
EQUIHASH: 1e9,
KHEAVYHASH: 1e15
},
coin_mapping: COIN_IDS,
market_provider: "Kraken primary + CoinPaprika fallback",
market_endpoint: "/prices",
history_endpoint: "/history",
history_binding: "RADAR_HISTORY",
history_retention_days: 8,
history_baselines: {
sampling: "At most one stored snapshot per UTC hour is used, so manual refreshes do not overweight the baseline.",
baseline_statistic: "median",
window_24h_hours: 24,
window_7d_hours: 168,
ready_24h: `>=${HISTORY_24H_MIN_SAMPLES} hourly samples and >=${HISTORY_24H_MIN_COVERAGE_HOURS}h coverage`,
ready_7d: `>=${HISTORY_7D_MIN_SAMPLES} hourly samples and >=${HISTORY_7D_MIN_COVERAGE_HOURS}h coverage`
},
decision_engine: "Profitability- and break-even-risk-gated mining quality versus own historical baseline. Mining quality finds unusually good mining conditions; BTC-equivalent expected profitability is primary, and negative-EV setups can be further capped by break-even risk.",
mining_quality_formula: "expected_blocks_per_btc = expected_blocks / ticket_price_btc; odds_efficiency_per_btc = hit_probability / ticket_price_btc; native_reward_per_btc = expected_reward_native / ticket_price_btc",
strong_buy_confirmation: "24h ready + expected-blocks-per-BTC >=15% above 24h median + (odds-efficiency-per-BTC >=10% above median OR primary native-reward-per-BTC >=15% above median); if 7d ready, long-window quality must not be materially adverse; FINAL STRONG BUY additionally requires POSITIVE profitability; low-lambda positive-EV jackpot setups are not rejected solely for low absolute break-even probability.",
profitability_gate: {
basis: "expected native rewards converted to BTC-equivalent through current coin/BTC ratios",
positive: "expected_return_multiple >= 1.00",
fair: "expected_return_multiple >= 0.8333 and < 1.00",
marginal: "expected_return_multiple >= 0.6667 and < 0.8333",
poor: "expected_return_multiple < 0.6667",
final_signal_rule: "POOR => NO BUY; MARGINAL caps strong mining at GOOD; FAIR caps strong mining at BUY NOW; POSITIVE permits the full mining-quality signal. v2.9.0 additionally caps negative-EV setups when break-even probability is very low, while exempting positive-EV low-lambda jackpot setups from that probability cap."
},
break_even_risk_gate: {
ultra_low_negative_ev: "expected return <90% AND break-even probability <1% => max final WAIT",
low_negative_ev: "expected return <95% AND break-even probability <5% => max final GOOD",
stretched_break_even: "negative EV with materially stretched break-even multiple and <10% break-even probability => max final GOOD",
jackpot_exception: "expected return >=100% => no downgrade solely because absolute break-even probability is low"
},
ev_formula: "expected_blocks = package_hashrate_hps / network_hashpower_hps * duration_seconds / block_time_seconds",
probability_formula: "P(at least 1 block) = 1 - exp(-expected_blocks)",
expected_reward_formula: "expected_reward_native = expected_blocks * blockReward",
ev_eur_formula: "EV_EUR = sum(expected_reward_native * coin_EUR_price)",
ev_cost_formula: "EV_COST_PERCENT = EV_EUR / (price_btc * BTC_EUR) * 100",
break_even_formula: "break_even_primary_blocks = (ticket_price_btc - expected_merge_reward_btc_equiv) / primary_block_reward_btc_equiv",
break_even_probability: "Approximate P(primary blocks >= ceil(break_even_primary_blocks)) under a Poisson block-arrival model.",
sanity_relative_tolerance: SANITY_RELATIVE_TOLERANCE,
sanity_absolute_tolerance: SANITY_ABSOLUTE_TOLERANCE,
near_100_percent_rule: "If NiceHash and model are both >=99%, sanity check passes because displayed odds may saturate.",
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
});
}

return jsonResponse({
status: "OK",
ok: true,
source: "NiceHash EasyMining",
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
upstream_status: response.status,
upstream_latency_ms: latency,
package_count: analysed.length,
available_endpoints: ["/test", "/prices", "/radar", "/buy-feed", "/buy-feed-shadow", "/history", "/schema"],
mining_math: "HASHRATE_DERIVED",
market_value_status: "PROFITABILITY_AVAILABLE_AT_/buy-feed",
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
});
} catch (err) {
const message = err?.name === "AbortError" ? "Request timed out" : String(err?.message || err);
return fail(message);
}
}
};

async function historyEndpoint(env) {
  if (!env?.RADAR_HISTORY) return jsonResponse({ status: 'HISTORY INVALID', ok: false, error: 'RADAR_HISTORY binding missing', checked_at: new Date().toISOString() });
  try {
    const listed = await listAllHistoryKeys(env.RADAR_HISTORY);
    return jsonResponse({ status: 'HISTORY LIST OK', ok: true, relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
      binding: 'RADAR_HISTORY', kv_read_write_test: 'NOT_RUN_READ_ONLY_DIAGNOSTIC',
      snapshot_count_listed: listed.keys.length, list_complete: true, list_pages: listed.pages,
      recent_snapshot_keys: listed.keys.map(x => x.name).sort().reverse().slice(0, 20),
      checked_at: new Date().toISOString(), schema_version: SCHEMA_VERSION });
  } catch (err) {
    return jsonResponse({ status: 'HISTORY INVALID', ok: false, relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
      error: String(err?.message || err), checked_at: new Date().toISOString(), schema_version: SCHEMA_VERSION });
  }
}

async function storeHistorySnapshot(env, packages, marketSnapshot) {
if (!env || !env.RADAR_HISTORY) {
return { ok: false, status: "HISTORY WARNING", error: "RADAR_HISTORY KV binding is missing" };
}

try {
const now = new Date();
const timestamp = now.getTime();
const key = `snapshot:${String(Math.floor(timestamp / 3600000) * 3600000).padStart(13, "0")}`;

const snapshot = {
captured_at: now.toISOString(),
captured_at_ms: timestamp,
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
market_provider: marketSnapshot.provider,
packages: packages.map(pkg => ({
name: pkg.name,
size: pkg.size,
currency_market: pkg.currency_market ?? "BTC",
price_native: pkg.price_native ?? pkg.price_btc,
price_btc: pkg.price_btc,
price_btc_equiv: pkg.price_btc_equiv ?? pkg.price_btc,
available: pkg.available,
nicehash_odds_denominator: pkg.nicehash_odds?.denominator ?? null,
nicehash_hit_probability_percent: pkg.nicehash_odds?.hit_probability_percent ?? null,
model_hit_probability_percent: pkg.primary_chain?.model_hit_probability_percent ?? null,
primary_expected_blocks: pkg.primary_chain?.expected_blocks ?? null,
primary_expected_reward_native: pkg.primary_chain?.expected_reward_native ?? null,
merge_expected_reward_native: pkg.merge_chain?.expected_reward_native ?? null,
primary_currency: pkg.primary_chain?.currency ?? null,
merge_currency: pkg.merge_chain?.currency ?? null,
ev_eur: pkg.economics?.ev_eur ?? null,
package_cost_eur: pkg.economics?.package_cost_eur ?? null,
ev_cost_percent: pkg.economics?.ev_cost_percent ?? null,
economic_status: pkg.economics?.status ?? null,
mining_signal: pkg.mining_signal ?? null,
economic_signal: pkg.economic_signal ?? null,
final_signal: pkg.final_signal ?? null,
expected_reward_btc_equiv: pkg.profitability?.expected_reward_btc_equiv ?? null,
expected_return_multiple: pkg.profitability?.expected_return_multiple ?? null,
profitability_margin_percent: pkg.profitability?.profitability_margin_percent ?? null,
break_even_primary_blocks: pkg.profitability?.break_even_primary_blocks ?? null,
break_even_block_target: pkg.profitability?.break_even_block_target ?? null,
break_even_multiple_vs_expected_blocks: pkg.profitability?.break_even_multiple_vs_expected_blocks ?? null,
break_even_probability_percent_approx: pkg.profitability?.break_even_probability_percent_approx ?? null,
break_even_risk: pkg.profitability?.break_even_risk ?? null,
data_quality: pkg.data_quality,
math_status: pkg.math_validation?.status ?? null
}))
};

await env.RADAR_HISTORY.put(key, JSON.stringify(snapshot), { expirationTtl: HISTORY_RETENTION_SECONDS });
return { ok: true, status: "HISTORY SAVED", key };
} catch (err) {
return { ok: false, status: "HISTORY WARNING", error: String(err?.message || err) };
}
}

function buildHistoryUnavailable(status, error) {
return {
ok: false,
status,
error: error ?? null,
sample_count: 0,
hourly_sample_count: 0,
coverage_hours: 0,
by_package: {}
};
}

async function buildHistoryAnalysis(env, currentPackages) {
  if (!env?.RADAR_HISTORY) return buildHistoryUnavailable('HISTORY INVALID', 'RADAR_HISTORY binding missing');
  try {
    const nowMs = Date.now();
    const hourMs = 3600000;
    const listed = await listAllHistoryKeys(env.RADAR_HISTORY);
    const candidates = new Map();
    let invalidKeys = 0, expiredKeys = 0, futureKeys = 0;
    for (const { name: key } of listed.keys) {
      const ts = snapshotTimestampFromKey(key);
      if (ts === null) { invalidKeys++; continue; }
      if (ts > nowMs) { futureKeys++; continue; }
      if (nowMs - ts > HISTORY_WINDOW_7D_MS + hourMs) { expiredKeys++; continue; }
      const bucket = Math.floor(ts / hourMs);
      const group = candidates.get(bucket) || { newest: null, hourly: null };
      if (ts % hourMs === 0) group.hourly = { key, ts };
      if (!group.newest || ts > group.newest.ts) group.newest = { key, ts };
      candidates.set(bucket, group);
    }
    // A rounded hourly key and a legacy key may coexist: compare actual observation times.
    const selected = [...new Set([...candidates.values()].flatMap(g => [g.newest?.key, g.hourly?.key]).filter(Boolean))];
    const byHour = new Map();
    let invalidPayloads = 0, missingPayloads = 0;
    for (let offset = 0; offset < selected.length; offset += 20) {
      const batch = selected.slice(offset, offset + 20);
      const values = await Promise.all(batch.map(key => env.RADAR_HISTORY.get(key, 'json')));
      for (let i = 0; i < values.length; i++) {
        const snapshot = values[i];
        if (snapshot === null) { missingPayloads++; continue; }
        const keyTs = snapshotTimestampFromKey(batch[i]);
        const ts = numberOrNull(snapshot?.captured_at_ms) ?? Date.parse(snapshot?.captured_at || '');
        if (!Number.isFinite(ts) || ts > nowMs || !Array.isArray(snapshot?.packages) ||
            Math.floor(ts / hourMs) !== Math.floor(keyTs / hourMs)) { invalidPayloads++; continue; }
        if (nowMs - ts > HISTORY_WINDOW_7D_MS) continue;
        const bucket = Math.floor(ts / hourMs);
        const existing = byHour.get(bucket);
        if (!existing || ts > existing._ts) byHour.set(bucket, { ...snapshot, _ts: ts });
      }
    }
    const snapshots = [...byHour.values()].sort((a, b) => a._ts - b._ts);
    const diagnostics = {
      listed_keys: listed.keys.length, listed_pages: listed.pages, list_complete: true,
      eligible_keys: selected.length, loaded_hourly_snapshots: snapshots.length,
      invalid_keys: invalidKeys, expired_keys: expiredKeys, future_keys: futureKeys,
      invalid_payloads: invalidPayloads, missing_payloads: missingPayloads,
      newest_snapshot_at: snapshots.length ? new Date(snapshots.at(-1)._ts).toISOString() : null
    };
    const broken = invalidPayloads > 0 || missingPayloads > 0 || futureKeys > 0 ||
      (listed.keys.length > 20 && snapshots.length === 0);
    if (broken) return { ...buildHistoryUnavailable('HISTORY INVALID', 'HISTORY_MISSING_OR_INVALID_OBSERVATIONS'), sample_count: listed.keys.length, diagnostics };
    if (snapshots.length && nowMs - snapshots.at(-1)._ts > 2 * hourMs) {
      return { ...buildHistoryUnavailable('HISTORY STALE', 'HISTORY_NEWEST_OBSERVATION_OLDER_THAN_2H'), sample_count: listed.keys.length, diagnostics };
    }
    const byPackage = {};
    for (const pkg of currentPackages) byPackage[pkg.name] = analysePackageHistory(pkg, snapshots, nowMs);
    const statuses = Object.values(byPackage).map(item => item.trend_status);
    const status = statuses.includes('7D READY') ? '7D READY' : statuses.includes('24H READY') ? '24H READY' : 'HISTORY WARMING UP';
    return {
      ok: true, status, error: null, sample_count: listed.keys.length,
      hourly_sample_count: snapshots.length,
      coverage_hours: snapshots.length > 1 ? round((snapshots.at(-1)._ts - snapshots[0]._ts) / hourMs, 2) : 0,
      list_complete: true, diagnostics, by_package: byPackage
    };
  } catch (err) {
    return buildHistoryUnavailable('HISTORY INVALID', String(err?.message || err));
  }
}

function snapshotTimestampFromKey(key) {
const match = /^snapshot:(\d{13})$/.exec(String(key || ""));
if (!match) return null;
const ts = Number(match[1]);
return Number.isFinite(ts) ? ts : null;
}

function analysePackageHistory(pkg, snapshots, nowMs) {
const rows = [];
for (const snapshot of snapshots) {
const historical = Array.isArray(snapshot.packages)
? snapshot.packages.find(item => item?.name === pkg.name)
: null;
if (!historical) continue;

rows.push({
ts: snapshot._ts,
ev_cost_percent: numberOrNull(historical.ev_cost_percent),
ev_eur: numberOrNull(historical.ev_eur),
package_cost_eur: numberOrNull(historical.package_cost_eur),
odds_denominator: numberOrNull(historical.nicehash_odds_denominator),
price_btc: numberOrNull(historical.price_btc),
hit_probability: probabilityFromStoredPercent(historical.model_hit_probability_percent),
expected_blocks: numberOrNull(historical.primary_expected_blocks),
primary_reward_native: numberOrNull(historical.primary_expected_reward_native),
merge_reward_native: numberOrNull(historical.merge_expected_reward_native)
});
}

const rows24 = rows.filter(row => nowMs - row.ts <= HISTORY_WINDOW_24H_MS);
const rows7d = rows.filter(row => nowMs - row.ts <= HISTORY_WINDOW_7D_MS);
const baseline24 = makeBaseline(rows24, nowMs);
const baseline7d = makeBaseline(rows7d, nowMs);

const ready24 = baseline24.sample_count >= HISTORY_24H_MIN_SAMPLES && baseline24.coverage_hours >= HISTORY_24H_MIN_COVERAGE_HOURS;
const ready7d = baseline7d.sample_count >= HISTORY_7D_MIN_SAMPLES && baseline7d.coverage_hours >= HISTORY_7D_MIN_COVERAGE_HOURS;

const currentQuality = miningQualityMetrics(pkg);
const blocksVs24 = relativeImprovement(currentQuality.expected_blocks_per_btc, baseline24.median_expected_blocks_per_btc);
const blocksVs7d = relativeImprovement(currentQuality.expected_blocks_per_btc, baseline7d.median_expected_blocks_per_btc);
const oddsEfficiencyVs24 = relativeImprovement(currentQuality.odds_efficiency_per_btc, baseline24.median_odds_efficiency_per_btc);
const oddsEfficiencyVs7d = relativeImprovement(currentQuality.odds_efficiency_per_btc, baseline7d.median_odds_efficiency_per_btc);
const rewardVs24 = relativeImprovement(currentQuality.primary_reward_native_per_btc, baseline24.median_primary_reward_native_per_btc);
const rewardVs7d = relativeImprovement(currentQuality.primary_reward_native_per_btc, baseline7d.median_primary_reward_native_per_btc);

const sevenDayNotAdverse = !ready7d || !(
(blocksVs7d !== null && blocksVs7d < -5) &&
(oddsEfficiencyVs7d !== null && oddsEfficiencyVs7d < -5)
);

let qualitySignal = "HISTORY WARMING UP";
if (ready24 && blocksVs24 !== null) {
if (blocksVs24 < QUALITY_THRESHOLDS.NO_BUY_BELOW) qualitySignal = "NO BUY";
else if (blocksVs24 < QUALITY_THRESHOLDS.WAIT_BELOW) qualitySignal = "WAIT";
else if (blocksVs24 < QUALITY_THRESHOLDS.GOOD_BELOW) qualitySignal = "GOOD";
else if (blocksVs24 < QUALITY_THRESHOLDS.BUY_NOW_BELOW) qualitySignal = "BUY NOW";
else {
const confirmation =
(oddsEfficiencyVs24 !== null && oddsEfficiencyVs24 >= 10) ||
(rewardVs24 !== null && rewardVs24 >= 15);
qualitySignal = confirmation && sevenDayNotAdverse ? "STRONG BUY" : "BUY NOW";
}
}

let trendStatus = "HISTORY WARMING UP";
if (ready7d) trendStatus = "7D READY";
else if (ready24) trendStatus = "24H READY";

return {
trend_status: trendStatus,
sample_count_24h: baseline24.sample_count,
sample_count_7d: baseline7d.sample_count,
coverage_hours_24h: baseline24.coverage_hours,
coverage_hours_7d: baseline7d.coverage_hours,
baseline_24h: baseline24,
baseline_7d: baseline7d,
decision_basis: "MINING_QUALITY_NOT_EUR_TICKET_PRICE",
current_mining_quality: currentQuality,
expected_blocks_per_btc_vs_24h_percent: roundOrNull(blocksVs24, 4),
expected_blocks_per_btc_vs_7d_percent: roundOrNull(blocksVs7d, 4),
odds_efficiency_per_btc_vs_24h_percent: roundOrNull(oddsEfficiencyVs24, 4),
odds_efficiency_per_btc_vs_7d_percent: roundOrNull(oddsEfficiencyVs7d, 4),
primary_reward_native_per_btc_vs_24h_percent: roundOrNull(rewardVs24, 4),
primary_reward_native_per_btc_vs_7d_percent: roundOrNull(rewardVs7d, 4),
seven_day_not_adverse: sevenDayNotAdverse,
quality_signal: qualitySignal,
current_ev_cost_percent_informational: numberOrNull(pkg.economics?.ev_cost_percent),
current_odds_denominator: numberOrNull(pkg.nicehash_odds?.denominator),
note: !ready24
? "Mining-quality baseline is still warming up; EUR ticket price does not drive BUY decisions."
: "Mining quality is evaluated per BTC versus historical baseline; final signal is profitability- and break-even-risk-gated in v2.9.0."
};
}

function makeBaseline(rows, nowMs) {
const evCosts = rows.map(row => row.ev_cost_percent).filter(Number.isFinite);
const evEur = rows.map(row => row.ev_eur).filter(Number.isFinite);
const costs = rows.map(row => row.package_cost_eur).filter(Number.isFinite);
const odds = rows.map(row => row.odds_denominator).filter(value => Number.isFinite(value) && value > 0);

const qualityRows = rows.map(historyQualityMetrics);
const blocksPerBtc = qualityRows.map(x => x.expected_blocks_per_btc).filter(Number.isFinite);
const oddsEfficiency = qualityRows.map(x => x.odds_efficiency_per_btc).filter(Number.isFinite);
const rewardPerBtc = qualityRows.map(x => x.primary_reward_native_per_btc).filter(Number.isFinite);

const oldestTs = rows.length ? Math.min(...rows.map(row => row.ts)) : null;
const newestTs = rows.length ? Math.max(...rows.map(row => row.ts)) : null;
const coverageHours = oldestTs !== null ? Math.max(0, (nowMs - oldestTs) / (60 * 60 * 1000)) : 0;

return {
sample_count: rows.length,
coverage_hours: round(coverageHours, 2),
oldest_at: oldestTs !== null ? new Date(oldestTs).toISOString() : null,
newest_at: newestTs !== null ? new Date(newestTs).toISOString() : null,
median_expected_blocks_per_btc: roundOrNull(median(blocksPerBtc), 8),
median_odds_efficiency_per_btc: roundOrNull(median(oddsEfficiency), 8),
median_primary_reward_native_per_btc: roundOrNull(median(rewardPerBtc), 8),
median_ev_cost_percent_informational: roundOrNull(median(evCosts), 4),
median_ev_eur_informational: roundOrNull(median(evEur), 4),
median_package_cost_eur_informational: roundOrNull(median(costs), 4),
median_odds_denominator: roundOrNull(median(odds), 4)
};
}

function probabilityFromStoredPercent(value) {
const p = numberOrNull(value);
return p !== null && p >= 0 ? Math.min(1, p / 100) : null;
}

function expectedBlocksFromProbability(p) {
if (!Number.isFinite(p) || p < 0 || p >= 1) return null;
return -Math.log(1 - p);
}

function historyQualityMetrics(row) {
const priceBtc = numberOrNull(row.price_btc);
if (priceBtc === null || priceBtc <= 0) {
return { expected_blocks_per_btc: null, odds_efficiency_per_btc: null, primary_reward_native_per_btc: null };
}
const probability = numberOrNull(row.hit_probability);
const expectedBlocks = numberOrNull(row.expected_blocks) ?? expectedBlocksFromProbability(probability);
const reward = numberOrNull(row.primary_reward_native);
return {
expected_blocks_per_btc: expectedBlocks !== null ? expectedBlocks / priceBtc : null,
odds_efficiency_per_btc: probability !== null ? probability / priceBtc : null,
primary_reward_native_per_btc: reward !== null ? reward / priceBtc : null
};
}

function miningQualityMetrics(pkg) {
const priceBtc = numberOrNull(pkg.price_btc);
const probability = numberOrNull(pkg.primary_chain?.model_hit_probability);
const expectedBlocks = numberOrNull(pkg.primary_chain?.expected_blocks);
const primaryReward = numberOrNull(pkg.primary_chain?.expected_reward_native);
const mergeReward = numberOrNull(pkg.merge_chain?.expected_reward_native);
const hashrate = numberOrNull(pkg.package_hashrate_hps);

if (priceBtc === null || priceBtc <= 0) {
return { expected_blocks_per_btc: null, odds_efficiency_per_btc: null, primary_reward_native_per_btc: null, merge_reward_native_per_btc: null, hashrate_hps_per_btc: null };
}

return {
expected_blocks_per_btc: roundOrNull(expectedBlocks !== null ? expectedBlocks / priceBtc : null, 8),
odds_efficiency_per_btc: roundOrNull(probability !== null ? probability / priceBtc : null, 8),
primary_reward_native_per_btc: roundOrNull(primaryReward !== null ? primaryReward / priceBtc : null, 8),
merge_reward_native_per_btc: roundOrNull(mergeReward !== null ? mergeReward / priceBtc : null, 8),
hashrate_hps_per_btc: hashrate !== null ? hashrate / priceBtc : null
};
}

function median(values) {
if (!Array.isArray(values) || !values.length) return null;
const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
if (!sorted.length) return null;
const middle = Math.floor(sorted.length / 2);
return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function relativeImprovement(current, baseline) {
if (!Number.isFinite(current) || !Number.isFinite(baseline) || baseline === 0) return null;
return (current - baseline) / Math.abs(baseline) * 100;
}

function attachHistoryTrend(pkg, historyTrend) {
const miningSignal = historyTrend?.quality_signal ?? "HISTORY WARMING UP";
return {
...pkg,
mining_quality: miningQualityMetrics(pkg),
history_trend: historyTrend,
mining_signal: miningSignal,
final_signal: miningSignal
};
}

function attachProfitabilityDecision(pkg, market) {
const profitability = profitabilityMetrics(pkg, market);
const miningSignal = pkg.mining_signal ?? pkg.history_trend?.quality_signal ?? "HISTORY WARMING UP";
const finalSignal = combineMiningAndProfitability(
miningSignal,
profitability.economic_signal,
profitability.break_even_risk
);

return {
...pkg,
profitability,
mining_signal: miningSignal,
economic_signal: profitability.economic_signal,
final_signal: finalSignal,
decision: {
mining_signal: miningSignal,
economic_signal: profitability.economic_signal,
final_signal: finalSignal,
break_even_risk: profitability.break_even_risk,
rationale: finalDecisionRationale(miningSignal, profitability.economic_signal, finalSignal, profitability.break_even_risk)
}
};
}

function profitabilityMetrics(pkg, market) {
const prices = market?.prices || {};
const btcEur = marketPriceEur(prices, "BTC");
const priceBtc = numberOrNull(pkg.price_btc);

const primaryCurrency = String(pkg.primary_chain?.currency || "").toUpperCase();
const mergeCurrency = String(pkg.merge_chain?.currency || "").toUpperCase();
const primaryCoinEur = marketPriceEur(prices, primaryCurrency);
const mergeCoinEur = pkg.merge_chain ? marketPriceEur(prices, mergeCurrency) : null;

const primaryExpectedReward = numberOrNull(pkg.primary_chain?.expected_reward_native);
const mergeExpectedReward = numberOrNull(pkg.merge_chain?.expected_reward_native);
const expectedBlocks = numberOrNull(pkg.primary_chain?.expected_blocks);
const primaryBlockReward = numberOrNull(pkg.primary_chain?.block_reward);

const primaryExpectedRewardBtc =
primaryExpectedReward !== null && primaryCoinEur !== null && btcEur !== null && btcEur > 0
? primaryExpectedReward * primaryCoinEur / btcEur
: null;

const mergeExpectedRewardBtc = pkg.merge_chain
? (mergeExpectedReward !== null && mergeCoinEur !== null && btcEur !== null && btcEur > 0
? mergeExpectedReward * mergeCoinEur / btcEur
: null)
: 0;

const complete =
priceBtc !== null && priceBtc > 0 && btcEur !== null && btcEur > 0 &&
primaryExpectedRewardBtc !== null && (!pkg.merge_chain || mergeExpectedRewardBtc !== null);

const expectedRewardBtc = complete
? primaryExpectedRewardBtc + (mergeExpectedRewardBtc || 0)
: null;

const expectedReturnMultiple =
complete && expectedRewardBtc !== null ? expectedRewardBtc / priceBtc : null;

const profitabilityMarginPercent =
expectedReturnMultiple !== null ? (expectedReturnMultiple - 1) * 100 : null;

const primaryBlockRewardBtc =
primaryBlockReward !== null && primaryCoinEur !== null && btcEur !== null && btcEur > 0
? primaryBlockReward * primaryCoinEur / btcEur
: null;

const remainingCostAfterExpectedMerge = complete
? Math.max(0, priceBtc - (mergeExpectedRewardBtc || 0))
: null;

const breakEvenPrimaryBlocks =
remainingCostAfterExpectedMerge !== null && primaryBlockRewardBtc !== null && primaryBlockRewardBtc > 0
? remainingCostAfterExpectedMerge / primaryBlockRewardBtc
: null;

const breakEvenMultiple =
breakEvenPrimaryBlocks !== null && expectedBlocks !== null && expectedBlocks > 0
? breakEvenPrimaryBlocks / expectedBlocks
: null;

const breakEvenBlockTarget = breakEvenPrimaryBlocks !== null ? Math.ceil(breakEvenPrimaryBlocks) : null;
const breakEvenProbability =
breakEvenBlockTarget !== null && expectedBlocks !== null
? poissonAtLeast(breakEvenBlockTarget, expectedBlocks)
: null;

const economicSignal = profitabilitySignal(expectedReturnMultiple);
const breakEvenRisk = breakEvenRiskAssessment(
expectedReturnMultiple,
breakEvenProbability,
breakEvenMultiple,
breakEvenBlockTarget,
expectedBlocks
);

return {
complete,
basis: "BTC_EQUIVALENT_FROM_CURRENT_COIN_BTC_RATIOS",
economic_signal: economicSignal,
break_even_risk: breakEvenRisk,
package_cost_btc: priceBtc,
expected_reward_btc_equiv: roundOrNull(expectedRewardBtc, 10),
expected_return_multiple: roundOrNull(expectedReturnMultiple, 6),
expected_return_percent: roundOrNull(expectedReturnMultiple !== null ? expectedReturnMultiple * 100 : null, 4),
profitability_margin_percent: roundOrNull(profitabilityMarginPercent, 4),
expected_primary_blocks: roundOrNull(expectedBlocks, 6),
break_even_primary_blocks: roundOrNull(breakEvenPrimaryBlocks, 4),
break_even_block_target: breakEvenBlockTarget,
break_even_multiple_vs_expected_blocks: roundOrNull(breakEvenMultiple, 6),
break_even_probability_approx: roundOrNull(breakEvenProbability, 10),
break_even_probability_percent_approx: roundOrNull(breakEvenProbability !== null ? breakEvenProbability * 100 : null, 8),
merge_expected_reward_btc_equiv: pkg.merge_chain ? roundOrNull(mergeExpectedRewardBtc, 10) : 0,
note: pkg.merge_chain
? "Break-even block target uses expected merge-mining value as an offset; probability is therefore approximate."
: "Break-even probability models primary-chain blocks with a Poisson process."
};
}

function profitabilitySignal(expectedReturnMultiple) {
if (!Number.isFinite(expectedReturnMultiple)) return "UNKNOWN";
if (expectedReturnMultiple >= PROFITABILITY_THRESHOLDS.POSITIVE_MIN_RETURN_MULTIPLE) return "POSITIVE";
if (expectedReturnMultiple >= PROFITABILITY_THRESHOLDS.FAIR_MIN_RETURN_MULTIPLE) return "FAIR";
if (expectedReturnMultiple >= PROFITABILITY_THRESHOLDS.MARGINAL_MIN_RETURN_MULTIPLE) return "MARGINAL";
return "POOR";
}

function combineMiningAndProfitability(miningSignal, economicSignal, breakEvenRisk = null) {
if (miningSignal === "HISTORY WARMING UP") return "WAIT";
if (economicSignal === "UNKNOWN") return "WAIT";
if (economicSignal === "POOR") return "NO BUY";

let baseFinal = "WAIT";

if (economicSignal === "MARGINAL") {
baseFinal = (miningSignal === "STRONG BUY" || miningSignal === "BUY NOW") ? "GOOD" : "WAIT";
} else if (economicSignal === "FAIR") {
if (miningSignal === "STRONG BUY") baseFinal = "BUY NOW";
else if (miningSignal === "BUY NOW" || miningSignal === "GOOD") baseFinal = "GOOD";
else baseFinal = miningSignal === "NO BUY" ? "NO BUY" : "WAIT";
} else if (economicSignal === "POSITIVE") {
// Jackpot protection: positive expected BTC-equivalent value is never downgraded
// merely because absolute break-even probability is low.
baseFinal = miningSignal;
}

return capSignal(baseFinal, breakEvenRisk?.max_final_signal ?? null);
}

function breakEvenRiskAssessment(expectedReturnMultiple, breakEvenProbability, breakEvenMultiple, breakEvenBlockTarget, expectedBlocks) {
if (!Number.isFinite(expectedReturnMultiple)) {
return { status: "UNKNOWN", max_final_signal: "WAIT", rationale: "Expected return is unavailable." };
}

const probabilityPercent = Number.isFinite(breakEvenProbability) ? breakEvenProbability * 100 : null;

// Deliberate v2.9.0 exception discussed during design: do not kill low-lambda jackpot
// opportunities solely because P(break-even) is small when EV itself is positive.
if (expectedReturnMultiple >= 1) {
return {
status: "POSITIVE_EV_JACKPOT_EXEMPT",
max_final_signal: null,
probability_percent: roundOrNull(probabilityPercent, 8),
rationale: "Expected BTC-equivalent return is >=100%; low absolute break-even probability does not cap the signal."
};
}

if (!Number.isFinite(probabilityPercent)) {
return { status: "UNKNOWN", max_final_signal: "WAIT", rationale: "Break-even probability is unavailable for a negative-EV setup." };
}

if (
expectedReturnMultiple < BREAK_EVEN_RISK_THRESHOLDS.DEEP_NEGATIVE_EV_RETURN_MULTIPLE &&
probabilityPercent < BREAK_EVEN_RISK_THRESHOLDS.ULTRA_LOW_PROBABILITY_PERCENT
) {
return {
status: "ULTRA_LOW_BREAK_EVEN_NEGATIVE_EV",
max_final_signal: "WAIT",
probability_percent: roundOrNull(probabilityPercent, 8),
rationale: "Negative EV below 90% expected return combined with <1% break-even probability is not actionable."
};
}

if (
expectedReturnMultiple < BREAK_EVEN_RISK_THRESHOLDS.MODERATE_NEGATIVE_EV_RETURN_MULTIPLE &&
probabilityPercent < BREAK_EVEN_RISK_THRESHOLDS.LOW_PROBABILITY_PERCENT
) {
return {
status: "LOW_BREAK_EVEN_NEGATIVE_EV",
max_final_signal: "GOOD",
probability_percent: roundOrNull(probabilityPercent, 8),
rationale: "Negative EV below 95% with <5% break-even probability is capped at GOOD."
};
}

if (
Number.isFinite(breakEvenMultiple) &&
breakEvenMultiple > 1 / BREAK_EVEN_RISK_THRESHOLDS.DEEP_NEGATIVE_EV_RETURN_MULTIPLE &&
expectedReturnMultiple < BREAK_EVEN_RISK_THRESHOLDS.MODERATE_NEGATIVE_EV_RETURN_MULTIPLE &&
probabilityPercent < 10
) {
return {
status: "STRETCHED_BREAK_EVEN",
max_final_signal: "GOOD",
probability_percent: roundOrNull(probabilityPercent, 8),
rationale: "Break-even requires materially more blocks than expected; negative-EV signal is capped at GOOD."
};
}

const jackpotLike = breakEvenBlockTarget === 1 && Number.isFinite(expectedBlocks) && expectedBlocks < 0.10;
return {
status: jackpotLike ? "LOW_LAMBDA_NEGATIVE_EV_MONITORED" : "NORMAL",
max_final_signal: null,
probability_percent: roundOrNull(probabilityPercent, 8),
rationale: jackpotLike
? "Low-lambda package retained for monitoring; no automatic rejection solely from low block count."
: "No additional v2.9.0 break-even risk cap applied."
};
}

function capSignal(signal, maxSignal) {
if (!maxSignal) return signal;
const rank = { "NO BUY": 0, "WAIT": 1, "GOOD": 2, "BUY NOW": 3, "STRONG BUY": 4 };
if (!(signal in rank) || !(maxSignal in rank)) return signal;
return rank[signal] > rank[maxSignal] ? maxSignal : signal;
}

function finalDecisionRationale(miningSignal, economicSignal, finalSignal, breakEvenRisk = null) {
const risk = breakEvenRisk?.status ? `; break-even-risk=${breakEvenRisk.status}` : "";
return `Mining=${miningSignal}; profitability=${economicSignal}${risk}; final=${finalSignal}. Profitability is primary and negative-EV setups can be capped by break-even risk.`;
}

function poissonAtLeast(k, lambda) {
if (!Number.isFinite(k) || !Number.isFinite(lambda) || k < 0 || lambda < 0) return null;
k = Math.ceil(k);
if (k <= 0) return 1;
if (lambda === 0) return 0;

if (lambda < 80) {
let term = Math.exp(-lambda);
let cdf = term;
for (let i = 1; i < k; i++) {
term *= lambda / i;
cdf += term;
if (term < 1e-16 && i > lambda) break;
}
return clamp01(1 - cdf);
}

const z = (k - 0.5 - lambda) / Math.sqrt(lambda);
const cdf = 0.5 * (1 + erfApprox(z / Math.SQRT2));
return clamp01(1 - cdf);
}

function erfApprox(x) {
const sign = x < 0 ? -1 : 1;
const ax = Math.abs(x);
const t = 1 / (1 + 0.3275911 * ax);
const y = 1 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t) * Math.exp(-ax * ax);
return sign * y;
}

function clamp01(value) {
if (!Number.isFinite(value)) return null;
return Math.max(0, Math.min(1, value));
}

async function pricesEndpoint() {
const snapshot = await fetchMarketSnapshot();
return jsonResponse({
status: snapshot.status,
ok: snapshot.ok,
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
provider: snapshot.provider,
provider_latency_ms: snapshot.provider_latency_ms,
market: snapshot.market,
error: snapshot.error ?? null,
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
}, 200);
}

async function fetchMarketSnapshot() {
const started = Date.now();
const provider = "Kraken primary + CoinPaprika fallback";
const krakenPairs = {
BTC: "XBTEUR",
BCH: "BCHEUR",
LTC: "LTCEUR",
DOGE: "XDGEUR",
ZEC: "ZECEUR",
KAS: "KASEUR",
USDT: "USDTEUR"
};

try {
const entries = await Promise.all(
Object.entries(COIN_IDS).map(async ([symbol, id]) => {
const primary = await fetchKrakenEurPrice(symbol, krakenPairs[symbol]);
if (primary.ok) return primary;

const fallback = await fetchCoinPaprikaPrice(symbol, id);
if (fallback.ok) {
return {
...fallback,
fallback_used: true,
primary_provider: "Kraken",
primary_status: primary.status,
primary_error: primary.error
};
}

return {
...primary,
fallback_used: false,
fallback_provider: "CoinPaprika",
fallback_status: fallback.status,
fallback_error: fallback.error
};
})
);

const market = buildMarketData(entries);
return {
status: market.ok ? "MARKET OK" : "MARKET WARNING",
ok: market.ok,
provider,
provider_latency_ms: Date.now() - started,
market,
error: null
};
} catch (err) {
return {
status: "MARKET WARNING",
ok: false,
provider,
provider_latency_ms: Date.now() - started,
market: {
ok: false,
status: "MARKET WARNING",
stale_or_missing: Object.keys(COIN_IDS),
request_errors: [],
max_age_seconds: MARKET_MAX_AGE_SECONDS,
prices: {}
},
error: String(err?.message || err)
};
}
}

async function fetchCoinPaprikaPrice(symbol, id) {
const fetchUrl = `${COINPAPRIKA_BASE_URL}/${id}?quotes=EUR`;
try {
const response = await fetchWithTimeout(fetchUrl, MARKET_TIMEOUT_MS, {
Accept: "application/json",
"User-Agent": `NiceHash-EasyMining-Relay/${RELAY_VERSION}`
});
const status = response.status;

if (!response.ok) return { symbol, id, provider: "CoinPaprika", ok: false, status, error: `CoinPaprika HTTP ${status}`, eur: null, updated_at: null };

const body = await response.text();
if (!body || !body.trim()) return { symbol, id, provider: "CoinPaprika", ok: false, status, error: "CoinPaprika returned an empty response", eur: null, updated_at: null };

let data;
try { data = JSON.parse(body); }
catch { return { symbol, id, provider: "CoinPaprika", ok: false, status, error: "CoinPaprika response is not valid JSON", eur: null, updated_at: null }; }

const eur = numberOrNull(data.quotes?.EUR?.price);
const updatedMs = data.last_updated ? Date.parse(data.last_updated) : NaN;
const updatedAt = Number.isFinite(updatedMs) ? Math.floor(updatedMs / 1000) : null;

if (eur === null || eur <= 0 || updatedAt === null) {
return { symbol, id, provider: "CoinPaprika", ok: false, status, error: "CoinPaprika price or timestamp missing", eur: null, updated_at: null };
}

return { symbol, id, provider: "CoinPaprika", ok: true, status, error: null, eur, updated_at: updatedAt };
} catch (err) {
return {
symbol, id, provider: "CoinPaprika", ok: false, status: null,
error: err?.name === "AbortError" ? `CoinPaprika request timed out after ${MARKET_TIMEOUT_MS} ms` : String(err?.message || err),
eur: null, updated_at: null
};
}
}

async function fetchKrakenEurPrice(symbol, pair) {
const fetchUrl = `${KRAKEN_TICKER_URL}?pair=${encodeURIComponent(pair)}`;
try {
const response = await fetchWithTimeout(fetchUrl, MARKET_TIMEOUT_MS, {
Accept: "application/json",
"User-Agent": `NiceHash-EasyMining-Relay/${RELAY_VERSION}`
});
const status = response.status;

if (!response.ok) return { symbol, id: pair, provider: "Kraken", ok: false, status, error: `Kraken HTTP ${status}`, eur: null, updated_at: null };

const body = await response.text();
if (!body || !body.trim()) return { symbol, id: pair, provider: "Kraken", ok: false, status, error: "Kraken returned an empty response", eur: null, updated_at: null };

let data;
try { data = JSON.parse(body); }
catch { return { symbol, id: pair, provider: "Kraken", ok: false, status, error: "Kraken response is not valid JSON", eur: null, updated_at: null }; }

if (Array.isArray(data.error) && data.error.length > 0) {
return { symbol, id: pair, provider: "Kraken", ok: false, status, error: `Kraken API error: ${data.error.join(", ")}`, eur: null, updated_at: null };
}

const result = data.result && typeof data.result === "object" ? data.result : null;
const firstTicker = result ? Object.values(result)[0] : null;
const eur = numberOrNull(firstTicker && Array.isArray(firstTicker.c) ? firstTicker.c[0] : null);

if (eur === null || eur <= 0) return { symbol, id: pair, provider: "Kraken", ok: false, status, error: "Kraken ticker price missing", eur: null, updated_at: null };

return { symbol, id: pair, provider: "Kraken", ok: true, status, error: null, eur, updated_at: Math.floor(Date.now() / 1000) };
} catch (err) {
return {
symbol, id: pair, provider: "Kraken", ok: false, status: null,
error: err?.name === "AbortError" ? `Kraken request timed out after ${MARKET_TIMEOUT_MS} ms` : String(err?.message || err),
eur: null, updated_at: null
};
}
}

function buildMarketData(entries) {
const now = Math.floor(Date.now() / 1000);
const prices = {};
const staleOrMissing = [];
const requestErrors = [];

for (const item of entries) {
const { symbol, id, provider, ok, status, error, eur, updated_at: updatedAt, fallback_error: fallbackError, fallback_status: fallbackStatus, fallback_used: fallbackUsed, primary_provider: primaryProvider, primary_status: primaryStatus, primary_error: primaryError, fallback_provider: fallbackProvider } = item;

let age = updatedAt !== null && updatedAt !== undefined ? now - updatedAt : null;
if (age !== null && age < 0) age = 0;

const fresh = ok === true && eur !== null && eur > 0 && updatedAt !== null && updatedAt !== undefined && age <= MARKET_MAX_AGE_SECONDS;

prices[symbol] = {
id,
provider: provider || null,
eur: eur ?? null,
last_updated_at: updatedAt ?? null,
last_updated_iso: updatedAt !== null && updatedAt !== undefined ? new Date(updatedAt * 1000).toISOString() : null,
age_seconds: age,
fresh,
provider_status: status ?? null,
error: error ?? null,
fallback_status: fallbackStatus ?? null,
fallback_error: fallbackError ?? null,
fallback_used: fallbackUsed === true,
primary_provider: primaryProvider ?? null,
primary_status: primaryStatus ?? null,
primary_error: primaryError ?? null,
fallback_provider: fallbackProvider ?? null
};

if (!fresh) {
staleOrMissing.push(symbol);
requestErrors.push({ symbol, provider: provider || null, provider_status: status ?? null, error: error ?? null, fallback_status: fallbackStatus ?? null, fallback_error: fallbackError ?? null });
}
}

return {
ok: staleOrMissing.length === 0 && requestErrors.length === 0,
status: staleOrMissing.length === 0 && requestErrors.length === 0 ? "MARKET OK" : "MARKET WARNING",
stale_or_missing: staleOrMissing,
request_errors: requestErrors,
max_age_seconds: MARKET_MAX_AGE_SECONDS,
prices
};
}

async function fetchWithTimeout(fetchUrl, timeoutMs, headers) {
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), timeoutMs);
try {
return await fetch(fetchUrl, { method: "GET", headers, signal: controller.signal });
} finally {
clearTimeout(timeout);
}
}

function extractPackages(data) {
const objects = [];
walk(data, value => {
if (value && typeof value === "object" && !Array.isArray(value)) objects.push(value);
});

const result = [];
const seen = new Set();
for (const obj of objects) {
if (typeof obj.name !== "string" || obj.price === undefined) continue;
if (obj.currencyAlgo === undefined || obj.duration === undefined) continue;
const key = `${obj.id || ""}|${obj.name}|${obj.price}`;
if (seen.has(key)) continue;
seen.add(key);
result.push(obj);
}
return result;
}

function analysePackage(raw) {
const algorithm = raw.currencyAlgo?.miningAlgorithm;
const projectedSpeed = numberOrNull(raw.projectedSpeed);
const multiplier = hashrateMultiplier(algorithm);
const packageHashrate = projectedSpeed !== null && multiplier !== null ? projectedSpeed * multiplier : null;
const duration = numberOrNull(raw.duration);

const primary = analyseChain({
chain: raw.currencyAlgo,
nicehashDenominator: raw.probabilityPrecision,
probabilityRounded: raw.probability,
packageHashrate,
duration
});

const merge = raw.mergeCurrencyAlgo ? analyseChain({
chain: raw.mergeCurrencyAlgo,
nicehashDenominator: raw.mergeProbabilityPrecision,
probabilityRounded: raw.mergeProbability,
packageHashrate,
duration
}) : null;

const missing = missingPackageFields(raw, multiplier);
const complete = missing.length === 0;
const primaryPass = primary?.sanity_check?.pass === true;
const mergePass = merge ? merge.sanity_check?.pass === true : true;
const overallPass = complete && primaryPass && mergePass;
const currencyMarket = String(raw.currencyMarket || "BTC").toUpperCase();

return {
id: raw.id ?? null,
name: raw.name,
size: packageSize(raw.name),
currency_market: currencyMarket,
price_native: numberOrNull(raw.price),
price_btc: currencyMarket === "BTC" ? numberOrNull(raw.price) : null,
status: raw.status ?? null,
available: raw.available === true,
duration_seconds: duration,
projected_speed: projectedSpeed,
projected_speed_unit: hashrateUnit(algorithm),
package_hashrate_hps: packageHashrate,
nicehash_odds: {
denominator: numberOrNull(raw.probabilityPrecision),
display: oddsDisplay(raw.probabilityPrecision),
hit_probability: denominatorToProbability(raw.probabilityPrecision),
hit_probability_percent: probabilityPercent(denominatorToProbability(raw.probabilityPrecision))
},
primary_chain: primary,
merge_chain: merge,
expected_rewards_native: {
primary: primary?.expected_reward_native ?? null,
merge: merge?.expected_reward_native ?? null
},
data_quality: complete ? "COMPLETE" : "DATA INCOMPLETE",
missing_fields: missing,
math_validation: {
primary_pass: primaryPass,
merge_pass: mergePass,
overall_pass: overallPass,
status: !complete ? "DATA INCOMPLETE" : overallPass ? "PASS" : "DATA WARNING"
}
};
}

function analyseChain({ chain, nicehashDenominator, probabilityRounded, packageHashrate, duration }) {
if (!chain) return null;

const networkHashpower = numberOrNull(chain.networkHashpower);
const blockTime = numberOrNull(chain.blockTime);
const blockReward = numberOrNull(chain.blockReward);
const blockRewardWithNhFee = numberOrNull(chain.blockRewardWithNhFee);

const expectedBlocks = calculateExpectedBlocks(packageHashrate, networkHashpower, duration, blockTime);
const modelHitProbability = expectedBlocks !== null ? 1 - Math.exp(-expectedBlocks) : null;
const nicehashProbability = denominatorToProbability(nicehashDenominator);
const sanity = compareProbabilities(modelHitProbability, nicehashProbability);
const expectedReward = expectedBlocks !== null && blockReward !== null ? expectedBlocks * blockReward : null;
const expectedRewardUsingNhField = expectedBlocks !== null && blockRewardWithNhFee !== null ? expectedBlocks * blockRewardWithNhFee : null;

return {
currency: chain.currency ?? null,
algorithm: chain.miningAlgorithm ?? null,
network_difficulty: numberOrNull(chain.networkDifficulty),
network_hashpower_hps: networkHashpower,
block_time_seconds: blockTime,
block_reward: blockReward,
block_reward_with_nh_fee: blockRewardWithNhFee,
nicehash_odds_denominator: numberOrNull(nicehashDenominator),
nicehash_odds_display: oddsDisplay(nicehashDenominator),
nicehash_probability_rounded: numberOrNull(probabilityRounded),
nicehash_hit_probability: nicehashProbability,
nicehash_hit_probability_percent: probabilityPercent(nicehashProbability),
expected_blocks: expectedBlocks,
model_hit_probability: modelHitProbability,
model_hit_probability_percent: probabilityPercent(modelHitProbability),
model_odds_denominator: probabilityToDenominator(modelHitProbability),
model_odds_display: probabilityOddsDisplay(modelHitProbability),
expected_reward_native: finiteOrNull(expectedReward),
expected_reward_using_nh_reward_field: finiteOrNull(expectedRewardUsingNhField),
sanity_check: sanity
};
}

function compareProbabilities(model, nicehash) {
if (model === null || nicehash === null) {
return { pass: false, status: "DATA INCOMPLETE", absolute_difference: null, relative_difference: null };
}

const absolute = Math.abs(model - nicehash);
const relative = nicehash > 0 ? absolute / nicehash : null;

if (model >= 0.99 && nicehash >= 0.99) {
return { pass: true, status: "PASS_SATURATED_NEAR_100_PERCENT", absolute_difference: absolute, relative_difference: relative };
}

const pass = absolute <= SANITY_ABSOLUTE_TOLERANCE || (relative !== null && relative <= SANITY_RELATIVE_TOLERANCE);
return {
pass,
status: pass ? "PASS" : "DATA WARNING",
absolute_difference: absolute,
relative_difference: relative,
relative_difference_percent: relative !== null ? round(relative * 100, 4) : null
};
}

function calculateExpectedBlocks(packageHashrate, networkHashrate, duration, blockTime) {
if (packageHashrate === null || networkHashrate === null || duration === null || blockTime === null || packageHashrate < 0 || networkHashrate <= 0 || duration <= 0 || blockTime <= 0) return null;
return packageHashrate / networkHashrate * duration / blockTime;
}

function hashrateMultiplier(algorithm) {
switch (String(algorithm || "").toUpperCase()) {
case "SHA256ASICBOOST": return 1e18;
case "SHA256ASICBOOST_USDT": return 1e18;
case "SCRYPT": return 1e12;
case "EQUIHASH": return 1e9;
case "KHEAVYHASH": return 1e15;
default: return null;
}
}

function hashrateUnit(algorithm) {
switch (String(algorithm || "").toUpperCase()) {
case "SHA256ASICBOOST": return "EH/s";
case "SHA256ASICBOOST_USDT": return "EH/s";
case "SCRYPT": return "TH/s";
case "EQUIHASH": return "GH/s";
case "KHEAVYHASH": return "PH/s";
default: return null;
}
}

function addEconomicValue(pkg, market) {
const prices = market?.prices || {};
const btcEur = marketPriceEur(prices, "BTC");
const primaryCurrency = String(pkg.primary_chain?.currency || "").toUpperCase();
const mergeCurrency = String(pkg.merge_chain?.currency || "").toUpperCase();
const primaryCoinEur = marketPriceEur(prices, primaryCurrency);
const mergeCoinEur = pkg.merge_chain ? marketPriceEur(prices, mergeCurrency) : null;

const currencyMarket = String(pkg.currency_market || "BTC").toUpperCase();
const priceNative = numberOrNull(pkg.price_native ?? pkg.price_btc);
const usdtEur = marketPriceEur(prices, "USDT");
const nativeCostEur = currencyMarket === "USDT"
? (priceNative !== null && usdtEur !== null ? priceNative * usdtEur : null)
: (priceNative !== null && btcEur !== null ? priceNative * btcEur : null);
const normalizedPriceBtc = nativeCostEur !== null && btcEur !== null && btcEur > 0 ? nativeCostEur / btcEur : null;
const primaryReward = numberOrNull(pkg.primary_chain?.expected_reward_native);
const mergeReward = numberOrNull(pkg.merge_chain?.expected_reward_native);

const packageCostEur = nativeCostEur;
const primaryEvEur = primaryReward !== null && primaryCoinEur !== null ? primaryReward * primaryCoinEur : null;
const mergeEvEur = pkg.merge_chain
? (mergeReward !== null && mergeCoinEur !== null ? mergeReward * mergeCoinEur : null)
: 0;

const complete = market?.ok === true && packageCostEur !== null && packageCostEur > 0 && primaryEvEur !== null && (!pkg.merge_chain || mergeEvEur !== null);
const totalEvEur = complete ? primaryEvEur + (mergeEvEur || 0) : null;
const evCostPercent = complete && totalEvEur !== null ? totalEvEur / packageCostEur * 100 : null;

return {
...pkg,
price_btc: normalizedPriceBtc,
price_btc_equiv: normalizedPriceBtc,
economics: {
complete,
status: complete ? economicZone(evCostPercent) : "MARKET WARNING",
package_cost_eur: roundOrNull(packageCostEur, 4),
currency_market: currencyMarket,
price_native: priceNative,
usdt_eur: currencyMarket === "USDT" ? roundOrNull(usdtEur, 8) : null,
package_cost_btc_equiv: roundOrNull(normalizedPriceBtc, 10),
btc_eur: roundOrNull(btcEur, 8),
primary_currency: primaryCurrency || null,
primary_coin_eur: roundOrNull(primaryCoinEur, 8),
primary_expected_reward_native: primaryReward,
primary_ev_eur: roundOrNull(primaryEvEur, 4),
merge_currency: pkg.merge_chain ? (mergeCurrency || null) : null,
merge_coin_eur: pkg.merge_chain ? roundOrNull(mergeCoinEur, 8) : null,
merge_expected_reward_native: pkg.merge_chain ? mergeReward : null,
merge_ev_eur: pkg.merge_chain ? roundOrNull(mergeEvEur, 4) : 0,
ev_eur: roundOrNull(totalEvEur, 4),
ev_cost_percent: roundOrNull(evCostPercent, 4),
buy_signal_allowed: false,
role_in_final_signal: "INFORMATIONAL_ONLY",
note: complete && evCostPercent >= 100
? "EUR EV/cost is >=100%, but v2.9.0 final signal uses BTC-equivalent profitability, break-even risk, and mining-quality history."
: complete ? null : "Fail closed: no BUY signal while required market data is unavailable."
}
};
}

function marketPriceEur(prices, symbol) {
if (!symbol || !prices || !prices[symbol]) return null;
const item = prices[symbol];
if (item.fresh !== true) return null;
const value = numberOrNull(item.eur);
return value !== null && value > 0 ? value : null;
}

function economicZone(evCostPercent) {
if (evCostPercent === null || evCostPercent === undefined) return "MARKET WARNING";
if (evCostPercent < EV_THRESHOLDS.NO_BUY_BELOW) return "NO BUY";
if (evCostPercent < EV_THRESHOLDS.WAIT_BELOW) return "WAIT";
if (evCostPercent < EV_THRESHOLDS.GOOD_BELOW) return "GOOD";
if (evCostPercent < EV_THRESHOLDS.BUY_NOW_BELOW) return "BUY NOW";
return "STRONG BUY CANDIDATE";
}

function compactMarketPrices(market) {
const result = {};
const prices = market?.prices || {};
for (const symbol of Object.keys(COIN_IDS)) {
const item = prices[symbol];
result[symbol] = item
? { eur: item.eur ?? null, provider: item.provider ?? null, fresh: item.fresh === true, age_seconds: item.age_seconds ?? null }
: { eur: null, provider: null, fresh: false, age_seconds: null };
}
return result;
}

function buildEconomicComparisons(packages) {
const base = buildComparisons(packages);
const byName = new Map(packages.map(pkg => [pkg.name, pkg]));

return base.map(item => {
const m = byName.get(item.m_package);
const s = byName.get(item.s_package);
const sCount = item.s_count_for_m_budget;

const mEv = numberOrNull(m?.economics?.ev_eur);
const sEv = numberOrNull(s?.economics?.ev_eur);
const mCost = numberOrNull(m?.economics?.package_cost_eur);
const sCost = numberOrNull(s?.economics?.package_cost_eur);

const sBasketEv = sEv !== null ? sEv * sCount : null;
const sBasketCost = sCost !== null ? sCost * sCount : null;
const mEvCost = mEv !== null && mCost !== null && mCost > 0 ? mEv / mCost * 100 : null;
const sBasketEvCost = sBasketEv !== null && sBasketCost !== null && sBasketCost > 0 ? sBasketEv / sBasketCost * 100 : null;

const mReturn = numberOrNull(m?.profitability?.expected_return_multiple);
const sReturn = numberOrNull(s?.profitability?.expected_return_multiple);

return {
...item,
m_cost_eur: roundOrNull(mCost, 4),
m_ev_eur: roundOrNull(mEv, 4),
m_ev_cost_percent: roundOrNull(mEvCost, 4),
m_status: economicZone(mEvCost),
m_mining_signal: m?.mining_signal ?? null,
m_economic_signal: m?.economic_signal ?? null,
m_final_signal: m?.final_signal ?? null,
m_expected_return_multiple: roundOrNull(mReturn, 6),
s_basket_cost_eur: roundOrNull(sBasketCost, 4),
s_basket_ev_eur: roundOrNull(sBasketEv, 4),
s_basket_ev_cost_percent: roundOrNull(sBasketEvCost, 4),
s_basket_status: economicZone(sBasketEvCost),
s_mining_signal: s?.mining_signal ?? null,
s_economic_signal: s?.economic_signal ?? null,
s_final_signal: s?.final_signal ?? null,
s_single_expected_return_multiple: roundOrNull(sReturn, 6),
btc_equivalent_return_winner: compareNumber(mReturn, sReturn),
final_ev_winner: compareNumber(mEv, sBasketEv)
};
});
}

function buildComparisons(packages) {
const groups = {};
for (const pkg of packages) {
const family = packageFamily(pkg.name);
if (!family) continue;
if (!groups[family]) groups[family] = {};
groups[family][pkg.size] = pkg;
}

const result = [];
for (const [family, pair] of Object.entries(groups)) {
if (!pair.S || !pair.M) continue;
const s = pair.S;
const m = pair.M;
if (!s.price_btc || !m.price_btc) continue;

const sCount = Math.floor((m.price_btc + 1e-12) / s.price_btc);
if (sCount < 1) continue;

const sSingleP = s.primary_chain?.model_hit_probability;
const mP = m.primary_chain?.model_hit_probability;
const basketP = sSingleP !== null && sSingleP !== undefined ? 1 - Math.pow(1 - sSingleP, sCount) : null;

const sPrimaryReward = s.primary_chain?.expected_reward_native;
const mPrimaryReward = m.primary_chain?.expected_reward_native;
const sBasketPrimaryReward = sPrimaryReward !== null && sPrimaryReward !== undefined ? sPrimaryReward * sCount : null;

const sMergeReward = s.merge_chain?.expected_reward_native;
const mMergeReward = m.merge_chain?.expected_reward_native;
const sBasketMergeReward = sMergeReward !== null && sMergeReward !== undefined ? sMergeReward * sCount : null;

result.push({
family,
m_package: m.name,
m_price_btc: m.price_btc,
s_package: s.name,
s_count_for_m_budget: sCount,
s_total_price_btc: round(sCount * s.price_btc, 8),
m_hit_probability: mP ?? null,
m_hit_probability_percent: probabilityPercent(mP),
s_basket_hit_probability: basketP,
s_basket_hit_probability_percent: probabilityPercent(basketP),
probability_winner: compareProbability(mP, basketP),
primary_currency: m.primary_chain?.currency ?? null,
m_expected_primary_reward: mPrimaryReward ?? null,
s_basket_expected_primary_reward: sBasketPrimaryReward,
primary_expected_reward_winner: compareNumber(mPrimaryReward, sBasketPrimaryReward),
merge_currency: m.merge_chain?.currency ?? null,
m_expected_merge_reward: mMergeReward ?? null,
s_basket_expected_merge_reward: sBasketMergeReward,
merge_expected_reward_winner: m.merge_chain ? compareNumber(mMergeReward, sBasketMergeReward) : null,
final_ev_winner: "MARKET PRICE REQUIRED"
});
}
return result;
}

function compactBuyView(pkg) {
return {
name: pkg.name,
size: pkg.size,
currency_market: pkg.currency_market ?? "BTC",
price_native: pkg.price_native ?? pkg.price_btc,
price_btc: pkg.price_btc,
price_btc_equiv: pkg.price_btc_equiv ?? pkg.price_btc,
available: pkg.available,
duration_seconds: pkg.duration_seconds,
projected_speed: pkg.projected_speed,
projected_speed_unit: pkg.projected_speed_unit,
package_hashrate_hps: pkg.package_hashrate_hps,
nicehash_odds: pkg.nicehash_odds,
primary_chain: pkg.primary_chain,
merge_chain: pkg.merge_chain,
expected_rewards_native: pkg.expected_rewards_native,
economics: pkg.economics ?? null,
history_trend: pkg.history_trend ?? null,
mining_signal: pkg.mining_signal ?? "HISTORY WARMING UP",
profitability: pkg.profitability ?? null,
economic_signal: pkg.economic_signal ?? "UNKNOWN",
final_signal: pkg.final_signal ?? "WAIT",
decision: pkg.decision ?? null,
data_quality: pkg.data_quality,
math_validation: pkg.math_validation
};
}

function warningView(pkg) {
return {
name: pkg.name,
available: pkg.available,
data_quality: pkg.data_quality,
missing_fields: pkg.missing_fields,
math_validation: pkg.math_validation,
primary_sanity: pkg.primary_chain?.sanity_check ?? null,
merge_sanity: pkg.merge_chain?.sanity_check ?? null
};
}

function healthView(pkg) {
return {
name: pkg.name,
price_btc: pkg.price_btc,
odds: pkg.nicehash_odds.display,
odds_precision: pkg.nicehash_odds.denominator,
available: pkg.available,
data_quality: pkg.data_quality,
mining_math: pkg.math_validation.status,
model_expected_blocks: pkg.primary_chain?.expected_blocks ?? null,
model_probability_percent: pkg.primary_chain?.model_hit_probability_percent ?? null
};
}

function missingPackageFields(raw, multiplier) {
const missing = [];
const checks = [
["name", raw.name],
["price", raw.price],
["probabilityPrecision", raw.probabilityPrecision],
["projectedSpeed", raw.projectedSpeed],
["duration", raw.duration],
["currencyAlgo.currency", raw.currencyAlgo?.currency],
["currencyAlgo.miningAlgorithm", raw.currencyAlgo?.miningAlgorithm],
["currencyAlgo.networkHashpower", raw.currencyAlgo?.networkHashpower],
["currencyAlgo.networkDifficulty", raw.currencyAlgo?.networkDifficulty],
["currencyAlgo.blockReward", raw.currencyAlgo?.blockReward],
["currencyAlgo.blockTime", raw.currencyAlgo?.blockTime]
];

for (const [name, value] of checks) {
if (value === undefined || value === null || value === "") missing.push(name);
}

if (multiplier === null) missing.push("supported_hashrate_unit");

if (raw.mergeCurrencyAlgo) {
const mergeChecks = [
["mergeProbabilityPrecision", raw.mergeProbabilityPrecision],
["mergeCurrencyAlgo.currency", raw.mergeCurrencyAlgo.currency],
["mergeCurrencyAlgo.networkHashpower", raw.mergeCurrencyAlgo.networkHashpower],
["mergeCurrencyAlgo.blockReward", raw.mergeCurrencyAlgo.blockReward],
["mergeCurrencyAlgo.blockTime", raw.mergeCurrencyAlgo.blockTime]
];
for (const [name, value] of mergeChecks) {
if (value === undefined || value === null || value === "") missing.push(name);
}
}

return missing;
}

function isRadarPackage(name) {
if (!name) return false;
if (/team/i.test(name)) return false;
const normalized = String(name).trim();
return /\s(S|M)$/i.test(normalized) || /^(Silver|Gold)(?:\s+\d+)?$/i.test(normalized);
}

function packageSize(name) {
const normalized = String(name || "").trim();
const legacy = normalized.match(/\s(S|M)$/i);
if (legacy) return legacy[1].toUpperCase();
const usdt = normalized.match(/^(?:Silver|Gold)(?:\s+(\d+))?$/i);
return usdt ? (usdt[1] || "USDT") : null;
}

function packageFamily(name) {
return String(name || "").replace(/\s+(S|M)$/i, "").trim();
}

function denominatorToProbability(denominator) {
const d = numberOrNull(denominator);
if (d === null || d <= 0) return null;
return 1 / d;
}

function probabilityToDenominator(probability) {
if (probability === null || probability === undefined || probability <= 0) return null;
return 1 / probability;
}

function probabilityPercent(p) {
if (p === null || p === undefined) return null;
return round(p * 100, 6);
}

function oddsDisplay(value) {
const n = numberOrNull(value);
if (n === null) return null;
return `1:${round(n, 2)}`;
}

function probabilityOddsDisplay(probability) {
const denominator = probabilityToDenominator(probability);
if (denominator === null) return null;
return `1:${round(denominator, 2)}`;
}

function compareProbability(a, b) {
if (a === null || a === undefined || b === null || b === undefined) return "UNKNOWN";
const diff = Math.abs(a - b);
if (diff < 0.000001) return "TIE";
return a > b ? "M" : "S_BASKET";
}

function compareNumber(a, b) {
if (a === null || a === undefined || b === null || b === undefined) return "UNKNOWN";
const scale = Math.max(Math.abs(a), Math.abs(b), 1e-15);
if (Math.abs(a - b) / scale < 0.000001) return "TIE";
return a > b ? "M" : "S_BASKET";
}

function numberOrNull(value) {
if (value === undefined || value === null || value === "") return null;
const n = Number(value);
return Number.isFinite(n) ? n : null;
}

function finiteOrNull(value) {
return Number.isFinite(value) ? value : null;
}

function round(value, decimals = 8) {
if (!Number.isFinite(value)) return null;
const factor = 10 ** decimals;
return Math.round((value + Number.EPSILON) * factor) / factor;
}

function roundOrNull(value, digits = 4) {
return value === null || value === undefined || !Number.isFinite(value) ? null : round(value, digits);
}

function walk(value, callback) {
if (Array.isArray(value)) {
for (const item of value) walk(item, callback);
return;
}
if (value && typeof value === "object") {
callback(value);
for (const child of Object.values(value)) walk(child, callback);
}
}

function fail(error, extra = {}) {
return jsonResponse({
status: "TEST FAIL",
ok: false,
error,
...extra,
relay_version: RELAY_VERSION,
source_revision: SCHEDULED_SOURCE_REVISION,
checked_at: new Date().toISOString(),
schema_version: SCHEMA_VERSION
}, 502);
}

function jsonResponse(data, status = 200) {
return new Response(JSON.stringify(data, null, 2), {
status,
headers: {
"Content-Type": "application/json; charset=utf-8",
"Cache-Control": "no-store, no-cache, must-revalidate",
"Pragma": "no-cache",
"Expires": "0",
"Access-Control-Allow-Origin": "*",
"X-Relay-Version": RELAY_VERSION
}
});
}
async function listAllHistoryKeys(kv) {
  const keys = new Map();
  const cursors = new Set();
  let cursor;
  for (let page = 0; page < 64; page++) {
    const result = await kv.list({ prefix: 'snapshot:', limit: HISTORY_LIST_LIMIT, ...(cursor ? { cursor } : {}) });
    if (!result || !Array.isArray(result.keys) || typeof result.list_complete !== 'boolean') {
      throw new Error('HISTORY_INVALID_LIST_RESPONSE');
    }
    for (const item of result.keys) if (typeof item?.name === 'string') keys.set(item.name, item);
    if (result.list_complete) return { keys: [...keys.values()], list_complete: true, pages: page + 1 };
    if (typeof result.cursor !== 'string' || !result.cursor || cursors.has(result.cursor)) {
      throw new Error('HISTORY_INVALID_OR_REPEATED_CURSOR');
    }
    cursors.add(result.cursor);
    cursor = result.cursor;
  }
  throw new Error('HISTORY_INCOMPLETE_PAGE_BUDGET_EXCEEDED');
}


function recoverySignalReason(pkg, history) {
  if (!history?.ok) return 'HISTORY_UNAVAILABLE';
  if (pkg.mining_signal === 'HISTORY WARMING UP') return 'HISTORY_WARMING_UP';
  if (!pkg.profitability?.complete || pkg.economic_signal === 'UNKNOWN') return 'ECONOMICS_UNAVAILABLE';
  const uncapped = combineMiningAndProfitability(pkg.mining_signal, pkg.economic_signal);
  if (uncapped !== pkg.final_signal) return 'BREAK_EVEN_RISK_CAP';
  if (pkg.final_signal === 'STRONG BUY') return 'CONFIRMED_QUALITY_AND_POSITIVE_EV';
  if (pkg.final_signal === 'BUY NOW') return 'QUALITY_AND_PROFITABILITY_GATES_PASSED';
  if (pkg.final_signal === 'GOOD') return 'QUALITY_WITH_PROFITABILITY_CAP';
  if (pkg.economic_signal === 'POOR') return 'NEGATIVE_EV';
  if (pkg.mining_signal === 'NO BUY') return 'QUALITY_BELOW_BASELINE';
  return 'NO_QUALIFYING_QUALITY_EDGE';
}

// Port of scripts/apply_math_consistency_shadow.py v2; audit-only, never a signal override.
function mathPositive(v) {
  if (typeof v !== 'number' && typeof v !== 'string') return null;
  if (typeof v === 'string' && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(v.trim())) return null;
  const n = Number(v); return Number.isFinite(n) && n > 0 ? n : null;
}
function scheduledChainCheck(p, c) {
  const algorithm = String(c.algorithm || '').toUpperCase(), coin = String(c.currency || '').toUpperCase();
  const supported = {SCRYPT:['LTC','DOGE'], SHA256ASICBOOST:['BTC','BCH'], SHA256ASICBOOST_USDT:['BTC','BCH']};
  if (!supported[algorithm]?.includes(coin)) return {status:'NOT_APPLICABLE',algorithm:algorithm || null,reason:'Difficulty convention not verified for this coin/algorithm pair.'};
  const [h,t,d,supplied] = [p.package_hashrate_hps,p.duration_seconds,c.network_difficulty,c.expected_blocks].map(mathPositive);
  const TWO32 = 2 ** 32, factor = 2 ** 48 / 65535;
  const expected = h !== null && t !== null && d !== null ? h / d * (t / TWO32) : null;
  if (!Number.isFinite(expected) || expected <= 0 || supplied === null) return {status:'UNKNOWN',algorithm,reason:'Inputs must be finite and strictly positive.'};
  const difference = 100 * (supplied / expected - 1);
  if (!Number.isFinite(difference)) return {status:'UNKNOWN',algorithm,reason:'Numeric overflow.'};
  const result = {status:Math.abs(difference) <= 5 ? 'PASS' : Math.abs(difference) <= 15 ? 'WARNING' : 'CRITICAL',algorithm,
    feedExpectedBlocks:supplied,difficultyExpectedBlocks:expected,
    signedDifferencePercent:Number(difference.toFixed(6)),absoluteDifferencePercent:Number(Math.abs(difference).toFixed(6)),
    difficulty:d,formula:'hashrate_hps * duration_seconds / (difficulty * 2^32)',
    differenceKind:'MODEL_DISAGREEMENT_NOT_A_VERIFIED_DATA_ERROR',difficultyConvention:'CONDITIONAL_BDIFF_APPROXIMATION',
    difficultyConventionVerifiedForUpstreamField:false,bdiffRefinedExpectedBlocks:expected * TWO32 / factor,
    constantApproximationErrorPercent:(factor / TWO32 - 1) * 100,externalInputsIndependentlyVerified:false,
    decomposition:{status:'MISSING_NETWORK_INPUTS'}};
  const network=mathPositive(c.network_hashpower_hps), target=mathPositive(c.block_time_seconds);
  if (network !== null && target !== null) {
    const implied=d / network * TWO32, n=h / network * t / target, ratio=implied / target;
    if ([implied,n,ratio].every(v=>Number.isFinite(v)&&v>0)) {
      const residual=100*(supplied/n-1);
      result.decomposition={status:Math.abs(residual)<=.01?'EXPLAINED_BY_TARGET_TIME_MODEL':'FEED_FORMULA_NOT_RECONCILED',
        targetBlockTimeSeconds:target,impliedBlockTimeSeconds:implied,networkHashrateDerivedExpectedBlocks:n,
        feedVsNetworkFormulaPercent:residual,networkVsDifficultyMultiplier:ratio,
        identity:'lambda_H/lambda_D = (D*2^32/H_network)/T_target',impliedIntervalIsObservedBlockInterval:false,independentValidation:false};
    }
  }
  return result;
}
function annotateScheduledMath(feed) {
  const summary={PASS:0,WARNING:0,CRITICAL:0,UNKNOWN:0,NOT_APPLICABLE:0};
  for (const p of feed.packages) {
    const checks=[];
    for (const [key,label] of [['primary_chain','PRIMARY'],['merge_chain','MERGE']])
      if (record(p[key]) && Object.keys(p[key]).length) checks.push({...scheduledChainCheck(p,p[key]),chain:label});
    const status=['CRITICAL','WARNING','UNKNOWN','PASS'].find(s=>checks.some(c=>c.status===s)) || 'NOT_APPLICABLE';
    summary[status]++;
    p.math_consistency_shadow={model_version:2,status,production_override:false,checks,
      thresholds:{passMaxAbsoluteDifferencePercent:5,warningMaxAbsoluteDifferencePercent:15},
      policy:'Conditional consistency only, not proof of source truth or pricing edge. CURRENT and existing disagreement thresholds are unchanged.'};
  }
  feed.math_consistency_shadow={model_version:2,model_use:'MATH_CONSISTENCY_AUDIT_ONLY',production_model:'CURRENT',
    production_model_changed:false,supported_algorithms:['SCRYPT','SHA256ASICBOOST','SHA256ASICBOOST_USDT'],summary};
  return feed;
}

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
