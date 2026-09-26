"""Synthetic implementation tests, not evidence of mining profitability."""
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import palladium_m_intraday as m

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def fixture():
    chains = []
    for coin, reward, blocktime in (('LTC', 6.1875, 150), ('DOGE', 9900, 60)):
        difficulty = 90e6 if coin == 'LTC' else 40e6
        chains.append(dict(currency=coin, algorithm='SCRYPT', network_difficulty=difficulty,
            network_hashpower_hps=difficulty*2**32/blocktime, block_time_seconds=blocktime,
            block_reward=reward, expected_blocks=1e12*7200/(difficulty*2**32)))
    p = dict(name='Palladium M', size='M', currency_market='BTC', available=True,
        price_native=.001, price_btc=.001, price_btc_equiv=.001,
        package_hashrate_hps=1e12, duration_seconds=7200,
        primary_chain=chains[0], merge_chain=chains[1], final_signal='WAIT')
    return dict(ok=True, status='BUY FEED OK', upstream_status=200, market_status='MARKET OK',
        checked_at=NOW.isoformat(), relay_version='2.9.0', packages=[p],
        market_prices_eur={c: dict(eur=price, fresh=True, age_seconds=0) for c,price in (
            ('BTC', 70000), ('LTC', 60), ('DOGE', .09))})


def sample(minutes=0, fair=1.0, version='2.9.0'):
    feed = fixture()
    at = NOW+timedelta(minutes=minutes)
    feed['checked_at'] = at.isoformat()
    feed['relay_version'] = version
    p = m.point(feed, f'{minutes:040d}', at, Counter())
    for k in ('fairDifficultyBtcPerThDay','fairHashrateBtcPerThDay','returnDifficultyPercent','returnHashratePercent'):
        p[k] *= fair
    return p


class IntradayTests(unittest.TestCase):
    def test_units_models_and_no_mutation(self):
        f = fixture()
        old = copy.deepcopy(f)
        p = m.point(f, 'a'*40, NOW, Counter())
        self.assertEqual(f, old)
        self.assertAlmostEqual(p['workThHoursFor001Btc'], 2)
        self.assertAlmostEqual(p['costBtcPerThDay'], .012)
        self.assertAlmostEqual(p['returnDifficultyPercent'], p['returnHashratePercent'])
        self.assertTrue(p['sourceMathClear'])
        self.assertFalse(p['deliveredWorkVerified'])
        self.assertFalse(p['canRaiseSignal'])

    def test_legacy_schema_is_reused_not_guessed(self):
        f = fixture()
        f['relay_version'] = '2.8.2'
        for k in ('currency_market','price_native','price_btc_equiv'):
            f['packages'][0].pop(k)
        self.assertIsNotNone(m.point(f,'a',NOW,Counter()))
        f['relay_version'] = 'unknown'
        self.assertIsNone(m.point(f,'a',NOW,Counter()))

    def test_stale_or_future_source_rejected(self):
        for seconds in (-421, 2):
            f = fixture()
            f['checked_at'] = (NOW+timedelta(seconds=seconds)).isoformat()
            self.assertIsNone(m.point(f,'a',NOW,Counter()))

    def test_subsecond_quote_availability_adjusts_forward(self):
        f = fixture()
        f['checked_at'] = (NOW+timedelta(milliseconds=300)).isoformat()
        counts = Counter()
        p = m.point(f, 'a', NOW, counts)
        self.assertEqual(p['availableAt'], f['checked_at'])
        self.assertEqual(counts['subsecondAvailabilityAdjustedForward'], 1)

    def test_missing_zero_invalid_and_usdt_fail_closed(self):
        for field, value in (('price_native',0), ('duration_seconds',True), ('package_hashrate_hps',float('nan')), ('currency_market','USDT')):
            f = fixture()
            f['packages'][0][field] = value
            self.assertIsNone(m.point(f,'a',NOW,Counter()))

    def test_conflicting_price_and_duplicate_m_rejected(self):
        f = fixture()
        f['packages'][0]['price_btc'] = .1
        self.assertIsNone(m.point(f,'a',NOW,Counter()))
        f = fixture()
        f['packages'].append(copy.deepcopy(f['packages'][0]))
        self.assertIsNone(m.point(f,'a',NOW,Counter()))

    def test_missing_chain_and_stale_fx_not_zero_value(self):
        f = fixture()
        f['packages'][0]['merge_chain'] = None
        self.assertIsNone(m.point(f,'a',NOW,Counter()))
        f = fixture()
        f['market_prices_eur']['DOGE']['fresh'] = False
        self.assertIsNone(m.point(f,'a',NOW,Counter()))

    def test_whitelisted_output_has_no_account_fields(self):
        f = fixture()
        f['accountToken'] = 'SECRET_SENTINEL'
        f['packages'][0]['customerId'] = 'SECRET_SENTINEL'
        self.assertNotIn('SECRET_SENTINEL', str(m.point(f,'a',NOW,Counter())))

    def test_dedup_and_conflict(self):
        p = sample()
        counts = Counter()
        self.assertEqual(len(m.deduplicate([p,copy.deepcopy(p)],counts)),1)
        q = copy.deepcopy(p)
        q['economicFingerprint'] = 'different'
        self.assertEqual(m.deduplicate([p,q],Counter()),[])

    def test_local_day_not_utc_day(self):
        f = fixture()
        at = datetime(2026,9,23,23,0,tzinfo=timezone.utc)
        f['checked_at'] = at.isoformat()
        p = m.point(f,'a',at,Counter())
        self.assertEqual(p['dateLocal'], '2026-09-24')

    def test_screen_and_forward_are_labels_not_buy(self):
        rs = [sample(0), sample(15,1.1), sample(30,1.0), sample(45,1.0)]
        report = m.build_report(rs, ['2026-09-24'])
        self.assertEqual(report['screen15mWindows'],1)
        self.assertEqual(report['episodesWith60mSpacing'],1)
        self.assertEqual(report['forwardQuoteDiagnostics']['15']['observedEpisodes'],1)
        self.assertLess(report['forwardQuoteDiagnostics']['15']['fairDifficultyChangePercent']['median'],0)
        self.assertFalse(report['canRaiseSignal'])
        self.assertFalse(report['canEstimateHitRate'])
        self.assertIsNone(report['verifiedNetReturnPercent'])
        self.assertEqual(report['verdict'],'NO_VERIFIED_EDGE')

    def test_no_baseline_after_target(self):
        report = m.build_report([sample(0), sample(16,1.1), sample(30,1.2)], ['2026-09-24'])
        self.assertEqual(report['eligibleWindowsByHorizon'].get(15),1)

    def test_no_forward_interpolation_over_gap(self):
        rs = [sample(0), sample(15,1.1), sample(45)]
        report = m.build_report(rs, ['2026-09-24'])
        self.assertEqual(report['forwardQuoteDiagnostics']['15']['observedEpisodes'],0)
        self.assertEqual(report['forwardQuoteDiagnostics']['30']['observedEpisodes'],0)

    def test_version_boundary_not_bridged(self):
        report = m.build_report([sample(0), sample(15,1.1,'new')], ['2026-09-24'])
        self.assertEqual(report['screen15mWindows'],0)

    def test_large_path_gap_excluded(self):
        report = m.build_report([sample(0),sample(30,1.2)], ['2026-09-24'])
        self.assertEqual(report['eligibleWindowsByHorizon'],{})

    def test_one_hour_episode_spacing(self):
        rs = [sample(i, 1.1**(i/15)) for i in range(0,106,15)]
        report = m.build_report(rs, ['2026-09-24'])
        self.assertGreater(report['screen15mWindows'],report['episodesWith60mSpacing'])
        self.assertEqual(report['episodesWith60mSpacing'],2)

    def test_no_data_explicit(self):
        r = m.build_report([], ['2026-09-24'])
        self.assertEqual(r['days'][0]['status'],'NO_DATA')
        self.assertEqual(r['quoteCount'],0)


if __name__ == '__main__':
    unittest.main()
