"""Regression tests for evidence integrity. Offline; no trading or account I/O."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_lag_episodes as m
from test_market_lag_episodes import P, T, evaluate, fixtures

FULL = [(0,100,100),(10,110,100),(20,110,99),(25,110,90),(35,110,90),(40,110,85)]


def full(start=T):
    return evaluate(FULL, start=start)


class LagIntegrityTests(unittest.TestCase):
    def test_01_cannot_backdate_holdout_and_registration(self):
        p = copy.deepcopy(P)
        p.update(lockedAt='2026-09-18T00:00:00Z', validationStart='2026-09-19T00:00:00Z')
        with self.assertRaises(ValueError): m.validate_protocol(p)

    def test_02_cannot_postpone_holdout_after_seeing_data(self):
        p = copy.deepcopy(P); p['validationStart'] = '2026-09-22T00:00:00Z'
        with self.assertRaises(ValueError): m.validate_protocol(p)

    def test_03_hypothesis_text_is_frozen_too(self):
        p = copy.deepcopy(P); p['hypothesis'] += ' alternative interpretation'
        with self.assertRaises(ValueError): m.validate_protocol(p)

    def test_04_original_protocol_digest_unchanged(self):
        self.assertEqual(m.protocol_hash(m.validate_protocol(P)), 'ba500589e4908319e07d57ca0466498b6318368b7b9dbee86ca77c4e94d79129')

    def test_05_work_cost_identity_not_trusted_blindly(self):
        pts, ms = fixtures([(0,100,100),(10,110,100)])
        pts[1]['workPerNative'] = 1000
        report, records = m.run(pts, ms, P, [], T+timedelta(minutes=10))
        self.assertEqual(records, [])
        self.assertEqual(report['counts']['WORK_COST_IDENTITY_MISMATCH'], 1)

    def test_06_overflow_of_derived_work_rejected(self):
        pts, ms = fixtures([(0,100,100),(10,110,100)])
        pts[1].update(hashrateHps=1e308, durationSeconds=1e308)
        self.assertEqual(m.run(pts, ms, P, [], T+timedelta(minutes=10))[1], [])

    def test_07_blank_price_unit_not_a_contract(self):
        pts, ms = fixtures([(0,100,100),(10,110,100)])
        ms[1]['algorithms']['SCRYPT']['unitContract']['priceDisplayUnit'] = ''
        self.assertEqual(m.run(pts, ms, P, [], T+timedelta(minutes=10))[1], [])

    def test_08_retracted_observation_is_quarantined(self):
        _, old = full(); new = copy.deepcopy(old)
        new[0]['labels'][0] = {'horizonMinutes':15, 'status':'CENSORED'}
        out = m.merge_ledger(old, new, T+timedelta(hours=1))
        self.assertTrue(out[0]['sourceRevision'])
        self.assertIn('OBSERVED_LABEL_RETRACTED', out[0]['sourceRevisionReasons'])
        self.assertEqual(out[0]['labels'][0], old[0]['labels'][0])
        self.assertEqual(m.observed(out,15,m.protocol_hash(P),T,T+timedelta(days=1),T+timedelta(hours=1)), [])

    def test_09_missing_label_cannot_remain_evaluable(self):
        _, old = full(); new = copy.deepcopy(old)
        new[0]['labels'][0] = {'horizonMinutes':15,'status':'MISSING'}
        self.assertTrue(m.merge_ledger(old,new,T+timedelta(hours=1))[0]['sourceRevision'])

    def test_10_revised_value_frozen_and_excluded(self):
        _, old = full(); new = copy.deepcopy(old)
        new[0]['labels'][0]['futureWorkChangePercent'] = -99
        out = m.merge_ledger(old,new,T+timedelta(hours=1))[0]
        self.assertTrue(out['sourceRevision'])
        self.assertEqual(out['labels'][0], old[0]['labels'][0])

    def test_11_duplicate_ledger_ids_cannot_overwrite(self):
        _, old = full(); fork = copy.deepcopy(old[0]); fork['entry']['workPerNative'] = 99
        with self.assertRaises(ValueError): m.merge_ledger([old[0],fork],[],T+timedelta(hours=1))

    def test_12_identical_ledger_duplicates_collapse(self):
        _, old = full()
        self.assertEqual(len(m.merge_ledger([old[0],copy.deepcopy(old[0])],[],T+timedelta(hours=1))),1)

    def test_13_forged_id_rejected(self):
        _, old = full(); old[0]['id'] = 'a'*64
        with self.assertRaises(ValueError): m.merge_ledger(old,[],T+timedelta(hours=1))

    def test_14_duplicate_horizons_rejected(self):
        _, old = full(); old[0]['labels'].append(copy.deepcopy(old[0]['labels'][0]))
        with self.assertRaises(ValueError): m.merge_ledger(old,[],T+timedelta(hours=1))

    def test_15_merge_does_not_mutate_input_objects(self):
        _, old = evaluate(); _, new = full()
        before = m.canonical([old,new])
        m.merge_ledger(old,new,T+timedelta(hours=1))
        self.assertEqual(m.canonical([old,new]),before)

    def test_16_late_detection_not_prospective(self):
        _, records = full()
        self.assertFalse(m.recorded_before_target(records[0],15))
        self.assertFalse(m.recorded_before_target(records[0],30))
        summary = m.recording_summary(records)
        self.assertEqual(summary['medianDetectionDelayMinutes'],30)
        self.assertFalse(summary['isPhoneDeliveryLatency'])

    def test_17_timely_detection_keeps_original_time(self):
        _, early = evaluate(); _, late = full()
        out = m.merge_ledger(early,late,T+timedelta(minutes=40))
        self.assertTrue(m.recorded_before_target(out[0],15))
        self.assertEqual(out[0]['firstRecordedAt'],early[0]['firstRecordedAt'])
        self.assertEqual(out[0]['labels'][0]['firstObservedAt'],(T+timedelta(minutes=40)).isoformat())

    def test_18_holdout_replay_is_reported_separately(self):
        report, _ = full(start=T+timedelta(days=1))
        folds = [f for f in report['walkForward']['folds'] if f['testEpisodeCount']]
        self.assertTrue(folds)
        self.assertTrue(all(f['testPreOutcomeRecordedEpisodeCount']==0 for f in folds))
        self.assertTrue(all(f['testLateOrUnknownRecordedEpisodeCount']==f['testEpisodeCount'] for f in folds))

    def test_19_controls_survive_archive_rolloff(self):
        pts, ms = fixtures([(0,100,100),(10,100,100),(20,100,100),(25,100,99)])
        report, records, controls = m.run(pts,ms,P,[],T+timedelta(minutes=25),include_controls=True)
        self.assertEqual(len(controls),1)
        after, _, saved = m.run([],[],P,records,T+timedelta(days=31),controls,include_controls=True)
        self.assertEqual(saved,controls)
        self.assertEqual(after['persistedControlCount'],1)

    def test_20_control_revisions_same_as_episodes(self):
        pts, ms = fixtures([(0,100,100),(10,100,100),(20,100,100),(25,100,99)])
        _, _, controls = m.run(pts,ms,P,[],T+timedelta(minutes=25),include_controls=True)
        changed = copy.deepcopy(controls); changed[0]['entry']['workPerNative'] = 101
        out = m.merge_ledger(controls,changed,T+timedelta(hours=1))
        self.assertTrue(out[0]['sourceRevision'])
        self.assertEqual(out[0]['entry'],controls[0]['entry'])

    def test_21_version_baselines_are_not_pooled(self):
        pts, ms = fixtures([(0,100,100),(10,100,100),(20,100,100),(25,100,99)])
        for p in pts: p['relayVersion'] = 'old-version'
        _, _, controls = m.run(pts,ms,P,[],T+timedelta(minutes=25),include_controls=True)
        _, records = full(start=T+timedelta(days=1))
        folds = m.walk_forward(records,controls,P,T+timedelta(days=1,hours=1))['folds']
        tests = [f for f in folds if f['testEpisodeCount']]
        self.assertTrue(tests)
        self.assertTrue(all(f['referenceControlCount']==0 for f in tests))

    def test_22_horizon_boundary_is_strict(self):
        _, records = evaluate()
        records[0]['firstRecordedAt']=(T+timedelta(minutes=25)).isoformat()
        self.assertFalse(m.recorded_before_target(records[0],15))
        self.assertTrue(m.recorded_before_target(records[0],30))

    def test_23_missing_recording_time_not_assumed_timely(self):
        _, records = evaluate(); records[0].pop('firstRecordedAt')
        self.assertFalse(m.recorded_before_target(records[0],15))

    def test_24_controls_output_cannot_alias_episodes(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'saved.jsonl'; path.write_text('UNCHANGED')
            p=subprocess.run([sys.executable,str(ROOT/'scripts/build_lag_episodes.py'),
                '--episodes',str(path),'--controls',str(path)],capture_output=True)
            self.assertNotEqual(p.returncode,0)
            self.assertEqual(path.read_text(),'UNCHANGED')

    def test_25_nonfinite_ledger_refused(self):
        _, records=full(); records[0]['labels'][0]['futureWorkChangePercent']=float('nan')
        with self.assertRaises(ValueError): m.merge_ledger(records,[],T)

    def test_26_withdrawn_horizon_quarantines_record(self):
        _, old=full(); new=copy.deepcopy(old); new[0]['labels']=[]
        out=m.merge_ledger(old,new,T+timedelta(hours=1))
        self.assertTrue(out[0]['sourceRevision'])
        self.assertEqual(out[0]['labels'],old[0]['labels'])

    def test_27_controls_cli_persist_and_read_prior(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); pts,ms=fixtures([(0,100,100),(10,100,100),(20,100,100),(25,100,99)])
            pairs,market,protocol,episodes,out=[root/n for n in ['pairs','market','protocol','episodes','report']]
            pairs.write_text(''.join(m.canonical(p)+'\n' for p in pts))
            market.write_text(''.join(m.canonical(p)+'\n' for p in ms))
            protocol.write_text(m.canonical(P))
            cmd=[sys.executable,str(ROOT/'scripts/build_lag_episodes.py'),'--pairs',str(pairs),'--market',str(market),
                 '--protocol',str(protocol),'--previous',str(episodes),'--episodes',str(episodes),'--output',str(out),
                 '--now',(T+timedelta(minutes=25)).isoformat()]
            subprocess.run(cmd,check=True,capture_output=True)
            controls=root/'lag-controls.jsonl'; first=controls.read_text()
            subprocess.run(cmd,check=True,capture_output=True)
            self.assertEqual(controls.read_text(),first)
            self.assertEqual(json.loads(out.read_text())['persistedControlCount'],1)

    def test_28_source_rolloff_keeps_audit_evidence(self):
        _, old=full()
        self.assertEqual(m.merge_ledger(old,[],T+timedelta(days=31)),old)


if __name__=='__main__': unittest.main(verbosity=2)
