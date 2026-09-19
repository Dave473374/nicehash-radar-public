"""Offline, public-file-only pricing-lag research. Never produces a BUY signal."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

SCHEMA = 1
LOOKBACK_DAYS = 30
MAX_PAIR_AGE_SECONDS = 600
MAX_OBSERVATION_DELAY_SECONDS = 900
MAX_TRANSITION_GAP_SECONDS = 1200
MIN_BASELINE_SLOTS = 24
MIN_BASELINE_HOURS = 12


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except ValueError:
        return None


def numeric(value: Any, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and (n > 0 if positive else n >= 0) else None
    except (ValueError, OverflowError):
        return None


def iso(dt: datetime) -> str:
    return dt.isoformat()


def json_lines(path: Path, counts: Counter):
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('not an object')
            except (ValueError, TypeError):
                counts['malformedRows'] += 1
                continue
            yield row


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def legacy_282_btc_only_feed(feed: dict) -> bool:
    packages = feed.get('packages')
    if feed.get('relay_version') != '2.8.2' or not isinstance(packages, list) or not packages:
        return False
    for package in packages:
        if not isinstance(package, dict):
            return False
        if package.get('currency_market') not in (None, ''):
            return False
        if str(package.get('size') or '') not in {'S', 'M'}:
            return False
        price_btc = numeric(package.get('price_btc'), True)
        if price_btc is None:
            return False
    return True


def resolved_currency_and_price(feed: dict, package: dict) -> tuple[str, float | None, str]:
    explicit = str(package.get('currency_market') or '').upper()
    if explicit:
        return explicit, numeric(package.get('price_native'), True), 'EXPLICIT_CURRENCY_MARKET'
    if legacy_282_btc_only_feed(feed):
        return 'BTC', numeric(package.get('price_btc'), True), 'LEGACY_2_8_2_BTC_ONLY_SCHEMA'
    return '', None, 'MISSING'


def source_algorithm(algorithm: str, currency: str) -> str | None:
    base = algorithm.upper().removesuffix('_USDT')
    if currency == 'USDT':
        return 'SHA256ASICBOOST_USDT' if base == 'SHA256ASICBOOST' else None
    if currency == 'BTC' and base in {'SHA256ASICBOOST', 'SCRYPT', 'EQUIHASH', 'KHEAVYHASH'}:
        return base
    return None


def collect_quotes(rows, now: datetime, counts: Counter) -> list[dict]:
    unique, conflicts = {}, set()
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    for row in rows:
        feed = row.get('feed')
        if not isinstance(feed, dict) or feed.get('ok') is not True or feed.get('status') != 'BUY FEED OK':
            counts['unhealthyFeeds'] += 1
            continue
        quote, observed = timestamp(feed.get('checked_at')), timestamp(row.get('collected_at'))
        if quote is None or observed is None or not cutoff <= quote <= observed <= now:
            counts['invalidQuoteTimes'] += 1
            continue
        declared = row.get('feed_generated_at')
        if declared is not None and timestamp(declared) != quote:
            counts['conflictingQuoteTimes'] += 1
            continue
        if (observed - quote).total_seconds() > MAX_OBSERVATION_DELAY_SECONDS:
            counts['delayedObservations'] += 1
            continue
        version = feed.get('relay_version')
        packages = feed.get('packages')
        if not isinstance(version, str) or not version or not isinstance(packages, list):
            counts['invalidFeedSchema'] += 1
            continue
        for p in packages:
            if not isinstance(p, dict):
                continue
            chain = p.get('primary_chain') or {}
            merge = p.get('merge_chain') or {}
            if not isinstance(chain, dict) or not isinstance(merge, dict):
                continue
            name, coin = p.get('name'), chain.get('currency')
            currency, price, currency_source = resolved_currency_and_price(feed, p)
            algorithm = str(chain.get('algorithm') or '').upper()
            market_key = source_algorithm(algorithm, currency)
            h = numeric(p.get('package_hashrate_hps'), True)
            duration = numeric(p.get('duration_seconds'), True)
            if not isinstance(name, str) or not name or not isinstance(coin, str) or not coin or market_key is None or None in (h, duration, price):
                counts['invalidPackages'] += 1
                continue
            if p.get('available') is not True:
                counts['unavailableQuotes'] += 1
                continue
            work = h * duration
            work_per_native = work / price
            if not math.isfinite(work) or not math.isfinite(work_per_native) or work_per_native <= 0:
                counts['invalidPackages'] += 1
                continue
            identity = (name, str(p.get('size') or ''), currency, coin, str(merge.get('currency') or ''), market_key, version)
            point = {
                'package': name, 'size': identity[1], 'currency': currency, 'coin': coin,
                'mergeCoin': identity[4] or None, 'marketAlgorithm': market_key, 'relayVersion': version,
                'currencySource': currency_source,
                'quoteAt': iso(quote), 'observedAt': iso(observed),
                'priceNative': price, 'durationSeconds': duration, 'hashrateHps': h,
                'workPerNative': work_per_native,
                'feedExpectedReturnPercent': numeric((p.get('profitability') or {}).get('expected_return_percent')),
                'mathStatus': (p.get('math_consistency_shadow') or {}).get('status') or 'NOT_RECORDED',
                'primaryDifficulty': numeric(chain.get('network_difficulty'), True),
                'mergeDifficulty': numeric(merge.get('network_difficulty'), True),
                'costEur': numeric((p.get('economics') or {}).get('package_cost_eur'), True),
                'currentSignal': p.get('final_signal'),
            }
            key = (identity, iso(quote))
            # Annotation changes do not turn one upstream quote into multiple observations.
            comparable = {k: v for k, v in point.items() if k not in {'observedAt', 'mathStatus'}}
            fingerprint = json.dumps(comparable, sort_keys=True, allow_nan=False)
            if key in conflicts:
                continue
            if key in unique:
                old, old_fp = unique[key]
                if old_fp != fingerprint:
                    conflicts.add(key)
                    del unique[key]
                    counts['conflictingQuotes'] += 1
                else:
                    counts['duplicateQuotes'] += 1
                    if observed < timestamp(old['observedAt']):
                        unique[key] = (point, fingerprint)
                continue
            unique[key] = (point, fingerprint)
    return sorted((p for p, _ in unique.values()), key=lambda p: (p['quoteAt'], p['package'], p['currency']))


def market_index(rows, now: datetime, counts: Counter) -> dict:
    unique, conflicts = {}, set()
    for row in rows:
        at = timestamp(row.get('collected_at'))
        if at is None or at > now or at < now - timedelta(days=LOOKBACK_DAYS):
            counts['invalidMarketTimes'] += 1
            continue
        if row.get('source') != 'NICEHASH_PUBLIC_MARKET' or any(row.get(k) is not False for k in ('credentials_used', 'private_api_used', 'admin_api_used')):
            counts['unverifiedMarketProvenance'] += 1
            continue
        algos = row.get('algorithms')
        if not isinstance(algos, dict):
            counts['invalidMarketSchema'] += 1
            continue
        for name, values in algos.items():
            if not isinstance(values, dict):
                values = {}
            price = numeric(values.get('priceRaw'), True)
            speed = numeric(values.get('speedRaw'), True)
            orders = numeric(values.get('orders'))
            unit = values.get('speedUnit')
            multi, price_multi = numeric(values.get('multi'), True), numeric(values.get('priceMulti'), True)
            valid = None not in (price, speed, orders, multi, price_multi) and isinstance(unit, str) and bool(unit)
            data = {'at': iso(at), 'valid': valid, 'priceRaw': price, 'speedRaw': speed, 'orders': orders,
                    'unitSignature': [unit, multi, price_multi]}
            key = (name, at)
            if key in conflicts:
                continue
            if key in unique and data != unique[key]:
                conflicts.add(key)
                unique[key] = {'at': iso(at), 'valid': False, 'reason': 'CONFLICTING_MARKET_ROWS'}
                counts['conflictingMarketRows'] += 1
            else:
                unique[key] = data
    out = defaultdict(list)
    for (name, at), data in unique.items():
        out[name].append((at, data))
    return {k: sorted(v, key=lambda x: x[0]) for k, v in out.items()}


def pair_quotes(quotes: list[dict], market: dict, counts: Counter) -> list[dict]:
    axes = {key: [at for at, _ in rows] for key, rows in market.items()}
    output = []
    for p in quotes:
        p = dict(p)
        at = timestamp(p['quoteAt'])
        rows = market.get(p['marketAlgorithm'], [])
        pos = bisect_right(axes.get(p['marketAlgorithm'], []), at) - 1
        status = 'NO_PAST_MARKET'
        if pos >= 0:
            mt, m = rows[pos]
            age = (at - mt).total_seconds()
            if age > MAX_PAIR_AGE_SECONDS:
                status = 'STALE_MARKET'
            elif not m['valid']:
                status = 'INVALID_MARKET'
            else:
                status = 'PAIRED'
                p.update(marketAt=iso(mt), marketAgeSeconds=age, marketPriceRaw=m['priceRaw'],
                         marketUnitSignature=m['unitSignature'], marketOrders=m['orders'], marketSpeedRaw=m['speedRaw'])
                # Multiplicative unknown conversion cancels in within-series ratios only.
                p['relativeValueLogIndex'] = math.log(m['priceRaw']) + math.log(p['workPerNative'])
        p['pairStatus'] = status
        counts[status] += 1
        output.append(p)
    return output


def same_series(a: dict, b: dict) -> bool:
    keys = ('package', 'size', 'currency', 'coin', 'mergeCoin', 'marketAlgorithm', 'relayVersion', 'marketUnitSignature')
    return all(a.get(k) == b.get(k) for k in keys)


def pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 6)


def baseline(point: dict, previous: list[dict]) -> dict:
    at = timestamp(point['quoteAt'])
    # One record per 15-minute slot avoids overweighting rapid repeats. Strictly past, known observations only.
    slots = {}
    for p in previous:
        pt = timestamp(p['quoteAt'])
        if p.get('pairStatus') == 'PAIRED' and same_series(point, p) and at - timedelta(hours=24) <= pt < at and timestamp(p['observedAt']) <= at:
            slots[int(pt.timestamp()) // 900] = p
    rows = sorted(slots.values(), key=lambda p: p['quoteAt'])
    span = (timestamp(rows[-1]['quoteAt']) - timestamp(rows[0]['quoteAt'])).total_seconds() / 3600 if len(rows) > 1 else 0
    ready = len(rows) >= MIN_BASELINE_SLOTS and span >= MIN_BASELINE_HOURS
    result = {'status': 'DESCRIPTIVE_ONLY' if ready else 'INSUFFICIENT_HISTORY', 'slots15m': len(rows), 'coverageHours': round(span, 3), 'relativeValueVsPriorMedianPercent': None, 'priorPercentile': None}
    if ready and point.get('pairStatus') == 'PAIRED':
        values = [r['relativeValueLogIndex'] for r in rows]
        difference = point['relativeValueLogIndex'] - median(values)
        result['relativeValueVsPriorMedianPercent'] = round(math.expm1(difference) * 100, 6) if abs(difference) < 100 else None
        result['priorPercentile'] = round(100 * (sum(v < point['relativeValueLogIndex'] for v in values) + 0.5 * sum(v == point['relativeValueLogIndex'] for v in values)) / len(values), 3)
    return result


def transition(previous: dict, point: dict) -> dict | None:
    if not same_series(previous, point) or previous.get('pairStatus') != 'PAIRED' or point.get('pairStatus') != 'PAIRED':
        return None
    gap = (timestamp(point['quoteAt']) - timestamp(previous['quoteAt'])).total_seconds()
    if not 0 < gap <= MAX_TRANSITION_GAP_SECONDS or previous['marketAt'] == point['marketAt'] or timestamp(previous['observedAt']) > timestamp(point['quoteAt']):
        return None
    market_ratio = point['marketPriceRaw'] / previous['marketPriceRaw']
    ticket_price_ratio = previous['workPerNative'] / point['workPerNative']
    return {'from': previous['quoteAt'], 'to': point['quoteAt'], 'gapSeconds': gap,
            'marketRawChangePercent': pct(point['marketPriceRaw'], previous['marketPriceRaw']),
            'ticketCostPerWorkChangePercent': round((ticket_price_ratio - 1) * 100, 6),
            'workPerNativeChangePercent': pct(point['workPerNative'], previous['workPerNative']),
            'divergenceLogPercentagePoints': round(100 * (math.log(market_ratio) - math.log(ticket_price_ratio)), 6),
            'interpretation': 'RELATIVE_DIVERGENCE_NOT_PROVEN_PRICING_LAG'}


def forward_labels(points: list[dict], counts: Counter) -> dict:
    changes = []
    times = [timestamp(p['quoteAt']) for p in points]
    for i, p in enumerate(points):
        if p.get('pairStatus') != 'PAIRED':
            continue
        target = times[i] + timedelta(minutes=15)
        j = bisect_right(times, target - timedelta(microseconds=1))
        if j >= len(points):
            counts['forwardPending'] += 1
            continue
        q = points[j]
        if times[j] > target + timedelta(minutes=10) or q.get('pairStatus') != 'PAIRED' or not same_series(p, q):
            counts['forwardUnusable'] += 1
            continue
        changes.append(pct(q['workPerNative'], p['workPerNative']))
    return {'role': 'RETROSPECTIVE_QUOTE_LABEL_ONLY_NOT_A_FEATURE_OR_REALIZED_RETURN',
            'horizonMinutes': 15, 'matchedLabels': len(changes),
            'medianFutureWorkPerNativeChangePercent': round(median(changes), 6) if changes else None,
            'usedForBuyDecisions': False}


def build_report(snapshot_rows, market_rows, now: datetime) -> tuple[dict, list[dict]]:
    counts = Counter()
    quotes = collect_quotes(snapshot_rows, now, counts)
    markets = market_index(market_rows, now, counts)
    paired = pair_quotes(quotes, markets, counts)
    groups = defaultdict(list)
    for p in paired:
        groups[(p['package'], p['currency'], p['coin'], p['mergeCoin'])].append(p)
    summaries = []
    for _, points in sorted(groups.items(), key=lambda item: tuple(str(v or '') for v in item[0])):
        latest = points[-1]
        at = timestamp(latest['quoteAt'])
        valid = [p for p in points if p['pairStatus'] == 'PAIRED']
        changes = [v for a, b in zip(points, points[1:]) if (v := transition(a, b)) is not None]
        gaps = [(timestamp(b['quoteAt']) - timestamp(a['quoteAt'])).total_seconds() / 60 for a, b in zip(points, points[1:])]
        span = (timestamp(points[-1]['quoteAt']) - timestamp(points[0]['quoteAt'])).total_seconds() / 3600
        summaries.append({
            'package': latest['package'], 'currency': latest['currency'], 'coin': latest['coin'],
            'priority': 'PRIMARY' if latest['size'].upper() == 'S' or latest['currency'] == 'USDT' and latest['size'] in {'5', '20'} else 'SECONDARY',
            'within100EurAtLastQuote': latest['costEur'] <= 100 if latest['costEur'] is not None else None,
            'quoteCount': len(points), 'pairedCount': len(valid), 'coverageHours': round(span, 3),
            'medianQuoteGapMinutes': round(median(gaps), 3) if gaps else None,
            'maxQuoteGapMinutes': round(max(gaps), 3) if gaps else None,
            'latestQuoteAt': latest['quoteAt'], 'latestQuoteAgeMinutes': round((now-at).total_seconds()/60, 3),
            'latestIsFresh': 0 <= (now-at).total_seconds() <= 420,
            'latestPairStatus': latest['pairStatus'], 'latestMathStatus': latest['mathStatus'],
            'mathCleared': latest['mathStatus'] == 'PASS',
            'baseline': baseline(latest, points[:-1]) if latest['pairStatus'] == 'PAIRED' else {'status': 'NO_VALID_CURRENT_PAIR'},
            'comparableTransitions': len(changes), 'lastTransition': changes[-1] if changes else None,
            'forwardQuoteLabels': forward_labels(points, counts),
            'absoluteMarketDiscountPercent': None,
            'lagTestStatus': 'NOT_RUN_REQUIRE_SEPARATE_WALK_FORWARD_VALIDATION',
            'verdict': 'NO_VERIFIED_EDGE',
        })
    report = {'schemaVersion': SCHEMA, 'generatedAt': iso(now), 'role': 'PUBLIC_MARKET_RESEARCH_ONLY',
              'currentProductionModelChanged': False, 'canRaiseSignal': False, 'automaticPurchase': False, 'automaticCancel': False,
              'privateApiUsed': False, 'networkRequestsMadeByResearch': 0,
              'quoteCount': len(quotes), 'pairedCount': counts['PAIRED'], 'counts': dict(counts),
              'settings': {'lookbackDays': LOOKBACK_DAYS, 'maxMarketAgeSeconds': MAX_PAIR_AGE_SECONDS,
                           'maxObservedDelaySeconds': MAX_OBSERVATION_DELAY_SECONDS, 'maxTransitionGapSeconds': MAX_TRANSITION_GAP_SECONDS},
              'limitations': ['Raw market price denomination/executability is not verified: no absolute discount is calculated.',
                              'Relative index is comparable only within unchanged algorithm, currency, package, version and units.',
                              'Divergence and forward quote labels do not demonstrate block-prediction or realized profit.',
                              'PAIRING is as-of quote time; future market rows and late observations are excluded.',
                              'A math PASS is consistency, not independent verification of input truth.',
                              'Market collection time records receipt, not a verified upstream price timestamp.',
                              'Relay 2.8.2 BTC-only snapshots may be normalized from price_btc when the whole feed satisfies the frozen legacy schema contract.'],
              'packages': sorted(summaries, key=lambda p: (p['priority'] != 'PRIMARY', p['package'], p['currency']))}
    return report, paired


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshots', type=Path, default=Path('calibration/radar-snapshots.jsonl'))
    ap.add_argument('--market', type=Path, default=Path('calibration/public-market-history.jsonl'))
    ap.add_argument('--feed', type=Path, default=Path('buy-feed.json'))
    ap.add_argument('--output', type=Path, default=Path('research/market-edge-report.json'))
    ap.add_argument('--pairs', type=Path, default=Path('research/market-edge-pairs.jsonl'))
    ap.add_argument('--now', help='Aware ISO time for reproducible offline analysis')
    args = ap.parse_args()
    now = timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        ap.error('--now must include a timezone')
    sources = [args.snapshots, args.market, args.feed]
    if len({p.resolve() for p in sources + [args.output, args.pairs]}) != 5:
        ap.error('Sources and outputs must all be distinct files')
    fingerprints = {str(p): digest(p) for p in sources}
    read_counts = Counter()
    def snapshots():
        yield from json_lines(args.snapshots, read_counts)
        with args.feed.open(encoding='utf-8') as handle:
            live = json.load(handle)
        yield {'collected_at': iso(now), 'feed': live}
    report, pairs = build_report(snapshots(), json_lines(args.market, read_counts), now)
    report['inputReadWarnings'] = dict(read_counts)
    report['inputSha256'] = fingerprints
    if fingerprints != {str(p): digest(p) for p in sources}:
        raise RuntimeError('Input changed during research; report refused')
    for path in (args.output, args.pairs):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')
    args.pairs.write_text(''.join(json.dumps(p, separators=(',', ':'), allow_nan=False)+'\n' for p in pairs), encoding='utf-8')
    print('PUBLIC MARKET RESEARCH OK; CURRENT unchanged; no network requests')
    print('Quotes:', report['quoteCount'], 'Paired:', report['pairedCount'], 'Counts:', report['counts'])
    for p in report['packages']:
        print(p['package'], p['currency'], '| pairs', p['pairedCount'], '| baseline', p['baseline']['status'], '| math', p['latestMathStatus'], '| NO_VERIFIED_EDGE')


if __name__ == '__main__':
    main()
