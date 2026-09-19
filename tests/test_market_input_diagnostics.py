import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import apply_math_consistency_shadow as m
import collect_public_market_history as c
import diagnose_market_inputs as d

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def package():
    return {'name': 'Palladium S', 'currency_market': 'BTC', 'final_signal': 'GOOD',
        'price_btc_equiv': .0001, 'duration_seconds': 600, 'package_hashrate_hps': m.TWO32,
        'primary_chain': {'currency': 'LTC', 'algorithm': 'SCRYPT', 'network_difficulty': 600,
            'network_hashpower_hps': m.TWO32*4, 'block_time_seconds': 150, 'expected_blocks': 1, 'block_reward': 1},
        'profitability': {'expected_return_percent': 95}, 'decision': {'final_signal': 'GOOD'}}


def feed():
    return {'status': 'BUY FEED OK', 'ok': True, 'checked_at': NOW.isoformat(), 'relay_version': '2.9.0',
        'packages': [package()], 'market_prices_eur': {'BTC': {'eur': 100000, 'fresh': True}, 'LTC': {'eur': 100, 'fresh': True}}}


def registry(name='KHEAVYHASH', currency='BTC'):
    return {'algorithm': name, 'enabledMarkets': currency, 'marketFactor': '1000000000000000',
        'priceFactor': '1000000000000000000', 'displayMarketFactor': 'PH', 'displayPriceFactor': 'EH', 'priceScale': 8}


def settings(name='KHEAVYHASH'):
    return {'name': name, 'speedUnit': 'PH'}


def market(at=NOW):
    return {'collected_at': at.isoformat(), 'source': 'NICEHASH_PUBLIC_MARKET', 'credentials_used': False,
        'private_api_used': False, 'admin_api_used': False,
        'algorithms': {'SCRYPT': {'priceRaw': 2, 'speedRaw': 3, 'volumeRaw': 6}}}


class DiagnosticsTests(unittest.TestCase):
    def test_01_finite_values(self):
        for v in (True, float('nan'), float('inf'), 'nan', None):
            self.assertIsNone(m.as_float(v))
        self.assertEqual(m.as_float('1.25'), 1.25)

    def test_02_invalid_work_unknown(self):
        for v in (0, -1, float('nan'), True):
            p = package(); p['package_hashrate_hps'] = v
            self.assertEqual(m.chain_check(p, p['primary_chain'])['status'], 'UNKNOWN')

    def test_03_difficulty_formula(self):
        self.assertEqual(m.expected_blocks_from_difficulty(m.TWO32, 600, 600), 1)

    def test_04_same_network_and_difficulty_model(self):
        p = package(); x = m.chain_check(p, p['primary_chain'])
        self.assertEqual(x['status'], 'PASS')
        self.assertEqual(x['decomposition']['impliedBlockTimeSeconds'], 150)
        self.assertEqual(x['decomposition']['status'], 'EXPLAINED_BY_TARGET_TIME_MODEL')

    def test_05_network_drop_is_not_more_success_per_hash(self):
        p = package(); a = m.chain_check(p, p['primary_chain'])
        p['primary_chain']['network_hashpower_hps'] /= 2
        p['primary_chain']['expected_blocks'] *= 2
        b = m.chain_check(p, p['primary_chain'])
        self.assertEqual(a['difficultyExpectedBlocks'], b['difficultyExpectedBlocks'])
        self.assertEqual(b['decomposition']['networkVsDifficultyMultiplier'], 2)
        self.assertEqual(b['status'], 'CRITICAL')

    def test_06_reported_lambda_mismatch_separate(self):
        p = package(); p['primary_chain']['expected_blocks'] = 1.2
        self.assertEqual(m.chain_check(p,p['primary_chain'])['decomposition']['status'], 'FEED_FORMULA_NOT_RECONCILED')

    def test_07_nonapplicable_not_bdiff(self):
        for coin, algo in [('KAS','KHEAVYHASH'),('ZEC','EQUIHASH'),('UNKNOWN','SCRYPT')]:
            p = package(); p['primary_chain'].update(currency=coin, algorithm=algo)
            self.assertEqual(m.chain_check(p,p['primary_chain'])['status'], 'NOT_APPLICABLE')

    def test_08_constant_not_twenty_percent_error(self):
        p = package(); x = m.chain_check(p,p['primary_chain'])
        self.assertLess(x['constantApproximationErrorPercent'], .002)
        self.assertGreater(x['constantApproximationErrorPercent'], .001)

    def test_09_preserve_current_and_all_other_fields(self):
        f = feed(); old = copy.deepcopy(f); new = m.annotate(f)
        new.pop('math_consistency_shadow')
        new['packages'][0].pop('math_consistency_shadow')
        self.assertEqual(old, new)

    def test_10_missing_h_not_fabricated(self):
        p = package(); p['primary_chain'].pop('network_hashpower_hps')
        self.assertEqual(m.chain_check(p,p['primary_chain'])['decomposition']['status'], 'MISSING_NETWORK_INPUTS')

    def test_11_historical_formula_signs(self):
        for difficulty, network, target in [(88389132.60279,2888325039005800,150),(44255305.03469,2531918833764449,60)]:
            p = package(); p['primary_chain'].update(network_difficulty=difficulty, network_hashpower_hps=network, block_time_seconds=target,
                expected_blocks=p['package_hashrate_hps']/network*p['duration_seconds']/target)
            x = m.chain_check(p,p['primary_chain'])
            self.assertAlmostEqual(x['signedDifferencePercent'], (x['decomposition']['impliedBlockTimeSeconds']/target-1)*100, places=5)

    def test_12_price_and_speed_units_separate(self):
        x = c.unit_contract(settings(), registry(), NOW.isoformat())
        self.assertEqual(x['priceToSpeedDisplayFactor'], 1000)
        self.assertTrue(x['differentPriceAndSpeedUnits'])
        self.assertEqual(x['priceDisplayUnit'], 'EH')

    def test_13_format_precision_not_raw_denomination(self):
        x = c.unit_contract(settings(), registry(), NOW.isoformat())
        self.assertFalse(x['rawPriceDenominationVerified'])
        self.assertIsNone(x['nativePerRawPriceUnit'])
        self.assertFalse(x['executableMarketPriceVerified'])

    def test_14_currency_conflict_rejected(self):
        x = c.unit_contract(settings('SHA256ASICBOOST_USDT'), registry('SHA256ASICBOOST_USDT', 'BTC'), NOW.isoformat())
        self.assertEqual(x['status'], 'CONFLICTING_CURRENCY_OR_ALGORITHM')
        self.assertIsNone(x['currencyMarket'])

    def test_15_missing_registry_unavailable(self):
        self.assertEqual(c.unit_contract(settings(), {}, NOW.isoformat())['status'], 'UNAVAILABLE')

    def test_16_speed_conflict(self):
        s = settings(); s['speedUnit'] = 'TH'
        self.assertEqual(c.unit_contract(s, registry(), NOW.isoformat())['status'], 'CONFLICTING_SPEED_UNIT')

    def test_17_nonfinite_registry(self):
        item = registry(); item['priceFactor'] = 'inf'
        self.assertEqual(c.unit_contract(settings(), item, NOW.isoformat())['status'], 'INVALID_REGISTRY')

    def test_18_public_allowlist_checked_before_network(self):
        with patch.object(c.urllib.request, 'build_opener', side_effect=AssertionError('network called')):
            with self.assertRaises(ValueError):
                c.get_json('/not-allowlisted')

    def test_19_redirect_denied(self):
        with self.assertRaises(RuntimeError):
            c.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com/')

    def test_20_snapshot_safe_projection(self):
        info = {'miningAlgorithms':[{'name':'KHeavyHash','algo':62,'speed_text':'PH','multi':.000001,'price_multi':.000000001}]}
        stats = {'algos':[{'a':62,'s':3,'p':2,'v':6,'o':1,'r':1,'unexpectedUserData':'DO_NOT_KEEP'}]}
        x = c.snapshot(info,stats,{'miningAlgorithms':[registry()]},NOW.isoformat())
        self.assertNotIn('DO_NOT_KEEP',json.dumps(x))
        self.assertFalse(x['credentials_used'])
        self.assertEqual(x['algorithms']['KHEAVYHASH']['priceRaw'],2)

    def test_21_volume_dependency_not_independent(self):
        x = d.summarize_market([market()], NOW)[0]
        self.assertTrue(x['volumeEqualsPriceTimesSpeed'])
        self.assertFalse(x['volumeIsIndependentEvidence'])
        self.assertFalse(x['rawPriceDenominationVerified'])
        self.assertIsNone(x['absoluteDiscountPercent'])

    def test_22_invalid_volume_no_identity(self):
        for v in (None, float('nan'), 0, True):
            self.assertIsNone(d.price_identity({'priceRaw':1,'speedRaw':1,'volumeRaw':v}))

    def test_23_historical_no_size_double_count(self):
        f = feed(); f['packages'].append(copy.deepcopy(f['packages'][0]))
        row = {'collected_at':NOW.isoformat(),'feed':f}
        r = d.build_report(f,[row,row],[],NOW)
        self.assertEqual(r['historicalChainReconciliation'][0]['uniqueChainTimestamps'],1)

    def test_24_future_archive_excluded(self):
        row = {'collected_at':(NOW+timedelta(minutes=1)).isoformat(),'feed':feed()}
        self.assertEqual(d.build_report(feed(),[row],[],NOW)['historicalChainReconciliation'],[])

    def test_25_conditional_ev_not_truth(self):
        f = feed(); x = d.package_diagnostics(f['packages'][0], f)
        self.assertAlmostEqual(x['conditionalDifficultyExpectedReturnPercent'],1000)
        self.assertFalse(x['conditionalEstimateIsVerifiedReturn'])

    def test_26_partial_merge_cannot_be_complete_ev(self):
        f = feed(); f['packages'][0]['merge_chain'] = {'currency':'DOGE','algorithm':'SCRYPT'}
        x = d.package_diagnostics(f['packages'][0],f)
        self.assertIsNone(x['conditionalDifficultyExpectedReturnPercent'])

    def test_27_stale_fx_blocks_conditional_ev(self):
        f = feed(); f['market_prices_eur']['LTC']['fresh'] = False
        self.assertIsNone(d.package_diagnostics(f['packages'][0],f)['conditionalDifficultyExpectedReturnPercent'])

    def test_28_conflicting_network_history_rejected(self):
        f = feed(); p = copy.deepcopy(f['packages'][0]); p['primary_chain']['network_hashpower_hps'] *= 2
        f['packages'].append(p)
        r = d.build_report(f,[{'collected_at':NOW.isoformat(),'feed':f}],[],NOW)
        self.assertEqual(r['conflictingChainTimestamps'],1)
        self.assertEqual(r['historicalChainReconciliation'],[])

    def test_29_no_decision_or_input_mutation(self):
        f = feed(); old = copy.deepcopy(f)
        r = d.build_report(f,[],[],NOW)
        self.assertEqual(f,old)
        self.assertFalse(r['canRaiseSignal']); self.assertFalse(r['currentProductionModelChanged'])

    def test_30_cli_immutable_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); f,s,mar,out = [root/n for n in ('feed.json','snap.jsonl','market.jsonl','report.json')]
            f.write_text(json.dumps(feed())); s.write_text(''); mar.write_text(json.dumps(market())+'\n')
            before = [p.read_bytes() for p in (f,s,mar)]
            subprocess.run([sys.executable,str(ROOT/'scripts/diagnose_market_inputs.py'),'--feed',str(f),'--snapshots',str(s),'--market',str(mar),'--output',str(out),'--now',NOW.isoformat()],check=True,capture_output=True)
            self.assertEqual(before,[p.read_bytes() for p in (f,s,mar)])
            self.assertEqual(json.loads(out.read_text())['networkRequestsMade'],0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
