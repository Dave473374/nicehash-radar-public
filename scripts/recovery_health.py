"""Validate BUY Radar decision readiness, not just HTTP/JSON success.
Does not place orders, change thresholds or create higher signals.
"""
import argparse
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ACTIONABLE = {'GOOD', 'BUY NOW', 'STRONG BUY'}

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError:
        return None

def assess(feed, now=None):
    now = now or datetime.now(timezone.utc)
    problems = []
    if not isinstance(feed, dict):
        return {'state': 'DEGRADED', 'signal_engine_ready': False, 'reasons': ['INVALID_FEED_OBJECT']}
    checked = timestamp(feed.get('checked_at'))
    age = (now - checked).total_seconds() if checked else None
    if age is None or age < 0 or age > 420:
        problems.append('INVALID_OR_STALE_TIMESTAMP')
    if feed.get('ok') is not True or feed.get('status') != 'BUY FEED OK':
        problems.append('UPSTREAM_NOT_HEALTHY')
    if feed.get('shadow_sampling_mode') == 'NO_KV':
        problems.append('SHADOW_NOT_PRODUCTION')
    hourly = feed.get('history_hourly_samples')
    coverage = feed.get('history_coverage_hours')
    loaded = feed.get('history_samples_loaded')
    if not finite(hourly) or not finite(coverage) or hourly < 1 or coverage <= 0:
        problems.append('HISTORY_UNAVAILABLE')
    if finite(loaded) and loaded > 20 and (not finite(hourly) or hourly == 0):
        problems.append('HISTORY_LISTED_BUT_NOT_USABLE')
    if feed.get('history_saved') is False:
        problems.append('HISTORY_WRITE_FAILED')
    diagnostics = feed.get('history_diagnostics') or {}
    if diagnostics.get('list_complete') is False:
        problems.append('HISTORY_INCOMPLETE_LIST')
    packages = feed.get('packages')
    if not isinstance(packages, list) or not packages:
        problems.append('NO_PACKAGES')
        packages = []
    seen = set()
    ready = 0
    for p in packages:
        if not isinstance(p, dict):
            problems.append('INVALID_PACKAGE_OBJECT')
            continue
        ident = (p.get('name'), p.get('currency_market'))
        if ident in seen:
            problems.append('DUPLICATE_PACKAGE')
        seen.add(ident)
        if p.get('available') is not True:
            continue
        name = p.get('name') or 'UNKNOWN'
        economics = p.get('economics') or {}
        profit = p.get('profitability') or {}
        price = economics.get('package_cost_eur')
        if (profit.get('complete') is not True or economics.get('complete') is not True or
                not finite(price) or price <= 0 or not finite(profit.get('expected_return_percent'))):
            problems.append(name + ':ECONOMICS_UNAVAILABLE')
        h = p.get('history_trend') or {}
        q24 = h.get('expected_blocks_per_btc_vs_24h_percent')
        q7 = h.get('expected_blocks_per_btc_vs_7d_percent')
        n24, c24 = h.get('sample_count_24h'), h.get('coverage_hours_24h')
        if not (finite(n24) and n24 >= 18 and finite(c24) and c24 >= 18 and finite(q24)):
            problems.append(name + ':HISTORY_24H_NOT_READY')
        elif h.get('quality_signal') in {None, 'UNKNOWN', 'HISTORY WARMING UP'}:
            problems.append(name + ':QUALITY_NOT_READY')
        else:
            ready += 1
        if h.get('trend_status') == '7D READY' and not finite(q7):
            problems.append(name + ':INVALID_7D_BASELINE')
    if not ready:
        problems.append('NO_READY_PACKAGE')
    return {'state': 'DEGRADED' if problems else 'READY',
            'signal_engine_ready': not problems, 'reasons': sorted(set(problems)),
            'checked_at': now.isoformat(), 'source_checked_at': feed.get('checked_at'),
            'age_seconds': age, 'packages_checked': len(packages), 'ready_packages': ready,
            'revision': 'history-recovery.1', 'automatic_purchase': False}

def annotate(feed, now=None):
    out = copy.deepcopy(feed)
    report = assess(feed, now)
    out['production_health'] = report
    if not report['signal_engine_ready']:
        out['upstream_feed_status'] = feed.get('status')
        out['status'], out['ok'] = 'BUY FEED WARNING', False
        if any(reason.startswith('HISTORY') for reason in report['reasons']):
            out['history_write_status'] = feed.get('history_write_status', feed.get('history_status'))
            out['history_status'] = 'HISTORY INVALID'
        for package in out.get('packages', []):
            if not isinstance(package, dict):
                continue
            if package.get('final_signal') in ACTIONABLE:
                package['upstream_final_signal'] = package['final_signal']
                package['final_signal'] = 'WAIT'
            package['final_signal_reason'] = 'DATA_QUALITY_UNAVAILABLE'
    return out, report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feed', type=Path)
    parser.add_argument('--annotate', action='store_true')
    args = parser.parse_args()
    feed = json.loads(args.feed.read_text())
    result, report = annotate(feed)
    if args.annotate:
        args.feed.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if args.annotate or report['signal_engine_ready'] else 2

if __name__ == '__main__':
    raise SystemExit(main())
