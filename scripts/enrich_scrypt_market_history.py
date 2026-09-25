"""Add conditional Palladium unit economics to the EXISTING public collector.

Offline only. Reads the already-collected BUY feed; never fetches account data,
changes signals, normalizes undocumented market p, or reconstructs Admin PRICE.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from apply_math_consistency_shadow import (
    as_float, positive, expected_blocks_from_difficulty, chain_check,
)

MAX_QUOTE_AGE_SECONDS = 420
MAX_MARKET_AGE_SECONDS = 120
TH = 1e12
DAY = 86400
PACKAGES = frozenset(('Palladium S', 'Palladium M'))


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def cadence(rows, at):
    times = sorted({t for row in rows if isinstance(row, dict)
                    if (t := timestamp(row.get('collected_at'))) is not None
                    and at - timedelta(days=1) <= t <= at})
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return {'samplesLast24h': len(times),
            'medianGapSeconds': median(gaps) if gaps else None,
            'maximumGapSeconds': max(gaps) if gaps else None,
            'gapsOver10Minutes': sum(g > 600 for g in gaps),
            'scheduledIntervalSeconds': 300, 'exactCadenceGuaranteed': False}


def package_economics(package, fx):
    name = package.get('name')
    out = {'package': name, 'priority': 'PRIMARY' if name == 'Palladium S' else 'SECONDARY',
           'status': 'UNAVAILABLE', 'reasons': [], 'canRaiseSignal': False,
           'verifiedNetExpectedReturnPercent': None, 'actualDeliveredWorkVerified': False}
    if package.get('available') is not True or package.get('currency_market') != 'BTC':
        out['reasons'].append('UNAVAILABLE_OR_NON_BTC_PACKAGE')
        return out
    h, seconds, cost = map(positive, (package.get('package_hashrate_hps'),
                                    package.get('duration_seconds'), package.get('price_native')))
    if None in (h, seconds, cost):
        out['reasons'].append('INVALID_QUOTED_WORK_OR_COST')
        return out
    # A different native cost must not be silently replaced with BTC-equivalent cost.
    for field in ('price_btc', 'price_btc_equiv'):
        other = positive(package.get(field))
        if other is None or not math.isclose(other, cost, rel_tol=1e-8, abs_tol=1e-12):
            out['reasons'].append('CONFLICTING_OR_MISSING_BTC_COST')
            return out
    work = finite((h / TH) * (seconds / DAY))
    rate = finite(cost / work) if work is not None and work > 0 else None
    if rate is None or rate <= 0:
        out['reasons'].append('INVALID_WORK_CONVERSION')
        return out
    out.update(quoteCostBtc=cost, quoteHashrateHps=h, quoteDurationSeconds=seconds,
               quoteWorkThDays=work, quoteWorkThHours=finite(work * 24),
               quoteImpliedCostBtcPerThDay=rate,
               quoteRateIsExecutableMarketPrice=False,
               rewardBasis='EXACT_UPSTREAM_BLOCK_REWARD_FIELD_FEE_BASIS_UNVERIFIED',
               additionalPoolFeeApplied=False)
    chains = [package.get(k) for k in ('primary_chain', 'merge_chain')]
    if (any(not isinstance(c, dict) for c in chains)
            or {c.get('currency') for c in chains} != {'LTC', 'DOGE'}
            or any(c.get('algorithm') != 'SCRYPT' for c in chains)):
        out['reasons'].append('EXPECTED_LTC_AND_DOGE_SCRYPT_CHAINS')
        return out
    terms = []
    for c in chains:
        coin = c['currency']
        d, reward = map(positive, (c.get('network_difficulty'), c.get('block_reward')))
        lam = expected_blocks_from_difficulty(TH, DAY, d)
        value = finite(lam * reward * fx[coin]['btc']) if None not in (lam, reward) else None
        if value is None or value <= 0:
            out['reasons'].append('INVALID_DIFFICULTY_OR_REWARD_' + coin)
            return out
        check = chain_check(package, c)
        terms.append({'coin': coin, 'difficulty': d,
                      'networkHashrateHps': positive(c.get('network_hashpower_hps')),
                      'targetBlockTimeSeconds': positive(c.get('block_time_seconds')),
                      'blockRewardField': reward,
                      'blockRewardWithNhFeeField': positive(c.get('block_reward_with_nh_fee')),
                      'coinBtc': fx[coin]['btc'],
                      'conditionalBlocksPerThDay': lam,
                      'conditionalValueBtcPerThDay': value,
                      'sourceMathStatus': check.get('status'),
                      'feedVsDifficultyDifferencePercent': as_float(check.get('signedDifferencePercent'))})
    fair = finite(sum(t['conditionalValueBtcPerThDay'] for t in terms))
    if fair is None or fair <= 0:
        out['reasons'].append('INVALID_MERGED_VALUE')
        return out
    premium, ret = finite(100 * (rate / fair - 1)), finite(100 * fair / rate)
    if None in (premium, ret):
        out['reasons'].append('NUMERIC_OVERFLOW')
        return out
    out.update(status='CONDITIONAL_MODEL_ONLY', chains=terms,
               conditionalMergedValueBtcPerThDay=fair,
               conditionalQuotePremiumPercent=premium,
               conditionalExpectedReturnPercent=ret,
               conditionalExpectedRewardBtc=finite(fair * work),
               sourceMathClear=all(t['sourceMathStatus'] == 'PASS' for t in terms),
               independentNetworkVerification=False,
               upstreamDifficultyConventionVerified=False,
               adminPriceReconstructed=False)
    return out


def build_economics(feed, market, now, history=()):
    result = {'schemaVersion': 1, 'generatedAt': now.isoformat(),
              'role': 'SCRYPT_PUBLIC_ECONOMICS_RESEARCH_ONLY',
              'status': 'UNAVAILABLE', 'edgeStatus': 'NO_VERIFIED_EDGE',
              'currentProductionModelChanged': False, 'canRaiseSignal': False,
              'networkRequestsMade': 0, 'credentialsUsed': False,
              'privateApiUsed': False, 'adminApiUsed': False,
              'marketReferenceRole': 'EXISTING_PUBLIC_AGGREGATE_NOT_EXECUTABLE_QUOTE',
              'rawMarketPriceNormalized': False, 'verifiedMarketPremiumPercent': None,
              'reasons': [], 'packages': [], 'cadence': cadence(history, now)}
    if not isinstance(market, dict) or market.get('source') != 'NICEHASH_PUBLIC_MARKET' or any(
            market.get(k) is not False for k in ('credentials_used', 'private_api_used', 'admin_api_used')):
        result['reasons'].append('INVALID_PUBLIC_MARKET_PROVENANCE')
        return result
    at = timestamp(market.get('collected_at'))
    if at is None or not 0 <= (now - at).total_seconds() <= MAX_MARKET_AGE_SECONDS:
        result['reasons'].append('STALE_OR_FUTURE_MARKET_RECEIPT')
        return result
    result['marketObservedAt'] = at.isoformat()
    algos = market.get('algorithms')
    scrypt = algos.get('SCRYPT') if isinstance(algos, dict) else None
    if not isinstance(scrypt, dict) or positive(scrypt.get('priceRaw')) is None:
        result['reasons'].append('SCRYPT_PUBLIC_REFERENCE_MISSING')
        return result
    # Do not copy arbitrary raw payload fields or publish order/account identifiers.
    result['marketPriceRaw'] = positive(scrypt.get('priceRaw'))
    if not isinstance(feed, dict):
        result['reasons'].append('BUY_FEED_MISSING')
        return result
    quote_at = timestamp(feed.get('checked_at'))
    if quote_at is None:
        result['reasons'].append('QUOTE_TIME_MISSING_OR_NAIVE')
        return result
    age = (at - quote_at).total_seconds()
    result.update(sourceQuoteAt=quote_at.isoformat(), quoteAgeAtMarketReceiptSeconds=age)
    # Latest local feed only; never fall back to a favourable older quote.
    if not 0 <= age <= MAX_QUOTE_AGE_SECONDS:
        result['reasons'].append('STALE_QUOTE_OR_QUOTE_AFTER_MARKET_RECEIPT')
        return result
    if (feed.get('ok') is not True or feed.get('status') != 'BUY FEED OK'
            or feed.get('market_status') != 'MARKET OK' or feed.get('upstream_status') != 200):
        result['reasons'].append('UNHEALTHY_BUY_FEED')
        return result
    version = feed.get('relay_version')
    if not isinstance(version, str) or not version or len(version) > 48:
        result['reasons'].append('RELAY_VERSION_MISSING_OR_INVALID')
        return result
    prices = feed.get('market_prices_eur')
    prices = prices if isinstance(prices, dict) else {}
    fx = {}
    for coin in ('BTC', 'LTC', 'DOGE'):
        p = prices.get(coin)
        p = p if isinstance(p, dict) else {}
        price, source_age = positive(p.get('eur')), as_float(p.get('age_seconds'))
        if (p.get('fresh') is not True or price is None or source_age is None
                or not 0 <= source_age + age <= MAX_QUOTE_AGE_SECONDS or source_age < 0):
            result['reasons'].append('MISSING_OR_STALE_FX_' + coin)
            return result
        fx[coin] = {'eur': price, 'sourceAgeSeconds': source_age,
                    'ageAtMarketReceiptSeconds': age + source_age,
                    'provider': p.get('provider') if p.get('provider') in ('Kraken', 'CoinPaprika') else 'OTHER_PUBLIC_FEED'}
    for coin in fx:
        fx[coin]['btc'] = finite(fx[coin]['eur'] / fx['BTC']['eur'])
        if fx[coin]['btc'] is None or fx[coin]['btc'] <= 0:
            result['reasons'].append('INVALID_FX_RATIO')
            return result
    result.update(relayVersion=version, fx=fx,
                  formula='sum(1e12 * 86400 / (difficulty * 2^32) * block_reward_field * coin_btc)',
                  assumptions=['Constant reported difficulty over the quoted ticket duration.',
                               'Quoted work is not measured delivered work.',
                               'Upstream reward fee basis and difficulty convention are not independently verified.',
                               'No extra 3% deduction: avoids possible double-counting of pool fees.',
                               'This is not the 24h Admin PRICE formula and not a net-profit guarantee.'])
    packages = feed.get('packages')
    if not isinstance(packages, list):
        result['reasons'].append('PACKAGE_LIST_MISSING')
        return result
    chosen = [p for p in packages if isinstance(p, dict) and p.get('name') in PACKAGES]
    counts = Counter(p['name'] for p in chosen)
    for name in sorted(PACKAGES):
        if counts[name] != 1:
            result['packages'].append({'package': name, 'status': 'UNAVAILABLE',
                                       'reasons': ['MISSING_OR_DUPLICATE_PACKAGE'], 'canRaiseSignal': False})
        else:
            result['packages'].append(package_economics(next(p for p in chosen if p['name'] == name), fx))
    if any(p['status'] == 'CONDITIONAL_MODEL_ONLY' for p in result['packages']):
        result['status'] = 'CONDITIONAL_MODEL_ONLY'
    else:
        result['reasons'].append('NO_USABLE_PALLADIUM_QUOTE')
    # Repeated feed observations have the same key. Consumers must deduplicate it;
    # different public market receipts do not create independent mining trials.
    identity = {'quoteAt': result['sourceQuoteAt'], 'relayVersion': version,
                'fx': {k: v['eur'] for k, v in fx.items()}, 'packages': result['packages']}
    result['quoteFingerprint'] = digest(identity)
    return result


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(text, encoding='utf-8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed', type=Path, default=Path('buy-feed.json'))
    parser.add_argument('--history', type=Path, default=Path('calibration/public-market-history.jsonl'))
    parser.add_argument('--report', type=Path, default=Path('research/scrypt-economics-latest.json'))
    parser.add_argument('--now')
    args = parser.parse_args()
    if len({p.resolve() for p in (args.feed, args.history, args.report)}) != 3:
        parser.error('Feed, history and report must be distinct')
    now = timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error('Aware UTC cutoff required')
    before = args.history.read_bytes()
    rows = [json.loads(line) for line in before.splitlines() if line.strip()]
    if not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError('Valid existing public market history required')
    # Refuse to rewrite an old archive as if it were a new observation.
    at = timestamp(rows[-1].get('collected_at'))
    if at is None or not 0 <= (now - at).total_seconds() <= MAX_MARKET_AGE_SECONDS:
        raise ValueError('Run immediately after the existing public market collector')
    try:
        feed_bytes = args.feed.read_bytes()
        feed = json.loads(feed_bytes)
    except (OSError, ValueError):
        feed_bytes, feed = None, None
    result = build_economics(feed, rows[-1], now, rows)
    if args.history.read_bytes() != before or (feed_bytes is not None and args.feed.read_bytes() != feed_bytes):
        raise RuntimeError('Source changed during enrichment')
    rows[-1]['scryptEconomics'] = result
    atomic_write(args.history, ''.join(json.dumps(r, separators=(',', ':'), allow_nan=False) + '\n' for r in rows))
    atomic_write(args.report, json.dumps(result, indent=2, allow_nan=False) + '\n')
    print('SCRYPT ECONOMICS:', result['status'], '|', result['edgeStatus'])
    print('Public/archive only; CURRENT unchanged; no new API calls')
    for p in result['packages']:
        print(p['package'], p['status'], '| conditional return:', p.get('conditionalExpectedReturnPercent'))


if __name__ == '__main__':
    main()
