"""Synthetic forward-state tests. No network, purchases or profitability evidence."""
import copy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import palladium_shadow_trial as m
from enrich_scrypt_market_history import build_economics

T=datetime(2026,9,26,12,tzinfo=timezone.utc)


def snapshot(at=T,scale_m=1,scale_s=1,version='test-v1'):
    d=1e12*86400/2**32
    packages=[]
    for name,cost,h,seconds,scale in [('Palladium M',.001,1e12,7200,scale_m),('Palladium S',.0001,2e11,3600,scale_s)]:
        chains=[]
        for coin,reward,blocktime in [('LTC',2,150),('DOGE',88,60)]:
            chains.append(dict(currency=coin,algorithm='SCRYPT',network_difficulty=d,
                network_hashpower_hps=d*2**32/blocktime,block_time_seconds=blocktime,
                block_reward=reward,expected_blocks=h*scale*seconds/(d*2**32)))
        packages.append(dict(name=name,currency_market='BTC',available=True,
            price_native=cost,price_btc=cost,price_btc_equiv=cost,
            package_hashrate_hps=h*scale,duration_seconds=seconds,
            primary_chain=chains[0],merge_chain=chains[1]))
    feed=dict(ok=True,status='BUY FEED OK',upstream_status=200,market_status='MARKET OK',
        checked_at=(at-timedelta(seconds=1)).isoformat(),relay_version=version,packages=packages,
        market_prices_eur={c:dict(eur=p,fresh=True,age_seconds=0,provider='Kraken') for c,p in [('BTC',1000),('LTC',1),('DOGE',.1)]})
    market=dict(collected_at=at.isoformat(),source='NICEHASH_PUBLIC_MARKET',
        credentials_used=False,private_api_used=False,admin_api_used=False,
        algorithms={'SCRYPT':{'priceRaw':1}})
    market['scryptEconomics']=build_economics(feed,market,at,[market])
    return market


class Clock:
    def __init__(self,start=T):self.start=start;self.seconds=0
    def now(self):return self.start+timedelta(seconds=self.seconds)
    def mono(self):return self.seconds
    def sleep(self,n):self.seconds+=n


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.p=m.load_protocol(ROOT/'research/palladium-shadow-protocol-v1.json')
        self.s=m.fresh_state(self.p,T,'synthetic-code')

    def step(self,seconds,scale=1,scale_s=1,**kwargs):
        at=T+timedelta(seconds=seconds)
        return m.process_sample(self.s,snapshot(at,scale,scale_s,**kwargs),at)

    def labels(self,r):return {p['observation']['package']:p['evaluation'] for p in r['packages']}

    def test_initial_units_and_both_models(self):
        source=snapshot();old=copy.deepcopy(source)
        for name in m.PACKAGES:
            p=m.observation(source,name)
            self.assertEqual(p['status'],'VALID')
            self.assertAlmostEqual(p['returnDifficultyPercent'],90)
            self.assertAlmostEqual(p['returnHashratePercent'],90)
            self.assertTrue(p['bothMathPass'])
        self.assertEqual(source,old)

    def test_first_sample_is_only_baseline(self):
        r=self.step(0,2,2)
        self.assertEqual(set(self.labels(r).values()),{'BASELINE'})
        self.assertEqual(len(self.s['episodes']),0)

    def test_exact_three_distinct_observations_for_confirmation(self):
        self.step(0)
        r=self.step(60,1.25)
        self.assertEqual(self.labels(r)['Palladium M'],'CANDIDATE_SHADOW_ONLY')
        self.step(120,1.24)
        self.assertIsNone(self.s['episodes'][0]['confirmedAt'])
        r=self.step(180,1.23)
        self.assertEqual(self.labels(r)['Palladium M'],'REPEAT_CONFIRMED_SHADOW_ONLY')
        self.assertEqual(self.s['episodes'][0]['supportingObservations'],3)
        self.assertFalse(r['canRaiseSignal'])
        self.assertFalse(r['canSendNotifications'])

    def test_s_and_m_are_separate_and_s_primary(self):
        self.step(0)
        r=self.step(60,1,1.3)
        self.assertEqual(self.labels(r)['Palladium S'],'CANDIDATE_SHADOW_ONLY')
        self.assertEqual(self.labels(r)['Palladium M'],'NO_CANDIDATE')
        self.assertEqual(self.s['episodes'][0]['priority'],'PRIMARY')

    def test_no_future_input_dependency(self):
        self.step(0);self.step(60,1.25)
        entry=copy.deepcopy(self.s['episodes'][0])
        self.step(120,.8)
        self.assertEqual(entry['entryWorkPerBtc'],self.s['episodes'][0]['entryWorkPerBtc'])
        self.assertEqual(entry['firstObservedAt'],self.s['episodes'][0]['firstObservedAt'])
        self.assertIsNone(self.s['episodes'][0]['confirmedAt'])

    def test_small_improvement_not_candidate(self):
        self.step(0);self.step(60,1.09)
        self.assertEqual(len(self.s['episodes']),0)

    def test_return_exactly_100_is_not_positive_gate(self):
        p=m.observation(snapshot(),'Palladium M')
        p.update(returnDifficultyPercent=100,returnHashratePercent=110)
        self.assertFalse(m.gate(p))

    def test_both_math_pass_required(self):
        self.step(0)
        at=T+timedelta(seconds=60);snap=snapshot(at,1.25)
        for q in snap['scryptEconomics']['packages']:
            if q['package']=='Palladium M':q['sourceMathClear']=False
        m.process_sample(self.s,snap,at)
        self.assertEqual(len(self.s['episodes']),0)

    def test_both_models_must_pass(self):
        p=m.observation(snapshot(T,1.25),'Palladium M')
        p['returnHashratePercent']=99
        self.assertFalse(m.gate(p))

    def test_receipt_gap_resets_not_invented_persistence(self):
        self.step(0);self.step(60,1.25);self.step(180,1.25)
        self.assertEqual(self.s['episodes'][0]['endReason'],'GAP_OR_SERIES_CHANGE')
        self.assertIsNone(self.s['episodes'][0]['confirmedAt'])
        self.assertEqual(self.s['episodes'][0]['supportingObservations'],1)

    def test_duplicate_source_resets_confirmation(self):
        self.step(0);self.step(60,1.25)
        at=T+timedelta(seconds=120);snap=snapshot(at,1.25)
        snap['scryptEconomics']['sourceQuoteAt']=(T+timedelta(seconds=59)).isoformat()
        r=m.process_sample(self.s,snap,at)
        self.assertEqual(self.labels(r)['Palladium M'],'DUPLICATE_OR_OLD_SOURCE')
        self.assertIsNone(self.s['episodes'][0]['confirmedAt'])

    def test_missing_sample_resets_baseline(self):
        self.step(0);self.step(60,1.25)
        m.process_sample(self.s,None,T+timedelta(seconds=120),'HTTP_503')
        self.step(180,1.25);self.step(240,1.25)
        self.assertIsNone(self.s['episodes'][0]['confirmedAt'])
        self.assertEqual(self.s['episodes'][0]['endReason'],'INVALID_OR_MISSING_SAMPLE')

    def test_work_retention_required_for_confirmation(self):
        self.step(0);self.step(60,1.4);self.step(120,1.32)
        self.assertEqual(self.s['episodes'][0]['endReason'],'GATE_OR_WORK_NO_LONGER_MET')

    def test_cooldown_prevents_repeated_episode_count(self):
        self.step(0);self.step(60,1.25);self.step(120,.9)
        r=self.step(180,1.25)
        self.assertEqual(self.labels(r)['Palladium M'],'COOLDOWN')
        self.assertEqual(len(self.s['episodes']),1)

    def test_version_change_resets(self):
        self.step(0)
        self.step(60,1.25,version='new-version')
        self.assertEqual(len(self.s['episodes']),0)

    def test_source_time_jump_resets_even_when_receipts_consecutive(self):
        self.step(0)
        at=T+timedelta(seconds=60);snap=snapshot(at,1.25)
        snap['scryptEconomics']['sourceQuoteAt']=(T+timedelta(seconds=5)).isoformat()
        m.process_sample(self.s,snap,at)
        self.assertEqual(len(self.s['episodes']),0)

    def test_backdated_receipt_rejected(self):
        self.step(60)
        with self.assertRaises(ValueError):self.step(0)

    def test_sample_outside_trial_rejected(self):
        with self.assertRaises(ValueError):self.step(-1)
        with self.assertRaises(ValueError):self.step(72*3600)

    def test_old_source_and_stale_fx_rejected(self):
        for which in ('quote','fx'):
            s=snapshot()
            if which=='quote':s['scryptEconomics']['sourceQuoteAt']=(T-timedelta(seconds=121)).isoformat()
            else:s['scryptEconomics']['fx']['DOGE']['ageAtMarketReceiptSeconds']=121
            self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_nan_boolean_and_missing_work_rejected(self):
        for val in (None,True,0,float('nan'),float('inf')):
            s=snapshot()
            next(q for q in s['scryptEconomics']['packages'] if q['package']=='Palladium M')['quoteCostBtc']=val
            self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_inconsistent_work_and_return_rejected(self):
        for field in ('quoteImpliedCostBtcPerThDay','conditionalExpectedReturnPercent'):
            s=snapshot();q=next(q for q in s['scryptEconomics']['packages'] if q['package']=='Palladium M')
            q[field]*=2
            self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_missing_or_duplicate_chain_rejected(self):
        s=snapshot();q=next(q for q in s['scryptEconomics']['packages'] if q['package']=='Palladium M')
        q['chains'][1]['coin']=q['chains'][0]['coin']
        self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_wrong_provenance_rejected(self):
        s=snapshot();s['admin_api_used']=True
        self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_unknown_fields_not_copied(self):
        s=snapshot();s['credentials']='SECRET_SENTINEL'
        s['scryptEconomics']['packages'][0]['address']='SECRET_SENTINEL'
        p=m.observation(s,'Palladium M')
        self.assertNotIn('SECRET_SENTINEL',json.dumps(p))

    def test_protocol_hash_and_model_change_fail_closed(self):
        for code in ('wrong',):
            with self.assertRaises(ValueError):m.validate_state(self.s,self.p,code)
        self.s['expiresAt']=(T+timedelta(hours=73)).isoformat()
        with self.assertRaises(ValueError):m.validate_state(self.s,self.p,'synthetic-code')

    def test_protocol_rule_edits_rejected(self):
        p=copy.deepcopy(self.p);p['rules']['workRiseMinPercent']=9
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'p.json';f.write_text(json.dumps(p))
            with self.assertRaises(ValueError):m.load_protocol(f)

    def test_activation_cannot_precede_lock(self):
        with self.assertRaises(ValueError):m.fresh_state(self.p,m.timestamp(self.p['lockedAt'])-timedelta(seconds=1),'code')

    def test_expiry_does_not_extend_on_restart(self):
        end=self.s['expiresAt']
        self.assertEqual(m.mode(self.s,T+timedelta(hours=71)),'trial')
        self.assertEqual(m.mode(self.s,T+timedelta(hours=72)),'legacy')
        m.validate_state(self.s,self.p,'synthetic-code')
        self.assertEqual(end,self.s['expiresAt'])

    def test_http_policy_suspension_mode(self):
        self.s['suspendedHttpStatus']=429
        self.assertEqual(m.mode(self.s,T),'suspended')
        self.assertEqual(m.mode(self.s,T+timedelta(hours=73)),'legacy')

    def test_report_keeps_gaps_and_no_trading_claim(self):
        self.step(0);self.step(60);self.step(360)
        r=m.make_report(self.s,T+timedelta(seconds=360),{},True)
        self.assertEqual(r['gapsOver90Seconds'],1)
        self.assertEqual(r['missingMinuteSlotsLowerBound'],4)
        self.assertFalse(r['canEstimateHitRate'])
        self.assertFalse(r['canSendNotifications'])
        self.assertFalse(r['continuousCoverageGuaranteed'])

    def test_bounded_live_loop_uses_actual_minute_intervals(self):
        c=Clock()
        with tempfile.TemporaryDirectory() as tmp:
            r=m.collect(self.p,None,'code',3,tmp,False,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
            self.assertEqual(r['lastBatch']['startGapSeconds'],[60,60])
            self.assertEqual(r['attempts'],3)
            self.assertEqual(r['evidenceRole'],'PR_SMOKE_EXCLUDED_FROM_TRIAL')
            self.assertFalse(r['canRaiseSignal'])

    def test_rate_limit_stops_after_single_call(self):
        c=Clock();calls=[]
        def fail():
            calls.append(1)
            raise urllib.error.HTTPError('public',429,'PRIVATE_SENTINEL',{},None)
        with tempfile.TemporaryDirectory() as tmp:
            r=m.collect(self.p,None,'code',3,tmp,False,sampler=fail,clock=c.now,mono=c.mono,sleep=c.sleep)
            self.assertEqual(len(calls),1)
            self.assertEqual(r['status'],'SUSPENDED_HTTP_POLICY')
            self.assertFalse(r['lastBatch']['complete'])
            self.assertNotIn('PRIVATE_SENTINEL',Path(tmp,'new-shadow-samples.jsonl').read_text())

    def test_slow_requests_never_catch_up_with_bunched_requests(self):
        c=Clock()
        def slow():c.seconds+=75;return snapshot(c.now())
        with tempfile.TemporaryDirectory() as tmp:
            r=m.collect(self.p,None,'code',3,tmp,False,sampler=slow,clock=c.now,mono=c.mono,sleep=c.sleep)
            self.assertEqual(r['lastBatch']['startGapSeconds'],[75,75])

    def test_expiry_checked_inside_long_batch(self):
        c=Clock(T+timedelta(hours=72)-timedelta(seconds=61))
        with tempfile.TemporaryDirectory() as tmp:
            r=m.collect(self.p,self.s,'synthetic-code',31,tmp,False,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
            self.assertEqual(r['attempts'],2)
            self.assertEqual(r['status'],'COMPLETED')

    def test_checkpoint_cannot_be_overwritten(self):
        c=Clock()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'new-shadow-samples.jsonl').write_text('existing')
            with self.assertRaises(ValueError):m.collect(self.p,None,'code',1,tmp,False,sampler=lambda:snapshot(),clock=c.now,mono=c.mono,sleep=c.sleep)

    def test_two_production_batches_resume_verified_archive_and_confirm(self):
        c=Clock();oldcwd=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outputs:
            try:
                os.chdir(tmp)
                def sample():return snapshot(c.now(),1 if c.seconds==0 else 1.25)
                first=m.collect(self.p,None,'code',2,Path(outputs)/'a',True,sampler=sample,clock=c.now,mono=c.mono,sleep=c.sleep)
                state=m.load_live_state(self.p,'code')
                self.assertEqual(first['packages']['Palladium M']['candidates'],1)
                c.seconds+=60
                second=m.collect(self.p,state,'code',2,Path(outputs)/'b',True,sampler=sample,clock=c.now,mono=c.mono,sleep=c.sleep)
                self.assertEqual(second['attempts'],4)
                self.assertEqual(second['packages']['Palladium M']['repeatConfirmed'],1)
                self.assertEqual(second['startedAt'],first['startedAt'])
                from radar_snapshot_archive import verify_history
                self.assertEqual(verify_history(m.HISTORY)['records'],4)
                self.assertFalse(m.HISTORY.exists())
                self.assertEqual(len(m.PUBLIC_HISTORY.read_text().splitlines()),4)
                bad=json.loads(m.STATE.read_bytes());bad['archiveSha256']='0'*64;m.STATE.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):m.load_live_state(self.p,'code')
            finally:os.chdir(oldcwd)

    def test_lost_state_does_not_restart_existing_archive(self):
        c=Clock();old=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as out:
            try:
                os.chdir(tmp)
                m.collect(self.p,None,'code',1,out,True,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
                m.STATE.unlink()
                with self.assertRaises(ValueError):m.load_live_state(self.p,'code')
            finally:os.chdir(old)

    def test_public_history_limit_refuses_without_deleting_evidence(self):
        old=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp);m.PUBLIC_HISTORY.parent.mkdir()
                raw=m.encoded(snapshot())+b'\n';m.PUBLIC_HISTORY.write_bytes(raw)
                with patch.object(m,'MAX_PUBLIC_BYTES',1):
                    with self.assertRaises(ValueError):m.persist_public([snapshot(T+timedelta(seconds=60))],T+timedelta(seconds=60))
                self.assertEqual(m.PUBLIC_HISTORY.read_bytes(),raw)
            finally:os.chdir(old)


if __name__=='__main__':unittest.main()
