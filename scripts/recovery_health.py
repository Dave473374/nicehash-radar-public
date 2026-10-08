"""Validate acquisition, decision readiness and non-actionable diagnostic transport separately."""
import argparse
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ACTIONABLE = {'GOOD', 'BUY NOW', 'STRONG BUY'}
DIAGNOSTIC_STATES = {'DEGRADED', 'WARMING_UP', 'NOT_STARTED'}

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def timestamp(value):
    if not isinstance(value, str): return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError: return None

def obj(value):
    return value if isinstance(value, dict) else {}

def diagnostic(feed):
    h = obj(feed.get('production_health'))
    return (feed.get('status') == 'BUY FEED WARNING' and feed.get('ok') is False and
            h.get('signal_engine_ready') is False and h.get('state') in DIAGNOSTIC_STATES and
            isinstance(h.get('reasons'), list) and bool(h['reasons']) and
            all(isinstance(r, str) and r for r in h['reasons']))

def validate_transport(feed, now=None, max_age=420):
    """A known warning may be mirrored, but can never be accepted for a BUY."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(feed, dict) or feed.get('schema_version') != 12:
        raise ValueError('Unsupported or malformed BUY feed schema')
    ps = feed.get('packages')
    if not isinstance(ps, list) or any(not isinstance(p, dict) for p in ps):
        raise ValueError('Invalid package list')
    is_diagnostic = diagnostic(feed)
    checked = timestamp(feed.get('checked_at'))
    if checked is None:
        # No fabricated timestamp or price before the very first scheduled run.
        if is_diagnostic and not ps and feed.get('checked_at') is None: return 'DIAGNOSTIC_ONLY'
        raise ValueError('Missing or invalid source time')
    age = (now - checked).total_seconds()
    if age < 0: raise ValueError('Future source time')
    if is_diagnostic: return 'DIAGNOSTIC_ONLY' # old quote time remains visibly old
    if age > max_age: raise ValueError('STALE BUY FEED')
    if feed.get('status') != 'BUY FEED OK' or feed.get('ok') is not True:
        raise ValueError('Unrecognized unhealthy feed')
    if not ps or not any(p.get('available') is True for p in ps):
        raise ValueError('No available package')
    return 'FRESH_DATA'

def assess(feed, now=None):
    now = now or datetime.now(timezone.utc)
    errors, warming = [], []
    if not isinstance(feed, dict):
        return {'state':'DEGRADED','signal_engine_ready':False,'reasons':['INVALID_FEED_OBJECT']}
    checked = timestamp(feed.get('checked_at'))
    age = (now - checked).total_seconds() if checked else None
    if age is None or age < 0 or age > 420: errors.append('INVALID_OR_STALE_TIMESTAMP')
    known_warm = diagnostic(feed) and obj(feed.get('production_health')).get('state') == 'WARMING_UP'
    if (feed.get('ok') is not True or feed.get('status') != 'BUY FEED OK') and not known_warm:
        errors.append('UPSTREAM_NOT_HEALTHY')
    if feed.get('shadow_sampling_mode') == 'NO_KV': errors.append('SHADOW_NOT_PRODUCTION')
    hourly, coverage, loaded = (feed.get(k) for k in ('history_hourly_samples','history_coverage_hours','history_samples_loaded'))
    if not finite(hourly) or not finite(coverage) or hourly < 0 or coverage < 0:
        errors.append('HISTORY_UNAVAILABLE')
    elif hourly < 2 or coverage == 0: warming.append('HISTORY_WARMING_UP')
    if finite(loaded) and loaded > 20 and (not finite(hourly) or hourly == 0):
        errors.append('HISTORY_LISTED_BUT_NOT_USABLE')
    if feed.get('history_saved') is False: errors.append('HISTORY_WRITE_FAILED')
    if obj(feed.get('history_diagnostics')).get('list_complete') is False: errors.append('HISTORY_INCOMPLETE_LIST')
    ps = feed.get('packages')
    if not isinstance(ps, list) or not ps:
        errors.append('NO_PACKAGES'); ps=[]
    seen, ready = set(), 0
    for p in ps:
        if not isinstance(p, dict): errors.append('INVALID_PACKAGE_OBJECT'); continue
        name = p.get('name') if isinstance(p.get('name'),str) else 'UNKNOWN'
        identity = json.dumps([p.get('name'),p.get('currency_market')],sort_keys=True)
        if identity in seen: errors.append('DUPLICATE_PACKAGE')
        seen.add(identity)
        if p.get('available') is not True: continue
        e,pr,h = (obj(p.get(k)) for k in ('economics','profitability','history_trend'))
        price=e.get('package_cost_eur')
        if (pr.get('complete') is not True or e.get('complete') is not True or
                not finite(price) or price <= 0 or not finite(pr.get('expected_return_percent'))):
            errors.append(name+':ECONOMICS_UNAVAILABLE')
        n24,c24,q24 = (h.get(k) for k in ('sample_count_24h','coverage_hours_24h','expected_blocks_per_btc_vs_24h_percent'))
        if not (finite(n24) and n24 >= 18 and finite(c24) and c24 >= 18 and finite(q24)):
            warming.append(name+':HISTORY_24H_NOT_READY')
        elif h.get('quality_signal') not in {'WAIT','NO BUY','GOOD','BUY NOW','STRONG BUY'}:
            errors.append(name+':QUALITY_NOT_READY')
        else: ready+=1
        if h.get('trend_status') == '7D READY' and not finite(h.get('expected_blocks_per_btc_vs_7d_percent')):
            errors.append(name+':INVALID_7D_BASELINE')
    if not ready: warming.append('NO_READY_PACKAGE')
    upstream_health=obj(feed.get('production_health'))
    if upstream_health.get('signal_engine_ready') is False and upstream_health.get('state') not in {'WARMING_UP'}:
        errors.append('UPSTREAM_DECISION_GATE_NOT_READY')
    reasons=sorted(set(errors+warming))
    return {'state':'DEGRADED' if errors else 'WARMING_UP' if warming else 'READY',
            'signal_engine_ready':not reasons,'reasons':reasons,'checked_at':now.isoformat(),
            'source_checked_at':feed.get('checked_at'),'age_seconds':age,
            'packages_checked':len(ps),'ready_packages':ready,'revision':'scheduler-recovery.2','automatic_purchase':False}

def annotate(feed, now=None):
    out=copy.deepcopy(feed); report=assess(feed,now)
    if 'production_health' in out: out['upstream_production_health']=out['production_health']
    out['production_health']=report
    if not report['signal_engine_ready']:
        out['upstream_feed_status']=feed.get('status')
        out['status'],out['ok']='BUY FEED WARNING',False
        if report['state']=='WARMING_UP': out['history_status']='HISTORY WARMING UP'
        elif any(r.startswith('HISTORY') for r in report['reasons']):
            out['history_write_status']=feed.get('history_write_status',feed.get('history_status'))
            out['history_status']='HISTORY INVALID'
        for p in out.get('packages',[]):
            if not isinstance(p,dict): continue
            if p.get('final_signal') in ACTIONABLE:
                p['upstream_final_signal']=p['final_signal'];p['final_signal']='WAIT'
            p['final_signal_reason']='HISTORY_WARMING_UP' if report['state']=='WARMING_UP' else 'DATA_QUALITY_UNAVAILABLE'
    return out,report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feed',type=Path);parser.add_argument('--annotate',action='store_true');parser.add_argument('--transport-only',action='store_true')
    args=parser.parse_args();feed=json.loads(args.feed.read_text())
    if args.transport_only:
        print('BUY FEED TRANSPORT:',validate_transport(feed));return 0
    result,report=annotate(feed)
    if args.annotate: args.feed.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2,allow_nan=False))
    return 0 if args.annotate or report['signal_engine_ready'] else 2
if __name__=='__main__': raise SystemExit(main())
