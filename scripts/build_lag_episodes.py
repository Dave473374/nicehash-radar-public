"""Public-file-only, locked-rule pricing-lag episodes and time-separated quote evaluation."""
from __future__ import annotations
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from build_market_edge_research import timestamp, numeric, json_lines, digest

PACKAGES = {'Palladium S': ('BTC', 'LTC', 'SCRYPT'), 'Silver S': ('BTC', 'BCH', 'SHA256ASICBOOST'),
            'Silver 5': ('USDT', 'BCH', 'SHA256ASICBOOST_USDT'), 'Silver 20': ('USDT', 'BCH', 'SHA256ASICBOOST_USDT')}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def protocol_hash(protocol):
    return hashlib.sha256(canonical(protocol).encode()).hexdigest()


def validate_protocol(p):
    expected = {'version': 1, 'lookbackMinSeconds': 300, 'lookbackMaxSeconds': 900,
                'marketRiseMinPercent': 5.0, 'ticketCostRiseMaxPercent': 1.0,
                'relativeDivergenceMinPercent': 5.0, 'releaseDivergencePercent': 2.0,
                'maxEpisodeMinutes': 30, 'cooldownMinutes': 60, 'horizonsMinutes': [15, 30],
                'labelToleranceMinutes': 5, 'referenceDays': 7, 'embargoMinutes': 60,
                'maxMarketAgeSeconds': 600, 'maxQuoteDelaySeconds': 900}
    if any(p.get(k) != v or isinstance(p.get(k), bool) for k, v in expected.items()):
        raise ValueError('Protocol v1 is locked; revise explicitly, never tune it silently')
    start, locked = timestamp(p.get('validationStart')), timestamp(p.get('lockedAt'))
    if start is None or locked is None or locked >= start or start.hour or start.minute or start.second:
        raise ValueError('Protocol needs a future UTC-day holdout after registration')
    if start.utcoffset() != timedelta(0) or p.get('packages') != list(PACKAGES):
        raise ValueError('Protocol package set or UTC boundary changed')
    return p


def contracts(rows, now):
    out, conflicts = {}, set()
    for row in rows:
        at = timestamp(row.get('collected_at'))
        if at is None or at > now or row.get('source') != 'NICEHASH_PUBLIC_MARKET':
            continue
        if any(row.get(k) is not False for k in ('credentials_used', 'private_api_used', 'admin_api_used')):
            continue
        algos = row.get('algorithms')
        if not isinstance(algos, dict):
            continue
        for algo, data in algos.items():
            if not isinstance(data, dict):
                continue
            unit = data.get('unitContract') or {}
            if not isinstance(unit, dict):
                unit = {}
            valid = (unit.get('status') == 'DISPLAY_UNITS_CONFIRMED_RAW_PRICE_UNVERIFIED'
                     and timestamp(unit.get('observedAt')) == at
                     and unit.get('source') == 'PUBLIC_MINING_ALGORITHMS'
                     and unit.get('speedDisplayUnit') == data.get('speedUnit')
                     and numeric(unit.get('marketFactor'), True) is not None
                     and numeric(unit.get('priceFactor'), True) is not None
                     and isinstance(unit.get('priceDisplayUnit'), str))
            value = {'valid': valid, 'price': numeric(data.get('priceRaw'), True),
                     'currency': unit.get('currencyMarket'),
                     'signature': [unit.get(k) for k in ('currencyMarket', 'marketFactor', 'priceFactor',
                                                        'speedDisplayUnit', 'priceDisplayUnit', 'priceScale')]}
            key = (algo, at)
            if key in conflicts:
                continue
            if key in out and out[key] != value:
                conflicts.add(key); out[key] = {'valid': False}
            else:
                out[key] = value
    return out


def prepare(rows, metadata, p, now, counts):
    groups, seen, conflicts = defaultdict(list), {}, set()
    for row in rows:
        name = row.get('package')
        if name not in PACKAGES:
            continue
        at = timestamp(row.get('quoteAt'))
        observed = timestamp(row.get('observedAt'))
        if at is None or at > now:
            counts['invalidQuoteTime'] += 1; continue
        key = (name, at)
        if key in conflicts:
            continue
        if key in seen:
            if canonical(seen[key]) != canonical(row):
                conflicts.add(key)
            else:
                counts['duplicatePoints'] += 1
            continue
        seen[key] = row
    for (name, at), raw in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        row = dict(raw)
        reason = None
        observed, mt = timestamp(row.get('observedAt')), timestamp(row.get('marketAt'))
        wanted = PACKAGES[name]
        if (name, at) in conflicts:
            reason = 'CONFLICTING_QUOTE'
        elif (row.get('currency'), row.get('coin'), row.get('marketAlgorithm')) != wanted:
            reason = 'SERIES_MISMATCH'
        elif observed is None or not at <= observed <= now or (observed-at).total_seconds() > p['maxQuoteDelaySeconds']:
            reason = 'LATE_OR_UNKNOWN_AVAILABILITY'
        elif row.get('pairStatus') != 'PAIRED' or mt is None or not 0 <= (at-mt).total_seconds() <= p['maxMarketAgeSeconds']:
            reason = 'NO_VALID_MARKET_PAIR'
        elif any(numeric(row.get(k), True) is None for k in ('workPerNative', 'marketPriceRaw', 'priceNative', 'durationSeconds', 'hashrateHps')):
            reason = 'INVALID_NUMERIC_INPUT'
        elif not isinstance(row.get('relayVersion'), str) or not row['relayVersion']:
            reason = 'NO_VERSION'
        contract = metadata.get((wanted[2], mt)) if mt else None
        if reason is None and (not contract or not contract.get('valid') or contract.get('currency') != wanted[0]):
            reason = 'UNVERIFIED_DISPLAY_CONTRACT'
        if reason is None and (contract['price'] is None or not math.isclose(contract['price'], float(row['marketPriceRaw']), rel_tol=1e-12)):
            reason = 'MARKET_PRICE_CONFLICT'
        row['_at'], row['_observed'], row['_valid'] = at, observed, reason is None
        row['_signature'] = [name, *wanted, row.get('mergeCoin'), row.get('relayVersion'),
                             row.get('marketUnitSignature'), contract.get('signature') if contract else None]
        if reason:
            counts[reason] += 1
        else:
            counts['eligiblePoints'] += 1
        groups[name].append(row)
    return groups


def compatible(a, b, max_gap):
    gap = (b['_at']-a['_at']).total_seconds()
    return (a['_valid'] and b['_valid'] and a['_signature'] == b['_signature']
            and 0 < gap <= max_gap and a['_observed'] <= b['_at'])


def features(a, b, p):
    gap = (b['_at']-a['_at']).total_seconds()
    if not compatible(a, b, p['lookbackMaxSeconds']) or gap < p['lookbackMinSeconds'] or a.get('marketAt') == b.get('marketAt'):
        return None
    market = float(b['marketPriceRaw'])/float(a['marketPriceRaw'])
    work = float(b['workPerNative'])/float(a['workPerNative'])
    return {'lookbackSeconds': gap, 'marketMovePercent': (market-1)*100,
            'ticketCostPerWorkMovePercent': (1/work-1)*100,
            'workPerNativeMovePercent': (work-1)*100, 'divergencePercent': (market*work-1)*100}


def candidate(f, p):
    return (f['marketMovePercent'] >= p['marketRiseMinPercent']
            and f['ticketCostPerWorkMovePercent'] <= p['ticketCostRiseMaxPercent']
            and f['divergencePercent'] >= p['relativeDivergenceMinPercent'])


def forward_label(points, i, horizon, p, now):
    entry = points[i]; target = entry['_at']+timedelta(minutes=horizon)
    deadline = target+timedelta(minutes=p['labelToleranceMinutes'])
    blank = {'horizonMinutes': horizon, 'role': 'FUTURE_QUOTE_LABEL_NOT_RETURN_OR_HIT',
             'status': 'PENDING' if now < deadline else 'MISSING', 'futureWorkChangePercent': None,
             'catchUpPercentOfInitialGap': None}
    times = [q['_at'] for q in points]
    j = bisect_left(times, target)
    if j >= len(points) or times[j] > deadline:
        return blank
    # Do not skip an intervening invalid row or bridge an observation gap.
    if any(not compatible(a,b,p['lookbackMaxSeconds']) for a,b in zip(points[i:j], points[i+1:j+1])):
        return {**blank, 'status': 'CENSORED'}
    future = points[j]
    ratio = float(future['workPerNative'])/float(entry['workPerNative'])
    return {**blank, 'status': 'OBSERVED', 'quoteAt': future['_at'].isoformat(),
            'availableAt': future['_observed'].isoformat(),
            'futureWorkChangePercent': (ratio-1)*100,
            'ticketCostPerWorkChangePercent': (1/ratio-1)*100,
            'marketChangePercent': (float(future['marketPriceRaw'])/float(entry['marketPriceRaw'])-1)*100}


def lifetime(points, i, base, p, now):
    entry = points[i]; previous = entry
    result = {'status': 'OPEN', 'lastObservedAt': entry['quoteAt'], 'observedDurationMinutes': 0.0,
              'closureCause': None, 'peakDivergencePercent': (float(entry['marketPriceRaw'])/float(base['marketPriceRaw'])*float(entry['workPerNative'])/float(base['workPerNative'])-1)*100}
    for q in points[i+1:]:
        if not compatible(previous,q,p['lookbackMaxSeconds']):
            return {**result, 'status': 'CENSORED_GAP_OR_SERIES'}
        elapsed = (q['_at']-entry['_at']).total_seconds()/60
        if elapsed > p['maxEpisodeMinutes']:
            return {**result, 'status': 'WINDOW_ENDED'}
        level = (float(q['marketPriceRaw'])/float(base['marketPriceRaw'])*float(q['workPerNative'])/float(base['workPerNative'])-1)*100
        result.update(lastObservedAt=q['quoteAt'], observedDurationMinutes=elapsed,
                      peakDivergencePercent=max(result['peakDivergencePercent'],level))
        if level <= p['releaseDivergencePercent']:
            repriced = float(entry['workPerNative'])/float(q['workPerNative']) > 1.01
            reversed_market = float(q['marketPriceRaw'])/float(entry['marketPriceRaw']) < .99
            cause = 'BOTH' if repriced and reversed_market else 'TICKET_REPRICED' if repriced else 'MARKET_REVERSED' if reversed_market else 'OTHER'
            return {**result, 'status': 'RESOLVED', 'closureCause': cause}
        previous = q
    if result['observedDurationMinutes'] >= p['maxEpisodeMinutes']:
        result['status'] = 'WINDOW_ENDED'
    elif (now-previous['_at']).total_seconds() > p['lookbackMaxSeconds']:
        result['status'] = 'CENSORED_GAP_OR_SERIES'
    return result


def collect_episodes(groups, p, now):
    records, controls = [], []
    ph = protocol_hash(p); holdout = timestamp(p['validationStart'])
    for name, points in sorted(groups.items()):
        last_episode, last_control = None, None
        for i in range(1,len(points)):
            a,b = points[i-1],points[i]; f = features(a,b,p)
            if f is None:
                continue
            is_candidate = candidate(f,p)
            last = last_episode if is_candidate else last_control
            if last and (b['_at']-last).total_seconds() < p['cooldownMinutes']*60:
                continue
            entry = {'package': name, 'currency': b['currency'], 'coin': b['coin'], 'quoteAt': b['quoteAt'],
                     'availableAt': b['observedAt'], 'baselineQuoteAt': a['quoteAt'], 'marketAt': b['marketAt'],
                     'signature': b['_signature'], 'workPerNative': float(b['workPerNative']),
                     'mathStatus': b.get('mathStatus'), 'currentSignal': b.get('currentSignal'),
                     'feedExpectedReturnPercent': b.get('feedExpectedReturnPercent'), 'features': f}
            labels = [forward_label(points,i,h,p,now) for h in p['horizonsMinutes']]
            for label in labels:
                if label['status'] == 'OBSERVED' and is_candidate:
                    # Catch-up is a quote-change diagnostic, not a refund or realized profit.
                    gap = math.log1p(f['divergencePercent']/100)
                    label['catchUpPercentOfInitialGap'] = 100*math.log1p(label['ticketCostPerWorkChangePercent']/100)/gap if gap else None
            record = {'id': hashlib.sha256(f"{ph}|{name}|{b['currency']}|{b['quoteAt']}".encode()).hexdigest(),
                      'protocolHash': ph, 'entry': entry,
                      'cohort': 'HOLDOUT_REPLAY' if b['_at'] >= holdout else 'EXPLORATORY',
                      'labels': labels, 'canRaiseSignal': False}
            if is_candidate:
                record['episode'] = lifetime(points,i,a,p,now)
                records.append(record); last_episode=b['_at']
            else:
                controls.append(record); last_control=b['_at']
    return records, controls


def merge_ledger(previous, new, now):
    """Freeze entry-time features. Recompute only outcomes; retain aged-out evidence."""
    out = {r['id']: dict(r) for r in previous}
    for r in new:
        old = out.get(r['id'])
        if old:
            if canonical(old['entry']) != canonical(r['entry']):
                out[r['id']] = {**old, 'sourceRevision': True}
                continue
            prior_labels = {v['horizonMinutes']: v for v in old.get('labels', [])}
            revised = old.get('sourceRevision', False)
            for i, label in enumerate(r['labels']):
                prior = prior_labels.get(label['horizonMinutes'])
                if prior and prior.get('status') == 'OBSERVED':
                    if label.get('status') == 'OBSERVED' and canonical(prior) != canonical(label):
                        revised = True
                    r['labels'][i] = prior
            r = {**r, 'firstRecordedAt': old.get('firstRecordedAt'), 'sourceRevision': revised}
        else:
            r = {**r, 'firstRecordedAt': now.isoformat(), 'sourceRevision': False}
        out[r['id']] = r
    return sorted(out.values(),key=lambda r:(r['entry']['quoteAt'],r['entry']['package']))


def observed(records, horizon, p_hash, start, end, available_by):
    values=[]
    for r in records:
        at=timestamp(r['entry']['quoteAt'])
        if r.get('sourceRevision') or r.get('protocolHash') != p_hash or at is None or not start <= at < end:
            continue
        for label in r.get('labels',[]):
            available=timestamp(label.get('availableAt'))
            if (label.get('horizonMinutes')==horizon and label.get('status')=='OBSERVED'
                and available is not None and available <= available_by):
                value=label.get('futureWorkChangePercent')
                if isinstance(value,(int,float)) and math.isfinite(value):
                    values.append(value)
    return values


def walk_forward(records, controls, p, now):
    start=timestamp(p['validationStart']); ph=protocol_hash(p); folds=[]
    day=start
    while day <= now:
        end=day+timedelta(days=1); cutoff=day-timedelta(minutes=p['embargoMinutes'])
        for name in p['packages']:
            rs=[r for r in records if r['entry']['package']==name]
            cs=[r for r in controls if r['entry']['package']==name]
            for horizon in p['horizonsMinutes']:
                reference=observed(cs,horizon,ph,day-timedelta(days=p['referenceDays']),cutoff,cutoff)
                values=observed(rs,horizon,ph,day,min(end,now+timedelta(microseconds=1)),now)
                control_values=observed(cs,horizon,ph,day,min(end,now+timedelta(microseconds=1)),now)
                # No optimized model or significance claim: expanding history, fixed rule, daily forward folds.
                folds.append({'package':name,'testDay':day.date().isoformat(),'horizonMinutes':horizon,
                    'referenceEndsAt':cutoff.isoformat(), 'referenceControlCount':len(reference),
                    'testEpisodeCount':len(values),'testControlCount':len(control_values),
                    'referenceMedianFutureWorkChangePercent':median(reference) if reference else None,
                    'testMedianFutureWorkChangePercent':median(values) if values else None,
                    'testControlMedianFutureWorkChangePercent':median(control_values) if control_values else None,
                    'status':'DESCRIPTIVE_ONLY' if reference and values else 'INSUFFICIENT_LABELS'})
        day=end
    return {'status':'NOT_STARTED' if now < start else 'FORWARD_QUOTE_EVALUATION_ONLY',
            'validationStart':p['validationStart'],'folds':folds,'thresholdsFitted':False,
            'automaticallyPromotesSignal':False,'verdict':'NO_VERIFIED_EDGE'}


def run(pairs, market_rows, protocol, previous, now):
    p=validate_protocol(protocol); counts=Counter()
    groups=prepare(pairs,contracts(market_rows,now),p,now,counts)
    episodes,controls=collect_episodes(groups,p,now)
    ledger=merge_ledger(previous,episodes,now)
    report={'schemaVersion':1,'generatedAt':now.isoformat(),'protocolHash':protocol_hash(p),
        'role':'LAG_EPISODE_QUOTE_RESEARCH_ONLY','currentProductionModelChanged':False,
        'automaticPurchase':False,'automaticCancel':False,'privateApiUsed':False,
        'networkRequestsMade':0,'canRaiseSignal':False,'verdict':'NO_VERIFIED_EDGE',
        'counts':dict(counts),'episodeCount':len(ledger),'currentWindowEpisodes':len(episodes),
        'controlAnchorsInCurrentWindow':len(controls),
        'episodeStatusCounts':dict(Counter(r['episode']['status'] for r in ledger)),
        'walkForward':walk_forward(ledger,controls,p,now),
        'limitations':['Relative price statistic is not an executable purchase price or verified absolute discount.',
            'Features are causal; future quote labels are separate and are never HIT/MISS or realized ROI.',
            'Holdout begins after protocol registration. Daily reference uses only labels known before a 60-minute embargo.',
            'Controls and episodes may overlap across packages or groups; counts are not independent statistical trials.',
            'Descriptive folds do not prove incremental edge beyond EV, regimes, fees or actual delivery latency.',
            'The research schedule is not a live phone notification system. Missing/gapped observations stay censored.']}
    return report,ledger


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pairs',type=Path,default=Path('research/market-edge-pairs.jsonl'))
    ap.add_argument('--market',type=Path,default=Path('calibration/public-market-history.jsonl'))
    ap.add_argument('--protocol',type=Path,default=Path('research/lag-protocol-v1.json'))
    ap.add_argument('--previous',type=Path,default=Path('research/lag-episodes.jsonl'))
    ap.add_argument('--episodes',type=Path,default=Path('research/lag-episodes.jsonl'))
    ap.add_argument('--output',type=Path,default=Path('research/lag-report.json'))
    ap.add_argument('--now')
    a=ap.parse_args(); now=timestamp(a.now) if a.now else datetime.now(timezone.utc)
    if now is None: ap.error('Aware timestamp required')
    sources=[a.pairs,a.market,a.protocol]; outputs=[a.episodes,a.output]
    if len({x.resolve() for x in sources+outputs})!=5 or a.previous.resolve() in {a.output.resolve(),*(p.resolve() for p in sources)}:
        ap.error('Outputs may not overwrite sources; only previous ledger can equal episodes')
    before={str(x):digest(x) for x in sources}
    if a.previous.exists(): before[str(a.previous)]=digest(a.previous)
    counts=Counter(); old=list(json_lines(a.previous,counts)) if a.previous.exists() else []
    # Never silently discard an unreadable previously committed ledger.
    if counts: raise ValueError('Invalid prior ledger')
    protocol=json.loads(a.protocol.read_text())
    report,ledger=run(json_lines(a.pairs,counts),json_lines(a.market,counts),protocol,old,now)
    report.update(inputSha256=before,inputReadWarnings=dict(counts))
    if before!={path:digest(Path(path)) for path in before}: raise RuntimeError('Inputs changed during analysis')
    texts=[ ''.join(canonical(r)+'\n' for r in ledger), json.dumps(report,indent=2,allow_nan=False)+'\n' ]
    for path,text in zip(outputs,texts):
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix(path.suffix+'.tmp'); temp.write_text(text,encoding='utf-8'); temp.replace(path)
    print('LAG RESEARCH OK; CURRENT unchanged; no account or network access')
    print('Episodes:',len(ledger),'Controls:',report['controlAnchorsInCurrentWindow'],'Counts:',report['counts'])
    print('Walk-forward:',report['walkForward']['status'],'| NO_VERIFIED_EDGE')


if __name__=='__main__':
    main()
