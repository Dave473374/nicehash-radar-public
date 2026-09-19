"""Offline protocol/causality/ledger tests; no tickets or account access."""
import copy
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_lag_episodes as m
P=json.loads((ROOT/'research/lag-protocol-v1.json').read_text())
T=datetime(2026,9,19,12,tzinfo=timezone.utc)


def fixtures(entries, start=T, name='Palladium S'):
    """entries = (minutes, public price, ticket work per native cost)."""
    currency,coin,algo=m.PACKAGES[name]
    points=[]; markets=[]
    for minute,price,work in entries:
        at=(start+timedelta(minutes=minute)).isoformat()
        points.append({'package':name,'currency':currency,'coin':coin,'mergeCoin':None,
            'size':'S' if currency=='BTC' else name.split()[-1], 'quoteAt':at,'observedAt':at,
            'marketAt':at,'marketAlgorithm':algo,'marketPriceRaw':price,'marketUnitSignature':['TH',1,1],
            'workPerNative':work,'priceNative':1,'durationSeconds':60,'hashrateHps':work/60,
            'pairStatus':'PAIRED','relayVersion':'2.9.0','mathStatus':'PASS','currentSignal':'GOOD',
            'feedExpectedReturnPercent':95})
        contract={'status':'DISPLAY_UNITS_CONFIRMED_RAW_PRICE_UNVERIFIED','source':'PUBLIC_MINING_ALGORITHMS',
            'observedAt':at,'currencyMarket':currency,'marketFactor':1e12,'priceFactor':1e12,
            'speedDisplayUnit':'TH','priceDisplayUnit':'TH','priceScale':8}
        markets.append({'collected_at':at,'source':'NICEHASH_PUBLIC_MARKET','credentials_used':False,
            'private_api_used':False,'admin_api_used':False,
            'algorithms':{algo:{'priceRaw':price,'speedUnit':'TH','unitContract':contract}}})
    return points,markets


def evaluate(entries=None, now=None, start=T):
    entries=entries or [(0,100,100),(10,110,100)]
    pts,markets=fixtures(entries,start=start)
    return m.run(pts,markets,P,[],now or start+timedelta(minutes=entries[-1][0]))


class LagTests(unittest.TestCase):
    def test_01_rule_locked(self):
        p=copy.deepcopy(P); p['marketRiseMinPercent']=4
        with self.assertRaises(ValueError): m.validate_protocol(p)

    def test_02_validation_after_registration(self):
        p=copy.deepcopy(P); p['validationStart']=p['lockedAt']
        with self.assertRaises(ValueError): m.validate_protocol(p)

    def test_03_candidate_and_no_buy(self):
        r,es=evaluate()
        self.assertEqual(len(es),1)
        self.assertFalse(r['canRaiseSignal']); self.assertFalse(r['automaticPurchase'])
        self.assertFalse(r['automaticCancel']); self.assertEqual(r['verdict'],'NO_VERIFIED_EDGE')

    def test_04_no_market_rise_no_episode(self):
        r,es=evaluate([(0,100,100),(10,95,120)])
        self.assertEqual(len(es),0)

    def test_05_ticket_already_repriced_no_episode(self):
        r,es=evaluate([(0,100,100),(10,110,90)])
        self.assertEqual(len(es),0)

    def test_06_too_short_or_long_window(self):
        for minute in (1,16):
            self.assertEqual(evaluate([(0,100,100),(minute,110,100)])[1],[])

    def test_07_repeated_market_not_new_move(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        pts[1]['marketAt']=pts[0]['marketAt']; pts[1]['marketPriceRaw']=100
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_08_missing_metadata_not_retrofilled(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        ms[0]['algorithms']['SCRYPT'].pop('unitContract')
        r,es=m.run(pts,ms,P,[],T+timedelta(minutes=10))
        self.assertEqual(es,[]); self.assertGreater(r['counts']['UNVERIFIED_DISPLAY_CONTRACT'],0)

    def test_09_wrong_currency_contract(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)],name='Silver 5')
        ms[1]['algorithms']['SHA256ASICBOOST_USDT']['unitContract']['currencyMarket']='BTC'
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_10_metadata_exact_time(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        ms[1]['algorithms']['SCRYPT']['unitContract']['observedAt']=(T+timedelta(minutes=11)).isoformat()
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=11))[1],[])

    def test_11_units_and_versions_break_episode(self):
        for key in ('relayVersion','marketUnitSignature'):
            pts,ms=fixtures([(0,100,100),(10,110,100)])
            pts[1][key]='changed'
            self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_12_future_quotes_no_episode(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        self.assertEqual(m.run(pts,ms,P,[],T)[1],[])

    def test_13_future_availability_no_episode(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        pts[1]['observedAt']=(T+timedelta(minutes=11)).isoformat()
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_14_duplicate_and_conflict(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        r,es=m.run(pts+[copy.deepcopy(pts[1])],ms,P,[],T+timedelta(minutes=10))
        self.assertEqual(len(es),1)
        bad=copy.deepcopy(pts[1]); bad['workPerNative']=99
        self.assertEqual(m.run(pts+[bad],ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_15_nan_rejected(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        pts[1]['workPerNative']=float('nan')
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_16_pending_not_miss(self):
        _,es=evaluate()
        self.assertEqual([x['status'] for x in es[0]['labels']],['PENDING','PENDING'])
        self.assertNotIn('hit',m.canonical(es[0]).lower().replace('not_return_or_hit',''))

    def test_17_forward_label_actual_not_interpolated(self):
        _,es=evaluate([(0,100,100),(10,110,100),(20,110,99),(25,110,90),(35,110,90),(40,110,85)])
        self.assertAlmostEqual(es[0]['labels'][0]['futureWorkChangePercent'],-10)
        self.assertAlmostEqual(es[0]['labels'][1]['futureWorkChangePercent'],-15)

    def test_18_gap_censors_label(self):
        _,es=evaluate([(0,100,100),(10,110,100),(30,110,90)],now=T+timedelta(minutes=31))
        self.assertEqual(es[0]['labels'][0]['status'],'CENSORED')
        self.assertEqual(es[0]['episode']['status'],'CENSORED_GAP_OR_SERIES')

    def test_19_invalid_midpoint_no_bridge(self):
        pts,ms=fixtures([(0,100,100),(10,110,100),(20,110,100),(25,110,90)])
        pts[2]['pairStatus']='INVALID_MARKET'
        _,es=m.run(pts,ms,P,[],T+timedelta(minutes=25))
        self.assertEqual(es[0]['labels'][0]['status'],'CENSORED')

    def test_20_market_reversal_not_ticket_catchup(self):
        _,es=evaluate([(0,100,100),(10,110,100),(20,100,100)])
        self.assertEqual(es[0]['episode']['closureCause'],'MARKET_REVERSED')

    def test_21_ticket_repricing_separate(self):
        _,es=evaluate([(0,100,100),(10,110,100),(20,110,90)])
        self.assertEqual(es[0]['episode']['closureCause'],'TICKET_REPRICED')

    def test_22_repeat_spikes_one_episode_in_cooldown(self):
        _,es=evaluate([(0,100,100),(10,110,100),(20,125,100),(30,140,100)])
        self.assertEqual(len(es),1)

    def test_23_missing_end_not_success(self):
        _,es=evaluate(now=T+timedelta(minutes=90))
        self.assertEqual(es[0]['labels'][1]['status'],'MISSING')
        self.assertEqual(es[0]['episode']['status'],'CENSORED_GAP_OR_SERIES')

    def test_24_no_future_leaks_into_entry(self):
        _,short=evaluate()
        _,long=evaluate([(0,100,100),(10,110,100),(20,110,99),(25,110,90)])
        self.assertEqual(short[0]['entry'],long[0]['entry'])
        self.assertEqual(short[0]['id'],long[0]['id'])

    def test_25_holdout_not_today(self):
        r,es=evaluate()
        self.assertEqual(es[0]['cohort'],'EXPLORATORY')
        self.assertEqual(r['walkForward']['status'],'NOT_STARTED')
        self.assertEqual(r['walkForward']['folds'],[])

    def test_26_holdout_daily_fixed_rule(self):
        tomorrow=T+timedelta(days=1)
        r,es=evaluate([(0,100,100),(10,110,100),(20,110,99),(25,110,90)],start=tomorrow)
        self.assertEqual(es[0]['cohort'],'HOLDOUT_REPLAY')
        self.assertFalse(r['walkForward']['thresholdsFitted'])
        self.assertTrue(any(x['testEpisodeCount']==1 for x in r['walkForward']['folds']))

    def test_27_ledger_dedupe_preserves_first_record(self):
        _,es=evaluate()
        again=m.merge_ledger(es,copy.deepcopy(es),T+timedelta(hours=1))
        self.assertEqual(len(again),1)
        self.assertEqual(again[0]['firstRecordedAt'],es[0]['firstRecordedAt'])

    def test_28_revised_entry_not_silently_changed(self):
        _,es=evaluate(); bad=copy.deepcopy(es)
        bad[0]['entry']['workPerNative']=123
        new=m.merge_ledger(es,bad,T)
        self.assertTrue(new[0]['sourceRevision'])
        self.assertEqual(new[0]['entry'],es[0]['entry'])

    def test_29_known_labels_survive_source_rolloff(self):
        _,es=evaluate([(0,100,100),(10,110,100),(20,110,99),(25,110,90)])
        _,short=evaluate()
        out=m.merge_ledger(es,short,T+timedelta(hours=1))
        self.assertEqual(out[0]['labels'][0]['status'],'OBSERVED')

    def test_30_embargo_excludes_late_known_labels(self):
        ph=m.protocol_hash(P); entry=(T-timedelta(days=1)).isoformat()
        row={'protocolHash':ph,'entry':{'quoteAt':entry},'labels':[{'horizonMinutes':15,'status':'OBSERVED',
            'availableAt':(T+timedelta(minutes=1)).isoformat(),'futureWorkChangePercent':-99}]}
        self.assertEqual(m.observed([row],15,ph,T-timedelta(days=7),T,T),[])

    def test_31_no_input_mutation(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        before=m.canonical([pts,ms,P]); m.run(pts,ms,P,[],T+timedelta(minutes=10))
        self.assertEqual(before,m.canonical([pts,ms,P]))

    def test_32_only_selected_primary_packages(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        for x in pts:x['package']='Palladium M'
        self.assertEqual(m.run(pts,ms,P,[],T+timedelta(minutes=10))[1],[])

    def test_33_cli_and_source_immutability(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp); pts,ms=fixtures([(0,100,100),(10,110,100)])
            a,b,c,ledger,out=[d/n for n in ('pairs.jsonl','market.jsonl','protocol.json','episodes.jsonl','report.json')]
            a.write_text(''.join(m.canonical(x)+'\n' for x in pts)); b.write_text(''.join(m.canonical(x)+'\n' for x in ms)); c.write_text(m.canonical(P))
            before=[x.read_bytes() for x in (a,b,c)]
            cmd=[sys.executable,str(ROOT/'scripts/build_lag_episodes.py'),'--pairs',str(a),'--market',str(b),
                 '--protocol',str(c),'--previous',str(ledger),'--episodes',str(ledger),'--output',str(out),'--now',(T+timedelta(minutes=10)).isoformat()]
            subprocess.run(cmd,check=True,capture_output=True); subprocess.run(cmd,check=True,capture_output=True)
            self.assertEqual(len(ledger.read_text().splitlines()),1)
            self.assertEqual(before,[x.read_bytes() for x in (a,b,c)])
            self.assertEqual(json.loads(out.read_text())['networkRequestsMade'],0)

    def test_34_cli_cannot_overwrite_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'x.jsonl'; path.write_text('SAFE')
            r=subprocess.run([sys.executable,str(ROOT/'scripts/build_lag_episodes.py'),'--pairs',str(path),'--output',str(path)],capture_output=True)
            self.assertNotEqual(r.returncode,0); self.assertEqual(path.read_text(),'SAFE')

    def test_35_conflicting_market_contract(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        bad=copy.deepcopy(ms[1]);bad['algorithms']['SCRYPT']['priceRaw']=999
        self.assertEqual(m.run(pts,ms+[bad],P,[],T+timedelta(minutes=10))[1],[])

    def test_36_unknown_math_recorded_not_promoted(self):
        pts,ms=fixtures([(0,100,100),(10,110,100)])
        pts[1]['mathStatus']='CRITICAL'
        r,es=m.run(pts,ms,P,[],T+timedelta(minutes=10))
        self.assertEqual(es[0]['entry']['mathStatus'],'CRITICAL')
        self.assertFalse(es[0]['canRaiseSignal']);self.assertEqual(r['verdict'],'NO_VERIFIED_EDGE')


if __name__=='__main__': unittest.main(verbosity=2)
