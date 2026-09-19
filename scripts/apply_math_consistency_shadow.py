"""Conditional model reconciliation. No CURRENT or purchase-threshold changes."""
import copy
import json
import math
import os
from pathlib import Path

TWO32 = float(2 ** 32)
BDIFF_ONE_TARGET = 0xffff << 208
BDIFF_WORK_FACTOR = float(2 ** 256 / BDIFF_ONE_TARGET)
SUPPORTED_ALGORITHMS = {'SCRYPT', 'SHA256ASICBOOST', 'SHA256ASICBOOST_USDT'}
SUPPORTED_COINS = {'SCRYPT': {'LTC', 'DOGE'}, 'SHA256ASICBOOST': {'BTC', 'BCH'}, 'SHA256ASICBOOST_USDT': {'BTC', 'BCH'}}
PASS_MAX_DIFF_PERCENT = 5.0
WARNING_MAX_DIFF_PERCENT = 15.0


def as_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def positive(value):
    n = as_float(value)
    return n if n is not None and n > 0 else None


def expected_blocks_from_difficulty(hashrate_hps, duration_s, difficulty):
    h, t, d = map(positive, (hashrate_hps, duration_s, difficulty))
    if None in (h, t, d):
        return None
    try:
        n = h / d * (t / TWO32)
        return n if math.isfinite(n) and n > 0 else None
    except (OverflowError, ZeroDivisionError):
        return None


def chain_check(package, chain):
    algorithm = str(chain.get('algorithm') or '').upper()
    coin = str(chain.get('currency') or '').upper()
    if coin not in SUPPORTED_COINS.get(algorithm, set()):
        return {'status': 'NOT_APPLICABLE', 'algorithm': algorithm or None,
                'reason': 'Difficulty convention not verified for this coin/algorithm pair.'}
    h, duration, difficulty, supplied = map(positive, (
        package.get('package_hashrate_hps'), package.get('duration_seconds'),
        chain.get('network_difficulty'), chain.get('expected_blocks')))
    expected = expected_blocks_from_difficulty(h, duration, difficulty)
    if expected is None or supplied is None:
        return {'status': 'UNKNOWN', 'algorithm': algorithm,
                'reason': 'Inputs must be finite and strictly positive.'}
    difference = 100 * (supplied / expected - 1)
    if not math.isfinite(difference):
        return {'status': 'UNKNOWN', 'algorithm': algorithm, 'reason': 'Numeric overflow.'}
    status = 'PASS' if abs(difference) <= PASS_MAX_DIFF_PERCENT else 'WARNING' if abs(difference) <= WARNING_MAX_DIFF_PERCENT else 'CRITICAL'
    result = {
        'status': status, 'algorithm': algorithm,
        'feedExpectedBlocks': supplied, 'difficultyExpectedBlocks': expected,
        'signedDifferencePercent': round(difference, 6), 'absoluteDifferencePercent': round(abs(difference), 6),
        'difficulty': difficulty, 'formula': 'hashrate_hps * duration_seconds / (difficulty * 2^32)',
        'differenceKind': 'MODEL_DISAGREEMENT_NOT_A_VERIFIED_DATA_ERROR',
        'difficultyConvention': 'CONDITIONAL_BDIFF_APPROXIMATION',
        'difficultyConventionVerifiedForUpstreamField': False,
        'bdiffRefinedExpectedBlocks': expected * TWO32 / BDIFF_WORK_FACTOR,
        'constantApproximationErrorPercent': (BDIFF_WORK_FACTOR / TWO32 - 1) * 100,
        'externalInputsIndependentlyVerified': False,
        'decomposition': {'status': 'MISSING_NETWORK_INPUTS'},
    }
    network, target_time = map(positive, (chain.get('network_hashpower_hps'), chain.get('block_time_seconds')))
    if network is not None and target_time is not None:
        implied_time = difficulty / network * TWO32
        network_expected = h / network * duration / target_time
        ratio = implied_time / target_time
        if all(math.isfinite(n) and n > 0 for n in (implied_time, network_expected, ratio)):
            residual = 100 * (supplied / network_expected - 1)
            result['decomposition'] = {
                'status': 'EXPLAINED_BY_TARGET_TIME_MODEL' if abs(residual) <= 0.01 else 'FEED_FORMULA_NOT_RECONCILED',
                'targetBlockTimeSeconds': target_time,
                'impliedBlockTimeSeconds': implied_time,
                'networkHashrateDerivedExpectedBlocks': network_expected,
                'feedVsNetworkFormulaPercent': residual,
                'networkVsDifficultyMultiplier': ratio,
                'identity': 'lambda_H/lambda_D = (D*2^32/H_network)/T_target',
                'impliedIntervalIsObservedBlockInterval': False,
                'independentValidation': False,
            }
    return result


def overall_status(checks):
    statuses = {x.get('status') for x in checks}
    for status in ('CRITICAL', 'WARNING', 'UNKNOWN', 'PASS'):
        if status in statuses:
            return status
    return 'NOT_APPLICABLE'


def annotate(feed):
    if not isinstance(feed, dict) or feed.get('status') != 'BUY FEED OK' or feed.get('ok') is not True:
        raise ValueError('Refusing unhealthy BUY feed')
    packages = feed.get('packages')
    if not isinstance(packages, list) or not packages or any(not isinstance(p, dict) for p in packages):
        raise ValueError('Invalid package list')
    original = copy.deepcopy(feed)
    summary = dict.fromkeys(('PASS', 'WARNING', 'CRITICAL', 'UNKNOWN', 'NOT_APPLICABLE'), 0)
    for package in packages:
        checks = []
        for key, label in (('primary_chain', 'PRIMARY'), ('merge_chain', 'MERGE')):
            chain = package.get(key)
            if isinstance(chain, dict) and chain:
                check = chain_check(package, chain)
                check['chain'] = label
                checks.append(check)
        status = overall_status(checks)
        summary[status] += 1
        package['math_consistency_shadow'] = {
            'model_version': 2, 'status': status, 'production_override': False, 'checks': checks,
            'thresholds': {'passMaxAbsoluteDifferencePercent': PASS_MAX_DIFF_PERCENT,
                           'warningMaxAbsoluteDifferencePercent': WARNING_MAX_DIFF_PERCENT},
            'policy': 'Conditional consistency only, not proof of source truth or pricing edge. CURRENT and existing disagreement thresholds are unchanged.',
        }
    feed['math_consistency_shadow'] = {'model_version': 2, 'model_use': 'MATH_CONSISTENCY_AUDIT_ONLY',
        'production_model': 'CURRENT', 'production_model_changed': False,
        'supported_algorithms': sorted(SUPPORTED_ALGORITHMS), 'summary': summary}
    def without_annotations(data):
        data = copy.deepcopy(data)
        data.pop('math_consistency_shadow', None)
        for p in data['packages']:
            p.pop('math_consistency_shadow', None)
        return data
    if without_annotations(original) != without_annotations(feed):
        raise RuntimeError('Unexpected production field mutation')
    return feed


def main():
    path = Path(os.getenv('BUY_RADAR_FEED', 'buy-feed.json'))
    feed = annotate(json.loads(path.read_text(encoding='utf-8')))
    text = json.dumps(feed, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    path.write_text(text, encoding='utf-8')
    print('MATH CONSISTENCY SHADOW APPLIED', feed['math_consistency_shadow']['summary'])
    print('CURRENT unchanged; decomposition is conditional, not independent evidence')


if __name__ == '__main__':
    main()
