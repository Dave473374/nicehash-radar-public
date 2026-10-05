// Recovery patch for the existing v2.9.0 history engine. No mining thresholds change.
// KV list() is ascending and can return an empty, incomplete page. Always follow cursor.
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

async function historyEndpoint(env) {
  if (!env?.RADAR_HISTORY) return jsonResponse({ status: 'HISTORY INVALID', ok: false, error: 'RADAR_HISTORY binding missing', checked_at: new Date().toISOString() });
  try {
    const listed = await listAllHistoryKeys(env.RADAR_HISTORY);
    return jsonResponse({ status: 'HISTORY LIST OK', ok: true, relay_version: RELAY_VERSION,
      binding: 'RADAR_HISTORY', kv_read_write_test: 'NOT_RUN_READ_ONLY_DIAGNOSTIC',
      snapshot_count_listed: listed.keys.length, list_complete: true, list_pages: listed.pages,
      recent_snapshot_keys: listed.keys.map(x => x.name).sort().reverse().slice(0, 20),
      checked_at: new Date().toISOString(), schema_version: SCHEMA_VERSION });
  } catch (err) {
    return jsonResponse({ status: 'HISTORY INVALID', ok: false, relay_version: RELAY_VERSION,
      error: String(err?.message || err), checked_at: new Date().toISOString(), schema_version: SCHEMA_VERSION });
  }
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
