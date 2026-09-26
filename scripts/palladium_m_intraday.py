"""Offline, retrospective Palladium M quote audit; never a trading signal.

Reuse existing read-only Git feed helpers. No HTTP, account data, order results,
or changes to the locked prospective lag protocol. Dates are outcome-selected.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import subprocess
from zoneinfo import ZoneInfo

from match_private_orders_git_feed_context import git_commit_index, load_feed_at_commit
from build_market_edge_research import resolved_currency_and_price, timestamp
from apply_math_consistency_shadow import positive, as_float, expected_blocks_from_difficulty, chain_check

DATES = ('2026-09-07', '2026-09-20', '2026-09-23', '2026-09-24')
TZ = ZoneInfo('Europe/Ljubljana')
MAX_AGE = 420
MAX_GAP = 1200
FIELDS = ('workThHoursFor001Btc', 'costBtcPerThDay', 'fairDifficultyBtcPerThDay',
          'fairHashrateBtcPerThDay', 'returnDifficultyPercent', 'returnHashratePercent')


def sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def finite(value):
    return value if math.isfinite(value) else None


def point(feed, commit, committed, counts):
    """Validate both source and availability time; copy only public whitelisted data."""
    if not isinstance(feed, dict) or any((feed.get('ok') is not True,
            feed.get('status') != 'BUY FEED OK', feed.get('upstream_status') != 200,
            feed.get('market_status') != 'MARKET OK')):
        counts['unhealthyFeed'] += 1
        return None
    at = timestamp(feed.get('checked_at'))
    if at is None or committed.tzinfo is None:
        counts['invalidTime'] += 1
        return None
    age = (committed - at).total_seconds()
    # Git committer time is second-resolution; do not backdate sub-second quotes.
    if age < -1 or age > MAX_AGE:
        counts['staleOrFutureQuote'] += 1
        return None
    available = max(committed, at)
    if age < 0:
        counts['subsecondAvailabilityAdjustedForward'] += 1
    packages = feed.get('packages')
    if not isinstance(packages, list):
        counts['invalidPackages'] += 1
        return None
    selected = [p for p in packages if isinstance(p, dict) and p.get('name') == 'Palladium M']
    if len(selected) != 1:
        counts['missingOrDuplicateM'] += 1
        return None
    p = selected[0]
    currency, cost, currency_source = resolved_currency_and_price(feed, p)
    version = feed.get('relay_version')
    if currency != 'BTC' or cost is None or p.get('available') is not True or not isinstance(version, str) or not version:
        counts['unavailableOrInvalidCurrency'] += 1
        return None
    for k in ('price_btc', 'price_btc_equiv'):
        if k in p and (positive(p[k]) is None or not math.isclose(float(p[k]), cost, rel_tol=1e-8, abs_tol=1e-12)):
            counts['conflictingCost'] += 1
            return None
    h, duration = positive(p.get('package_hashrate_hps')), positive(p.get('duration_seconds'))
    if h is None or duration is None:
        counts['invalidWork'] += 1
        return None
    work = finite(h / 1e12 * duration / 3600)
    if work is None or work <= 0:
        counts['invalidWork'] += 1
        return None
    rate = finite(cost / (work / 24))
    if rate is None or rate <= 0:
        counts['invalidRate'] += 1
        return None
    prices = feed.get('market_prices_eur') or {}
    fx = {}
    for coin in ('BTC', 'LTC', 'DOGE'):
        value = prices.get(coin) or {}
        price, fx_age = positive(value.get('eur')), as_float(value.get('age_seconds'))
        if price is None or value.get('fresh') is not True or fx_age is None or fx_age < 0 or fx_age + max(age, 0) > MAX_AGE:
            counts['missingOrStaleFx'] += 1
            return None
        fx[coin] = price
    chains = []
    for key, coin in (('primary_chain', 'LTC'), ('merge_chain', 'DOGE')):
        c = p.get(key) or {}
        d, reward = positive(c.get('network_difficulty')), positive(c.get('block_reward'))
        network, blocktime = positive(c.get('network_hashpower_hps')), positive(c.get('block_time_seconds'))
        if c.get('currency') != coin or c.get('algorithm') != 'SCRYPT' or None in (d, reward, network, blocktime):
            counts['invalidScryptChain'] += 1
            return None
        conversion = fx[coin] / fx['BTC']
        lam = expected_blocks_from_difficulty(1e12, 86400, d)
        if lam is None:
            counts['invalidChainValue'] += 1
            return None
        fair_d = finite(lam * reward * conversion)
        fair_h = finite(1e12 / network * (86400 / blocktime) * reward * conversion)
        if fair_d is None or fair_h is None or min(fair_d, fair_h) <= 0:
            counts['invalidChainValue'] += 1
            return None
        check = chain_check(p, c)
        chains.append({'coin': coin, 'difficulty': d, 'hashrateHps': network,
            'rewardField': reward, 'coinBtc': conversion, 'blockTimeSeconds': blocktime,
            'fairDifficultyBtcPerThDay': fair_d, 'fairHashrateBtcPerThDay': fair_h,
            'mathStatus': check.get('status'), 'mathDifferencePercent': check.get('signedDifferencePercent')})
    fair_d = sum(c['fairDifficultyBtcPerThDay'] for c in chains)
    fair_h = sum(c['fairHashrateBtcPerThDay'] for c in chains)
    row = {'package': 'Palladium M', 'relayVersion': version, 'currencySource': currency_source,
        'quoteAt': at.isoformat(), 'availableAt': available.isoformat(), 'commitAt': committed.isoformat(),
        'sourceCommit': commit, 'dateLocal': available.astimezone(TZ).date().isoformat(),
        'quoteCostBtc': cost, 'hashrateThs': h / 1e12, 'durationSeconds': duration,
        'workThHoursFor001Btc': work * .001 / cost, 'costBtcPerThDay': rate,
        'fairDifficultyBtcPerThDay': fair_d, 'fairHashrateBtcPerThDay': fair_h,
        'returnDifficultyPercent': 100 * fair_d / rate, 'returnHashratePercent': 100 * fair_h / rate,
        'feedReturnPercent': as_float((p.get('profitability') or {}).get('expected_return_percent')),
        'sourceMathClear': all(c['mathStatus'] == 'PASS' for c in chains),
        'chains': chains, 'currentSignal': p.get('final_signal'),
        'deliveredWorkVerified': False, 'canRaiseSignal': False}
    if any(not math.isfinite(row[k]) for k in FIELDS):
        counts['nonfiniteEconomics'] += 1
        return None
    # Fingerprint excludes source metadata and cosmetic BUY annotations.
    row['economicFingerprint'] = sha256({k: row[k] for k in (
        'relayVersion', 'quoteCostBtc', 'hashrateThs', 'durationSeconds', 'chains')})
    return row


def deduplicate(rows, counts):
    groups, conflicts = {}, set()
    for p in rows:
        key = (p['relayVersion'], p['quoteAt'])
        if key in groups:
            counts['duplicateQuote'] += 1
            if groups[key]['economicFingerprint'] != p['economicFingerprint']:
                conflicts.add(key)
            elif p['availableAt'] < groups[key]['availableAt']:
                groups[key] = p
        else:
            groups[key] = p
    counts['conflictingQuotesExcluded'] += len(conflicts)
    return sorted((p for k, p in groups.items() if k not in conflicts), key=lambda p: (p['availableAt'], p['quoteAt']))


def describe(values):
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return {'n': len(vals), 'min': min(vals) if vals else None,
            'median': median(vals) if vals else None, 'max': max(vals) if vals else None}


def same_series(a, b):
    return a['relayVersion'] == b['relayVersion'] and a['durationSeconds'] == b['durationSeconds'] and a['quoteCostBtc'] == b['quoteCostBtc'] and [(c['coin'], c['rewardField']) for c in a['chains']] == [(c['coin'], c['rewardField']) for c in b['chains']]


def change(a, b):
    return 100 * (a / b - 1)


def build_report(rows, dates, counts=None):
    counts = Counter() if counts is None else counts
    rows = deduplicate(rows, counts)
    times = [timestamp(p['availableAt']) for p in rows]
    selected = [p for p in rows if p['dateLocal'] in dates]
    days = []
    for day in dates:
        rs = [p for p in selected if p['dateLocal'] == day]
        gaps = [(timestamp(b['availableAt']) - timestamp(a['availableAt'])).total_seconds()/60 for a, b in zip(rs, rs[1:])]
        days.append({'date': day, 'quoteCount': len(rs), 'status': 'OBSERVED_QUOTES_ONLY' if rs else 'NO_DATA',
            'firstLocalAt': timestamp(rs[0]['availableAt']).astimezone(TZ).isoformat() if rs else None,
            'lastLocalAt': timestamp(rs[-1]['availableAt']).astimezone(TZ).isoformat() if rs else None,
            'gapMinutes': describe(gaps), 'relayVersions': sorted({r['relayVersion'] for r in rs}),
            'metrics': {k: describe([r[k] for r in rs]) for k in FIELDS},
            'difficultyReturnAbove100Quotes': sum(r['returnDifficultyPercent'] > 100 for r in rs),
            'bothModelsAbove100Quotes': sum(min(r['returnDifficultyPercent'], r['returnHashratePercent']) > 100 for r in rs),
            'bothAbove100AndMathPassQuotes': sum(min(r['returnDifficultyPercent'], r['returnHashratePercent']) > 100 and r['sourceMathClear'] for r in rs)})
    windows = []
    for i, p in enumerate(rows):
        if p['dateLocal'] not in dates:
            continue
        for horizon, tolerance in ((15, 5), (30, 10), (60, 10)):
            target = times[i] - timedelta(minutes=horizon)
            j = bisect_right(times, target) - 1
            if j < 0 or (target-times[j]).total_seconds() > tolerance*60:
                counts[f'{horizon}mMissingBaseline'] += 1
                continue
            path = rows[j:i+1]
            if any(not same_series(p, x) for x in path) or any((b-a).total_seconds() > MAX_GAP for a, b in zip(times[j:i], times[j+1:i+1])):
                counts[f'{horizon}mSeriesOrGapExcluded'] += 1
                continue
            b = rows[j]
            fair_d_change = change(p['fairDifficultyBtcPerThDay'], b['fairDifficultyBtcPerThDay'])
            fair_h_change = change(p['fairHashrateBtcPerThDay'], b['fairHashrateBtcPerThDay'])
            cost_change = change(p['costBtcPerThDay'], b['costBtcPerThDay'])
            div = change(p['returnDifficultyPercent'], b['returnDifficultyPercent'])
            screen = fair_d_change >= 5 and cost_change <= 1 and div >= 5
            w = {'dateLocal': p['dateLocal'], 'horizonMinutes': horizon,
                'startLocalAt': times[j].astimezone(TZ).isoformat(), 'endLocalAt': times[i].astimezone(TZ).isoformat(),
                'actualMinutes': (times[i]-times[j]).total_seconds()/60,
                'startCommit': b['sourceCommit'], 'endCommit': p['sourceCommit'],
                'fairDifficultyChangePercent': fair_d_change, 'fairHashrateChangePercent': fair_h_change,
                'costChangePercent': cost_change, 'difficultyReturnRelativeChangePercent': div,
                'returnDifficultyPercent': p['returnDifficultyPercent'], 'returnHashratePercent': p['returnHashratePercent'],
                'ltcDifficultyChangePercent': change(p['chains'][0]['difficulty'], b['chains'][0]['difficulty']),
                'dogeDifficultyChangePercent': change(p['chains'][1]['difficulty'], b['chains'][1]['difficulty']),
                'exploratoryScreen': screen, 'sourceMathClear': p['sourceMathClear'], 'forward': []}
            for delay in (15, 30):
                target_forward = times[i]+timedelta(minutes=delay)
                k = bisect_left(times, target_forward)
                label = {'horizonMinutes': delay, 'status': 'MISSING_OR_GAPPED'}
                if k < len(rows) and (times[k]-target_forward).total_seconds() <= 300 and all(same_series(p, x) for x in rows[i:k+1]) and all((b-a).total_seconds() <= MAX_GAP for a,b in zip(times[i:k], times[i+1:k+1])):
                    f = rows[k]
                    label.update(status='OBSERVED', actualMinutes=(times[k]-times[i]).total_seconds()/60,
                        sourceCommit=f['sourceCommit'], fairDifficultyChangePercent=change(f['fairDifficultyBtcPerThDay'], p['fairDifficultyBtcPerThDay']),
                        costChangePercent=change(f['costBtcPerThDay'], p['costBtcPerThDay']),
                        returnDifficultyPercent=f['returnDifficultyPercent'], returnHashratePercent=f['returnHashratePercent'])
                w['forward'].append(label)
            windows.append(w)
    screens = [w for w in windows if w['horizonMinutes'] == 15 and w['exploratoryScreen']]
    episodes = []
    last = None
    for w in screens:
        at = timestamp(w['endLocalAt'])
        if last is None or (at-last).total_seconds() >= 3600:
            episodes.append(w)
            last = at
    future_summary = {}
    for delay in (15, 30):
        valid = [x for w in episodes for x in w['forward'] if x['horizonMinutes']==delay and x['status']=='OBSERVED']
        future_summary[str(delay)] = {'observedEpisodes': len(valid), 'unobservedEpisodes': len(episodes)-len(valid),
            'costChangePercent': describe([x['costChangePercent'] for x in valid]),
            'fairDifficultyChangePercent': describe([x['fairDifficultyChangePercent'] for x in valid]),
            'difficultyReturnStillAbove100': sum(x['returnDifficultyPercent']>100 for x in valid)}
    return {'schemaVersion': 1, 'role': 'RETROSPECTIVE_INTRADAY_DIAGNOSTICS_NOT_LOCKED_LAG_PROTOCOL',
        'timezone': 'Europe/Ljubljana', 'focusDates': list(dates), 'counts': dict(counts),
        'quoteCount': len(selected), 'days': days, 'eligibleWindowsByHorizon': dict(Counter(w['horizonMinutes'] for w in windows)),
        'screen15mWindows': len(screens), 'episodesWith60mSpacing': len(episodes),
        'episodesBothModelsAbove100': sum(min(w['returnDifficultyPercent'], w['returnHashratePercent'])>100 for w in episodes),
        'episodesBothAbove100AndMathPass': sum(min(w['returnDifficultyPercent'], w['returnHashratePercent'])>100 and w['sourceMathClear'] for w in episodes),
        'forwardQuoteDiagnostics': future_summary, 'episodes': episodes,
        'windowSummaries': {str(h): {k: describe([w[k] for w in windows if w['horizonMinutes']==h]) for k in ('fairDifficultyChangePercent','fairHashrateChangePercent','costChangePercent')} for h in (15,30,60)},
        'canRaiseSignal': False, 'currentProductionModelChanged': False, 'privateApiUsed': False,
        'adminApiUsed': False, 'networkRequestsMadeByAnalysis': 0, 'canEstimateHitRate': False,
        'verifiedNetReturnPercent': None, 'verdict': 'NO_VERIFIED_EDGE',
        'limitations': [
            'These dates were chosen after looking at rewards. This is not an out-of-sample validation.',
            'All reachable buy-feed changes in local-calendar windows are inspected, not only the first 100 search hits.',
            'Git commit time is an availability proxy, not independently verified upstream freshness or remote publication time.',
            '15m means observed baseline 15-20m earlier; 30/60m allow 10m tolerance. No gap over 20m is bridged.',
            'The exploratory >=5% difficulty-value rise / <=1% cost rise screen is not the existing frozen public-market lag protocol.',
            'One episode per hour reduces overlap but does not establish independence or a causal effect.',
            'Forward labels describe the next observed quote, not executed trades, ticket HIT/MISS or realized returns.',
            'Both difficulty and hashrate models are conditional, reuse reported inputs and are not independent truth.',
            'A momentary difficulty is not assumed to persist for the two-hour package. Fee basis and delivered work remain unverified.',
            'Hashrate displayed in order details times wall-clock duration does not prove integrated delivered work.',
        ]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dates', nargs='+', default=list(DATES))
    ap.add_argument('--output', type=Path, default=Path('/tmp/palladium-m-intraday-report.json'))
    ap.add_argument('--quotes', type=Path, default=Path('/tmp/palladium-m-git-quotes.jsonl'))
    args = ap.parse_args()
    if args.output.resolve() == args.quotes.resolve() or any(p.resolve()==Path('buy-feed.json').resolve() for p in (args.output,args.quotes)):
        ap.error('Distinct non-source output paths required')
    if subprocess.check_output(['git','rev-parse','--is-shallow-repository'], text=True).strip() != 'false':
        raise RuntimeError('Full Git history required; refusing to label a shallow clone complete')
    bounds = []
    for d in args.dates:
        start = datetime.fromisoformat(d).replace(tzinfo=TZ)
        bounds.append((start.astimezone(timezone.utc)-timedelta(hours=2),
                       (start+timedelta(days=1)).astimezone(timezone.utc)+timedelta(hours=1)))
    counts = Counter()
    rows = []
    index = git_commit_index()
    candidates = [(sha, at) for sha,at in index if any(a<=at<b for a,b in bounds)]
    for sha, committed in candidates:
        counts['gitCommitsInspected'] += 1
        feed = load_feed_at_commit(sha)
        p = point(feed, sha, committed, counts)
        if p is not None:
            rows.append(p)
    report = build_report(rows, args.dates, counts)
    report['generatedAt'] = datetime.now(timezone.utc).isoformat()
    report['gitHead'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    report['gitHistoryEntries'] = len(index)
    report['candidateManifestSha256'] = sha256([(sha,at.isoformat()) for sha,at in candidates])
    report['codeSha256'] = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (
        'scripts/palladium_m_intraday.py', 'scripts/match_private_orders_git_feed_context.py',
        'scripts/build_market_edge_research.py', 'scripts/apply_math_consistency_shadow.py')}
    clean = deduplicate(rows, Counter())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.quotes.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    args.quotes.write_text(''.join(json.dumps(p, separators=(',', ':'), allow_nan=False)+'\n' for p in clean))
    print('INTRADAY_REPORT_JSON_START')
    print(json.dumps(report, separators=(',', ':'), allow_nan=False))
    print('INTRADAY_REPORT_JSON_END')
    print('PUBLIC GIT HISTORY ONLY; CURRENT and locked lag protocol unchanged')


if __name__ == '__main__':
    main()
