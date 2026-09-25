"""Synthetic offline tests; fixtures are NOT observations or profitability evidence."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import enrich_scrypt_market_history as module

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def fixture():
    # One conditional block per TH-day on each chain, artificial prices/rewards.
    difficulty = 1e12 * 86400 / 2**32
    chain = lambda coin, reward: dict(currency=coin, algorithm='SCRYPT',
        network_difficulty=difficulty, block_reward=reward,
        block_reward_with_nh_fee=reward, expected_blocks=0.1,
        network_hashpower_hps=1e12 * 86400 / 60, block_time_seconds=60)
    m = dict(name='Palladium M', currency_market='BTC', available=True,
        price_native=0.001, price_btc=0.001, price_btc_equiv=0.001,
        package_hashrate_hps=1e12, duration_seconds=8640,
        primary_chain=chain('LTC', 2), merge_chain=chain('DOGE', 60),
        final_signal='WAIT')
    s = copy.deepcopy(m)
    s.update(name='Palladium S', price_native=0.0001, price_btc=0.0001,
             price_btc_equiv=0.0001, package_hashrate_hps=1e11)
    for key in ('primary_chain', 'merge_chain'):
        s[key]['expected_blocks'] = 0.01
    feed = dict(checked_at=(NOW - timedelta(seconds=60)).isoformat(),
        relay_version='synthetic-test-v1', ok=True, status='BUY FEED OK',
        upstream_status=200, market_status='MARKET OK',
        market_prices_eur={c: dict(eur=p, provider='Kraken', fresh=True, age_seconds=0)
                           for c, p in [('BTC', 1000), ('LTC', 1), ('DOGE', 0.1)]},
        packages=[s, m])
    market = dict(collected_at=NOW.isoformat(), source='NICEHASH_PUBLIC_MARKET',
        credentials_used=False, private_api_used=False, admin_api_used=False,
        schemaVersion=2, algorithms={'SCRYPT': {'priceRaw': 123.456,
        'unitContract': {'rawPriceDenominationVerified': False}}})
    return feed, market


class ScryptEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.feed, self.market = fixture()

    def build(self):
        return module.build_economics(self.feed, self.market, NOW, [self.market])

    def m(self, report=None):
        return next(p for p in (report or self.build())['packages'] if p['package'] == 'Palladium M')

    def test_units_ev_and_premium(self):
        p = self.m()
        self.assertAlmostEqual(p['quoteWorkThDays'], 0.1)
        self.assertAlmostEqual(p['quoteWorkThHours'], 2.4)
        self.assertAlmostEqual(p['quoteImpliedCostBtcPerThDay'], 0.01)
        self.assertAlmostEqual(p['conditionalMergedValueBtcPerThDay'], 0.008)
        self.assertAlmostEqual(p['conditionalQuotePremiumPercent'], 25)
        self.assertAlmostEqual(p['conditionalExpectedReturnPercent'], 80)
        self.assertAlmostEqual(p['conditionalExpectedRewardBtc'], 0.0008)

    def test_no_raw_market_conversion_or_buy_even_when_model_positive(self):
        self.feed['packages'][1]['merge_chain']['block_reward'] = 600
        result = self.build()
        self.assertGreater(self.m(result)['conditionalExpectedReturnPercent'], 100)
        self.assertEqual(result['edgeStatus'], 'NO_VERIFIED_EDGE')
        self.assertFalse(result['canRaiseSignal'])
        self.assertIsNone(result['verifiedMarketPremiumPercent'])
        self.assertFalse(result['rawMarketPriceNormalized'])
        self.assertIsNone(self.m(result)['verifiedNetExpectedReturnPercent'])
        self.assertFalse(self.m(result)['additionalPoolFeeApplied'])

    def test_source_is_not_mutated(self):
        before = copy.deepcopy((self.feed, self.market))
        self.build()
        self.assertEqual(before, (self.feed, self.market))

    def test_stale_quote_no_estimate(self):
        self.feed['checked_at'] = (NOW - timedelta(seconds=421)).isoformat()
        self.assertEqual(self.build()['packages'], [])
        self.assertIn('STALE_QUOTE_OR_QUOTE_AFTER_MARKET_RECEIPT', self.build()['reasons'])

    def test_future_quote_not_joined_to_older_market(self):
        self.feed['checked_at'] = (NOW + timedelta(seconds=1)).isoformat()
        self.assertEqual(self.build()['status'], 'UNAVAILABLE')

    def test_naive_quote_rejected(self):
        self.feed['checked_at'] = '2026-09-25T11:59:00'
        self.assertEqual(self.build()['status'], 'UNAVAILABLE')

    def test_market_time_and_provenance(self):
        for seconds in (-121, 1):
            self.market['collected_at'] = (NOW + timedelta(seconds=seconds)).isoformat()
            self.assertEqual(self.build()['packages'], [])
        self.market['collected_at'] = NOW.isoformat()
        self.market['credentials_used'] = True
        self.assertEqual(self.build()['packages'], [])

    def test_missing_scrypt_reference(self):
        self.market['algorithms'] = {}
        self.assertIn('SCRYPT_PUBLIC_REFERENCE_MISSING', self.build()['reasons'])

    def test_fx_age_includes_age_of_saved_feed(self):
        self.feed['market_prices_eur']['DOGE']['age_seconds'] = 361
        self.assertIn('MISSING_OR_STALE_FX_DOGE', self.build()['reasons'])

    def test_unfresh_fx_and_invalid_numbers(self):
        for value in (0, -1, float('nan'), float('inf'), True, None):
            with self.subTest(value=value):
                self.feed, self.market = fixture()
                self.feed['market_prices_eur']['BTC']['eur'] = value
                self.assertEqual(self.build()['packages'], [])
        self.feed, self.market = fixture()
        self.feed['market_prices_eur']['DOGE']['fresh'] = False
        self.assertEqual(self.build()['packages'], [])

    def test_usdt_or_cost_conflict_does_not_use_btc_equivalent(self):
        p = self.feed['packages'][1]
        p['currency_market'] = 'USDT'
        self.assertEqual(self.m()['status'], 'UNAVAILABLE')
        p['currency_market'] = 'BTC'
        p['price_btc_equiv'] = 0.002
        self.assertEqual(self.m()['status'], 'UNAVAILABLE')

    def test_invalid_work_and_difficulty(self):
        for field in ('package_hashrate_hps', 'duration_seconds', 'price_native'):
            self.feed, self.market = fixture()
            self.feed['packages'][1][field] = 0
            self.assertEqual(self.m()['status'], 'UNAVAILABLE')
        self.feed, self.market = fixture()
        self.feed['packages'][1]['primary_chain']['network_difficulty'] = float('nan')
        self.assertEqual(self.m()['status'], 'UNAVAILABLE')

    def test_missing_or_wrong_merged_chain_not_silently_omitted(self):
        self.feed['packages'][1]['merge_chain'] = None
        self.assertEqual(self.m()['status'], 'UNAVAILABLE')
        self.feed, self.market = fixture()
        self.feed['packages'][1]['merge_chain']['currency'] = 'LTC'
        self.assertEqual(self.m()['status'], 'UNAVAILABLE')

    def test_duplicate_packages_rejected_not_double_counted(self):
        self.feed['packages'].append(copy.deepcopy(self.feed['packages'][1]))
        self.assertIn('MISSING_OR_DUPLICATE_PACKAGE', self.m()['reasons'])

    def test_model_disagreement_preserved(self):
        self.feed['packages'][1]['primary_chain']['expected_blocks'] *= 2
        p = self.m()
        self.assertFalse(p['sourceMathClear'])
        self.assertEqual(p['chains'][0]['sourceMathStatus'], 'CRITICAL')
        self.assertFalse(p['upstreamDifficultyConventionVerified'])

    def test_s_and_m_separate_and_scaled(self):
        result = self.build()
        s = next(p for p in result['packages'] if p['package'] == 'Palladium S')
        self.assertEqual(s['priority'], 'PRIMARY')
        self.assertEqual(self.m()['priority'], 'SECONDARY')
        self.assertAlmostEqual(s['conditionalExpectedRewardBtc'] * 10, self.m()['conditionalExpectedRewardBtc'])

    def test_no_account_fields_propagated(self):
        self.feed['private_token'] = 'DO_NOT_COPY_SENTINEL'
        self.feed['packages'][1]['customer_id'] = 'DO_NOT_COPY_SENTINEL'
        self.assertNotIn('DO_NOT_COPY_SENTINEL', json.dumps(self.build()))

    def test_repeated_quote_fingerprint_stable_across_receipts(self):
        first = self.build()['quoteFingerprint']
        later = NOW + timedelta(seconds=60)
        self.market['collected_at'] = later.isoformat()
        result = module.build_economics(self.feed, self.market, later)
        self.assertEqual(first, result['quoteFingerprint'])

    def test_cadence_reports_actual_gaps(self):
        rows = [{'collected_at': (NOW - timedelta(seconds=n)).isoformat()} for n in (1500, 1200, 0, 0)]
        c = module.cadence(rows, NOW)
        self.assertEqual(c['samplesLast24h'], 3)
        self.assertEqual(c['medianGapSeconds'], 750)
        self.assertEqual(c['gapsOver10Minutes'], 1)
        self.assertFalse(c['exactCadenceGuaranteed'])

    def test_cli_only_changes_last_market_record_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            feed, hist, report = tmp/'feed.json', tmp/'history.jsonl', tmp/'report.json'
            feed.write_text(json.dumps(self.feed))
            original = feed.read_bytes()
            previous = copy.deepcopy(self.market)
            previous['collected_at'] = (NOW - timedelta(minutes=5)).isoformat()
            hist.write_text(json.dumps(previous) + '\n' + json.dumps(self.market) + '\n')
            subprocess.run([sys.executable, str(Path(module.__file__)), '--feed', str(feed),
                            '--history', str(hist), '--report', str(report), '--now', NOW.isoformat()],
                           check=True, capture_output=True)
            rows = [json.loads(line) for line in hist.read_text().splitlines()]
            self.assertEqual(rows[0], previous)
            self.assertEqual(feed.read_bytes(), original)
            self.assertEqual(rows[-1]['scryptEconomics'], json.loads(report.read_text()))
            enriched = rows[-1].pop('scryptEconomics')
            self.assertEqual(rows[-1], self.market)
            self.assertFalse(enriched['currentProductionModelChanged'])

    def test_cli_refuses_source_output_alias(self):
        proc = subprocess.run([sys.executable, str(Path(module.__file__)),
                               '--feed', 'same.json', '--report', 'same.json'], capture_output=True)
        self.assertNotEqual(proc.returncode, 0)


if __name__ == '__main__':
    unittest.main()
