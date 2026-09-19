"""Offline decomposition of model disagreement and raw market-field dependence."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median

from apply_math_consistency_shadow import chain_check, as_float, positive
from build_market_edge_research import timestamp, digest, json_lines, source_algorithm


def package_diagnostics(package, feed):
    checks, ev_terms = [], []
    prices = feed.get('market_prices_eur') or {}
    btc = prices.get('BTC') or {}
    btc_eur = positive(btc.get('eur')) if btc.get('fresh') is True else None
    cost = positive(package.get('price_btc_equiv'))
    expected_chains = 2 if package.get('merge_chain') else 1
    for key in ('primary_chain', 'merge_chain'):
        chain = package.get(key)
        if not isinstance(chain, dict) or not chain:
            continue
        check = chain_check(package, chain)
        check.update(chain=key, coin=chain.get('currency'))
        checks.append(check)
        coin = chain.get('currency')
        price = prices.get(coin) or {}
        coin_eur = positive(price.get('eur')) if price.get('fresh') is True else None
        reward = positive(chain.get('block_reward'))
        expected = positive(check.get('difficultyExpectedBlocks'))
        conversion = 1.0 if coin == 'BTC' else coin_eur / btc_eur if coin_eur is not None and btc_eur is not None else None
        if None not in (expected, reward, conversion):
            value = expected * reward * conversion
            if math.isfinite(value):
                ev_terms.append(value)
    conditional = sum(ev_terms) / cost * 100 if len(ev_terms) == expected_chains and cost else None
    if conditional is not None and not math.isfinite(conditional):
        conditional = None
    return {'package': package.get('name'), 'currency': package.get('currency_market'),
        'currentSignal': package.get('final_signal'), 'checks': checks,
        'feedExpectedReturnPercent': as_float((package.get('profitability') or {}).get('expected_return_percent')),
        'conditionalDifficultyExpectedReturnPercent': conditional,
        'conditionalEstimateIsVerifiedReturn': False,
        'conditionalAssumptions': 'Quoted hashrate/duration, constant reported difficulty, existing reward field and fresh same-snapshot FX; delivery/fees and difficulty convention not independently verified.'}


def price_identity(row):
    p, s, v = map(positive, (row.get('priceRaw'), row.get('speedRaw'), row.get('volumeRaw')))
    if None in (p, s, v):
        return None
    product = p * s
    if not math.isfinite(product) or product <= 0:
        return None
    error = 100 * (product / v - 1)
    return error if math.isfinite(error) else None


def summarize_market(rows, now):
    errors, latest = defaultdict(list), {}
    seen, conflicts = {}, set()
    for row in rows:
        at = timestamp(row.get('collected_at'))
        if at is None or not now-timedelta(days=30) <= at <= now or row.get('source') != 'NICEHASH_PUBLIC_MARKET':
            continue
        if any(row.get(k) is not False for k in ('credentials_used', 'private_api_used', 'admin_api_used')):
            continue
        for name, values in (row.get('algorithms') or {}).items():
            if not isinstance(values, dict):
                continue
            key = (name, at)
            if key in seen and seen[key] != values:
                conflicts.add(key)
            seen[key] = values
    for (name, at), values in sorted(seen.items(), key=lambda x: (x[0][1], x[0][0])):
        if (name, at) in conflicts:
            latest[name] = {'observedAt': at.isoformat(), 'unitContract': {}, 'status': 'CONFLICTING_MARKET_ROWS'}
            continue
        error = price_identity(values)
        if error is not None:
            errors[name].append(abs(error))
        latest[name] = {'observedAt': at.isoformat(), 'unitContract': values.get('unitContract') or {}, 'status': 'OBSERVED'}
    return [{'algorithm': name, **value, 'samplesWithVolumeIdentity': len(errors[name]),
        'maxAbsoluteProductVsVolumeErrorPercent': max(errors[name]) if errors[name] else None,
        'volumeEqualsPriceTimesSpeed': bool(errors[name]) and max(errors[name]) <= 0.000001,
        'volumeIsIndependentEvidence': False, 'rawPriceDenominationVerified': False,
        'executablePriceVerified': False, 'absoluteDiscountPercent': None}
        for name, value in sorted(latest.items())]


def build_report(feed, snapshots, market_rows, now):
    counts = Counter()
    observations, conflicts = {}, set()
    for row in snapshots:
        f = row.get('feed') or {}
        at, observed = timestamp(f.get('checked_at')), timestamp(row.get('collected_at'))
        if at is None or observed is None or not now-timedelta(days=30) <= at <= observed <= now:
            counts['invalidOrUnavailableQuoteTime'] += 1
            continue
        if (observed-at).total_seconds() > 900:
            counts['lateObservationExcluded'] += 1
            continue
        if row.get('feed_generated_at') is not None and timestamp(row['feed_generated_at']) != at:
            counts['conflictingTimestamp'] += 1
            continue
        if f.get('ok') is not True or f.get('status') != 'BUY FEED OK':
            continue
        for p in f.get('packages') or []:
            if not isinstance(p, dict):
                continue
            for key in ('primary_chain', 'merge_chain'):
                chain = p.get(key)
                if not isinstance(chain, dict) or not chain:
                    continue
                c = chain_check(p, chain)
                dec = c.get('decomposition') or {}
                if 'impliedBlockTimeSeconds' not in dec:
                    continue
                series = (str(chain.get('currency')), str(chain.get('algorithm')), str(f.get('relay_version')))
                identity = (*series, at)
                signature = tuple(chain.get(k) for k in ('network_difficulty', 'network_hashpower_hps', 'block_time_seconds'))
                if identity in observations:
                    if observations[identity][0] != signature:
                        conflicts.add(identity)
                    counts['duplicateChainTimestamp'] += 1
                else:
                    observations[identity] = (signature, c)
    groups = defaultdict(list)
    for identity, (_, c) in observations.items():
        if identity not in conflicts:
            groups[identity[:3]].append(c)
    history = []
    for (coin, algorithm, version), cs in sorted(groups.items()):
        history.append({'coin': coin, 'algorithm': algorithm, 'relayVersion': version,
            'uniqueChainTimestamps': len(cs),
            'networkFormulaReconciledCount': sum(c['decomposition']['status'] == 'EXPLAINED_BY_TARGET_TIME_MODEL' for c in cs),
            'medianSignedDifferencePercent': median(c['signedDifferencePercent'] for c in cs),
            'minimumSignedDifferencePercent': min(c['signedDifferencePercent'] for c in cs),
            'maximumSignedDifferencePercent': max(c['signedDifferencePercent'] for c in cs),
            'medianImpliedBlockTimeSeconds': median(c['decomposition']['impliedBlockTimeSeconds'] for c in cs),
            'independentEvidenceOfEdge': False})
    quote_at = timestamp(feed.get('checked_at'))
    valid_feed = feed.get('ok') is True and feed.get('status') == 'BUY FEED OK' and quote_at is not None and quote_at <= now
    latest = [package_diagnostics(p, feed) for p in feed.get('packages') or [] if isinstance(p, dict)] if valid_feed else []
    return {'schemaVersion': 1, 'generatedAt': now.isoformat(), 'sourceQuoteAt': feed.get('checked_at'),
        'sourceQuoteAgeMinutes': (now-quote_at).total_seconds()/60 if quote_at else None,
        'sourceQuoteFresh': valid_feed and (now-quote_at).total_seconds() <= 420,
        'role': 'INPUT_DIAGNOSTICS_ONLY', 'currentProductionModelChanged': False,
        'networkRequestsMade': 0, 'privateApiUsed': False, 'canRaiseSignal': False,
        'counts': dict(counts), 'conflictingChainTimestamps': len(conflicts),
        'packages': latest, 'historicalChainReconciliation': history,
        'marketContractsAndDependence': summarize_market(market_rows, now),
        'unresolved': ['Upstream network-difficulty field convention and timestamps need independent confirmation.',
            'Implied interval is algebraic, not a measured future or past block interval.',
            'Matching the network formula identifies a mechanism, not upstream data provenance.',
            'Raw market p currency/scale/day basis and executable capacity/fees are not verified.',
            'Product p*s=v cannot identify physical units or create a third independent feature.',
            'No pricing-lag predictor or change to CURRENT is enabled.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed', type=Path, default=Path('buy-feed.json'))
    parser.add_argument('--snapshots', type=Path, default=Path('calibration/radar-snapshots.jsonl'))
    parser.add_argument('--market', type=Path, default=Path('calibration/public-market-history.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('research/input-diagnostics.json'))
    parser.add_argument('--now')
    args = parser.parse_args()
    now = timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error('Aware time required')
    paths = [args.feed, args.snapshots, args.market, args.output]
    if len({p.resolve() for p in paths}) != 4:
        parser.error('Source and output paths must be distinct')
    before = {str(p): digest(p) for p in paths[:3]}
    feed = json.loads(args.feed.read_text(encoding='utf-8'))
    warnings = Counter()
    report = build_report(feed, json_lines(args.snapshots, warnings), json_lines(args.market, warnings), now)
    report.update(inputSha256=before, inputReadWarnings=dict(warnings))
    if before != {str(p): digest(p) for p in paths[:3]}:
        raise RuntimeError('Inputs changed during diagnosis')
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding='utf-8')
    print('INPUT DIAGNOSTICS OK; no network or account access; CURRENT unchanged')
    for row in report['packages']:
        print(row['package'], '| feed EV', row['feedExpectedReturnPercent'], '| conditional D EV', row['conditionalDifficultyExpectedReturnPercent'])
    for row in report['historicalChainReconciliation']:
        print(row['coin'], row['relayVersion'], '| observations', row['uniqueChainTimestamps'], '| reconciled', row['networkFormulaReconciledCount'])


if __name__ == '__main__':
    main()
