"""Deterministically patch the archived v2.9.0 Worker; never contact a remote service."""
from pathlib import Path
import gzip, hashlib, re, json, os
ROOT = Path(__file__).resolve().parent
SOURCE_SHA256 = 'f150fdcca108302f84362224dec88dafa25e78ec9f868bca25165a5a2d41be57'

def replace_function(source, name, replacement):
    pattern = rf'^(?:async )?function {re.escape(name)}\([^\n]*\).*?(?=^(?:async )?function |\Z)'
    result, count = re.subn(pattern, lambda _: replacement.rstrip() + '\n\n', source, count=1, flags=re.M | re.S)
    if count != 1:
        raise ValueError(f'Expected exactly one function {name}')
    return result

def build():
    raw = gzip.decompress((ROOT / 'base-worker-v2.9.0.js.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('Source archive hash mismatch: refusing to guess another Worker version')
    source = raw.decode()
    fixes = (ROOT / 'history-functions.js').read_text()
    for name in ('buildHistoryAnalysis', 'historyEndpoint'):
        match = re.search(rf'^(?:async )?function {name}\([^\n]*\).*?(?=^(?:async )?function |\Z)', fixes, re.M | re.S)
        if not match: raise ValueError(name)
        source = replace_function(source, name, match.group())
    helpers = []
    for name in ('listAllHistoryKeys', 'recoverySignalReason'):
        match = re.search(rf'^(?:async )?function {name}\([^\n]*\).*?(?=^(?:async )?function |\Z)', fixes, re.M | re.S)
        if not match: raise ValueError(name)
        helpers.append(match.group())
    source += '\n' + '\n'.join(helpers)
    replacements = {
        'const RELAY_VERSION = "2.9.0";': 'const RELAY_VERSION = "2.9.0-history-recovery.1";',
        'const economicsOk = marketOk && economicPackages.every': 'const economicsOk = marketOk && economicPackages.length > 0 && economicPackages.every',
        'const key = `snapshot:${String(timestamp).padStart(13, "0")}`;': 'const key = `snapshot:${String(Math.floor(timestamp / 3600000) * 3600000).padStart(13, "0")}`;',
        'status: economicsOk ? "BUY FEED OK" : "BUY FEED WARNING",\nok: economicsOk,': 'status: economicsOk && (shadowNoKv || (historyAnalysis.ok && historyWrite.ok)) ? "BUY FEED OK" : "BUY FEED WARNING",\nok: economicsOk && (shadowNoKv || (historyAnalysis.ok && historyWrite.ok)),',
        'history_status: historyWrite.status,': 'history_status: shadowNoKv ? "HISTORY BYPASSED" : historyAnalysis.status,\nhistory_write_status: historyWrite.status,\nhistory_diagnostics: historyAnalysis.diagnostics ?? null,\nproduction_health: { state: shadowNoKv ? "SHADOW_ONLY" : !(economicsOk && historyAnalysis.ok && historyWrite.ok) ? "DEGRADED" : ["24H READY", "7D READY"].includes(historyAnalysis.status) ? "READY" : "WARMING_UP", signal_engine_ready: !shadowNoKv && economicsOk && historyAnalysis.ok && historyWrite.ok && ["24H READY", "7D READY"].includes(historyAnalysis.status), history_write_ok: historyWrite.ok, revision: "history-recovery.1" },',
        'history_error: historyWrite.error ?? historyAnalysis.error ?? null,': 'history_error: historyAnalysis.error ?? historyWrite.error ?? null,',
        'packages: packagesWithDecision.map(compactBuyView),': 'packages: packagesWithDecision.map(pkg => ({ ...compactBuyView(pkg), final_signal_reason: recoverySignalReason(pkg, historyAnalysis) })),',
    }
    for old, new in replacements.items():
        if source.count(old) != 1: raise ValueError(f'Unsafe replacement count {source.count(old)}: {old[:80]}')
        source = source.replace(old, new, 1)
    revision = os.environ.get('GITHUB_SHA', 'LOCAL_UNCOMMITTED')
    source = source.replace('relay_version: RELAY_VERSION,', 'relay_version: RELAY_VERSION,\nsource_revision: ' + json.dumps(revision) + ',')
    (ROOT / 'base-worker-v2.9.0.js').write_bytes(raw)
    production_source = source
    (ROOT / 'worker.js').write_text(production_source)
    # Only the test module exports internal functions. Deploy worker.js, not worker.mjs.
    source += '\nexport { buildHistoryAnalysis, storeHistorySnapshot, listAllHistoryKeys, analysePackageHistory, combineMiningAndProfitability, recoverySignalReason, attachProfitabilityDecision };\n'
    output = ROOT / 'worker.mjs'
    output.write_text(source)
    manifest = {'base_sha256': SOURCE_SHA256, 'worker_sha256': hashlib.sha256(production_source.encode()).hexdigest(), 'base_provenance': 'User Library: worker_v2.9.0_no_kv_shadow.js, generated 2026-09-29; exact equality to deployed bytes NOT verified', 'deployment_status': 'NOT_DEPLOYED_BY_BUILDER', 'signal_thresholds_changed': False, 'source_revision': revision}
    (ROOT / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))
    return output

if __name__ == '__main__':
    build()
