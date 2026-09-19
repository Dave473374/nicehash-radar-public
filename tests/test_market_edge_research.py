"""Synthetic, offline regressions for pricing-lag research; no account access."""
import copy
from collections import Counter
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('research', ROOT/'scripts/build_market_edge_research.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def quote(at=NOW, currency='BTC', name='Palladium S', size='S', version='2.9.0', observed=None):
    algo, coin = ('SCRYPT', 'LTC') if currency == 'BTC' else ('SHA256ASICBOOST', 'BCH')
    p = {'name': name, 'size': size, 'currency_market': currency, 'available': True,
         'price_native': 0.0001 if currency == 'BTC' else 5, 'duration_seconds': 3600,
         'package_hashrate_hps': 1e12, 'primary_chain': {'currency': coin, 'algorithm': algo, 'network_difficulty': 1e8},
         'profitability': {'expected_return_percent': 96}, 'economics': {'package_cost_eur': 7},
         'final_signal': 'GOOD', 'math_consistency_shadow': {'status': 'PASS'}}
    return {'collected_at': (observed or at).isoformat(), 'feed_generated_at': at.isoformat(),
            'feed': {'ok': True, 'status': 'BUY FEED OK', 'relay_version': version, 'checked_at': at.isoformat(), 'packages': [p]}}


def market(at=NOW, name='SCRYPT', price=1, unit='TH'):
    return {'collected_at': at.isoformat(), 'source': 'NICEHASH_PUBLIC_MARKET', 'credentials_used': False,
            'private_api_used': False, 'admin_api_used': False,
            'algorithms': {name: {'priceRaw': price, 'speedRaw': 1e15, 'orders': 80, 'speedUnit': unit, 'multi': .001, 'priceMulti': .001}}}


def points(qs=None, ms=None):
    counts = Counter()
    qs = r.collect_quotes(qs or [quote()], NOW, counts)
    ms = r.market_index(ms or [market()], NOW, counts)
    return r.pair_quotes(qs, ms, counts), counts


class ResearchTests(unittest.TestCase):
    def test_01_numbers(self):
        for x in (True, False, 'nan', float('inf'), -1):
            self.assertIsNone(r.numeric(x, True))
        self.assertEqual(r.numeric('0.25', True), .25)

    def test_02_aware_time_required(self):
        self.assertIsNone(r.timestamp('2026-09-19T12:00:00'))
        self.assertEqual(r.timestamp('2026-09-19T12:00:00Z'), NOW)

    def test_03_usdt_routing(self):
        self.assertEqual(r.source_algorithm('SHA256ASICBOOST', 'USDT'), 'SHA256ASICBOOST_USDT')
        self.assertIsNone(r.source_algorithm('SCRYPT', 'USDT'))

    def test_04_duplicate_quote_not_new_observation(self):
        q = quote()
        ps, c = points([q, copy.deepcopy(q)])
        self.assertEqual(len(ps), 1)
        self.assertEqual(c['duplicateQuotes'], 1)

    def test_05_quote_conflict_removed(self):
        a, b = quote(), quote()
        b['feed']['packages'][0]['price_native'] *= 2
        ps, c = points([a, b])
        self.assertEqual(ps, [])
        self.assertEqual(c['conflictingQuotes'], 1)

    def test_06_future_quote_excluded(self):
        ps, _ = points([quote(NOW+timedelta(seconds=1))])
        self.assertEqual(ps, [])

    def test_07_future_availability_excluded(self):
        ps, _ = points([quote(observed=NOW+timedelta(seconds=1))])
        self.assertEqual(ps, [])

    def test_08_late_archive_not_fresh(self):
        ps, c = points([quote(NOW-timedelta(hours=2), observed=NOW)])
        self.assertEqual(ps, [])
        self.assertEqual(c['delayedObservations'], 1)

    def test_09_conflicting_timestamp_excluded(self):
        q = quote()
        q['feed_generated_at'] = (NOW-timedelta(minutes=1)).isoformat()
        ps, _ = points([q])
        self.assertEqual(ps, [])

    def test_10_unavailable_not_compared(self):
        q = quote()
        q['feed']['packages'][0]['available'] = False
        ps, _ = points([q])
        self.assertEqual(ps, [])

    def test_11_no_future_market_join(self):
        qs = [quote(NOW-timedelta(minutes=5))]
        ps, _ = points(qs, [market(NOW)])
        self.assertEqual(ps[0]['pairStatus'], 'NO_PAST_MARKET')

    def test_12_pair_age_limit(self):
        ps, _ = points(ms=[market(NOW-timedelta(seconds=601))])
        self.assertEqual(ps[0]['pairStatus'], 'STALE_MARKET')

    def test_13_bad_latest_no_fallback(self):
        a, b = market(NOW-timedelta(minutes=2)), market()
        b['algorithms']['SCRYPT']['priceRaw'] = None
        ps, _ = points(ms=[a, b])
        self.assertEqual(ps[0]['pairStatus'], 'INVALID_MARKET')

    def test_14_market_provenance(self):
        m = market()
        m['credentials_used'] = True
        ps, c = points(ms=[m])
        self.assertEqual(ps[0]['pairStatus'], 'NO_PAST_MARKET')
        self.assertGreater(c['unverifiedMarketProvenance'], 0)

    def test_15_conflicting_market_invalid(self):
        ps, _ = points(ms=[market(price=1), market(price=2)])
        self.assertEqual(ps[0]['pairStatus'], 'INVALID_MARKET')

    def test_16_negative_or_nonfinite_work(self):
        for bad in (-1, True, float('nan')):
            q = quote()
            q['feed']['packages'][0]['package_hashrate_hps'] = bad
            ps, _ = points([q])
            self.assertEqual(ps, [])

    def test_17_usdt_never_btc_work_cost(self):
        ps, _ = points([quote(currency='USDT', name='Silver 5', size='5')], [market(name='SHA256ASICBOOST_USDT')])
        self.assertEqual(ps[0]['workPerNative'], 1e12*3600/5)
        self.assertEqual(ps[0]['currency'], 'USDT')

    def test_legacy_282_btc_quote_uses_price_btc_as_native_price(self):
        q = quote(version='2.8.2')
        p = q['feed']['packages'][0]
        p.pop('currency_market')
        p.pop('price_native')
        p['price_btc'] = 0.0001
        ps, counts = points([q], [market()])
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]['currency'], 'BTC')
        self.assertEqual(ps[0]['currencySource'], 'LEGACY_2_8_2_BTC_ONLY_SCHEMA')
        self.assertEqual(ps[0]['priceNative'], 0.0001)
        self.assertEqual(ps[0]['workPerNative'], 1e12 * 3600 / 0.0001)
        self.assertEqual(counts['invalidPackages'], 0)

    def test_missing_currency_unknown_schema_remains_rejected(self):
        q = quote(version='unknown')
        p = q['feed']['packages'][0]
        p.pop('currency_market')
        p.pop('price_native')
        p['price_btc'] = 0.0001
        ps, counts = points([q], [market()])
        self.assertEqual(ps, [])
        self.assertGreater(counts['invalidPackages'], 0)

    def test_18_unit_change_breaks_series(self):
        ps, _ = points([quote(NOW-timedelta(minutes=5)), quote()], [market(NOW-timedelta(minutes=5)), market(unit='GH')])
        self.assertIsNone(r.transition(*ps))

    def test_19_version_change_breaks_series(self):
        ps, _ = points([quote(NOW-timedelta(minutes=5), version='old'), quote()], [market(NOW-timedelta(minutes=5)), market()])
        self.assertIsNone(r.transition(*ps))

    def test_20_identical_market_cannot_confirm_new_market_change(self):
        ps, _ = points([quote(NOW-timedelta(minutes=5)), quote()], [market(NOW-timedelta(minutes=5))])
        self.assertIsNone(r.transition(*ps))

    def test_21_large_transition_gap_rejected(self):
        ps, _ = points([quote(NOW-timedelta(minutes=25)), quote()], [market(NOW-timedelta(minutes=25)), market()])
        self.assertIsNone(r.transition(*ps))

    def test_22_divergence_not_discount(self):
        ps, _ = points([quote(NOW-timedelta(minutes=5)), quote()], [market(NOW-timedelta(minutes=5)), market(price=1.1)])
        t = r.transition(*ps)
        self.assertEqual(t['ticketCostPerWorkChangePercent'], 0)
        self.assertAlmostEqual(t['marketRawChangePercent'], 10)
        self.assertIn('NOT_PROVEN', t['interpretation'])

    def test_23_prior_baseline_does_not_use_future(self):
        qs, ms = [], []
        for i in range(29):
            at = NOW-timedelta(minutes=30*(28-i))
            qs.append(quote(at))
            ms.append(market(at, price=1 if i < 28 else 2))
        ps, _ = points(qs, ms)
        b = r.baseline(ps[-1], ps[:-1])
        self.assertEqual(b['status'], 'DESCRIPTIVE_ONLY')
        self.assertAlmostEqual(b['relativeValueVsPriorMedianPercent'], 100)
        self.assertEqual(b['slots15m'], 28)

    def test_24_fast_repeats_not_maturity(self):
        qs, ms = [], []
        for i in range(100):
            at = NOW-timedelta(seconds=100-i)
            qs.append(quote(at)); ms.append(market(at))
        ps, _ = points(qs, ms)
        self.assertEqual(r.baseline(ps[-1], ps[:-1])['status'], 'INSUFFICIENT_HISTORY')

    def test_25_retrospective_label_not_feature(self):
        qs, ms = [], []
        for ago in (30, 15, 0):
            at = NOW-timedelta(minutes=ago)
            qs.append(quote(at)); ms.append(market(at))
        rep, _ = r.build_report(qs, ms, NOW)
        f = rep['packages'][0]['forwardQuoteLabels']
        self.assertEqual(f['matchedLabels'], 2)
        self.assertFalse(f['usedForBuyDecisions'])

    def test_26_no_override_or_auto_actions(self):
        rep, _ = r.build_report([quote()], [market()], NOW)
        for k in ('currentProductionModelChanged', 'canRaiseSignal', 'automaticPurchase', 'automaticCancel', 'privateApiUsed'):
            self.assertIs(rep[k], False)
        self.assertIsNone(rep['packages'][0]['absoluteMarketDiscountPercent'])
        self.assertEqual(rep['packages'][0]['verdict'], 'NO_VERIFIED_EDGE')

    def test_27_unchecked_math_not_pass(self):
        q = quote()
        q['feed']['packages'][0].pop('math_consistency_shadow')
        rep, _ = r.build_report([q], [market()], NOW)
        self.assertFalse(rep['packages'][0]['mathCleared'])
        self.assertEqual(rep['packages'][0]['latestMathStatus'], 'NOT_RECORDED')

    def test_28_m_and_50_secondary(self):
        qs = [quote(name='Palladium M', size='M'), quote(currency='USDT', name='Silver 50', size='50')]
        rep, _ = r.build_report(qs, [market(), market(name='SHA256ASICBOOST_USDT')], NOW)
        self.assertTrue(all(x['priority'] == 'SECONDARY' for x in rep['packages']))

    def test_29_no_input_mutation(self):
        q, m = quote(), market()
        before = json.dumps([q,m], sort_keys=True)
        r.build_report([q], [m], NOW)
        self.assertEqual(before, json.dumps([q,m], sort_keys=True))

    def test_30_cli_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            s, m, f, out, pairs = (d/n for n in ('snap.jsonl','market.jsonl','feed.json','report.json','pairs.jsonl'))
            s.write_text(json.dumps(quote())+'\n')
            m.write_text(json.dumps(market())+'\n')
            f.write_text(json.dumps(quote()['feed']))
            before = [p.read_bytes() for p in (s,m,f)]
            subprocess.run([sys.executable, str(ROOT/'scripts/build_market_edge_research.py'), '--snapshots',str(s),'--market',str(m),'--feed',str(f),'--output',str(out),'--pairs',str(pairs),'--now',NOW.isoformat()],check=True,capture_output=True)
            rep = json.loads(out.read_text())
            self.assertEqual(rep['pairedCount'],1)
            self.assertEqual(before, [p.read_bytes() for p in (s,m,f)])

    def test_31_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'source.json'
            p.write_text('{}')
            proc = subprocess.run([sys.executable,str(ROOT/'scripts/build_market_edge_research.py'),'--output',str(p),'--feed',str(p)],capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertEqual(p.read_text(),'{}')

    def test_32_late_past_observation_not_prior_feature(self):
        qs, ms = [], []
        for i in range(28):
            at = NOW-timedelta(minutes=30*(28-i))
            qs.append(quote(at)); ms.append(market(at))
        ps, _ = points(qs,ms)
        current, _ = points()
        for p in ps:
            p['observedAt'] = (NOW+timedelta(seconds=1)).isoformat()
        self.assertEqual(r.baseline(current[0],ps)['slots15m'],0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
