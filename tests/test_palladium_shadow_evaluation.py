"""Synthetic offline evaluator tests; no market calls or profitability evidence."""
import copy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import evaluate_palladium_shadow as e
from tests.test_palladium_shadow_trial import snapshot, T
trial=e.trial


def bundle(steps=((0,1,1),(60,1,1),(120,1,1)), transform=None, error_at=None):
    protocol=trial.load_protocol(ROOT/'research/palladium-shadow-protocol-v1.json')
    plan=e.load_plan(ROOT/'research/palladium-shadow-evaluation-plan-v1.json')
    state=trial.fresh_state(protocol,T,trial.model_code_digest())
    rows=[];sources=[]
    for seconds,m,s in steps:
        at=T+timedelta(seconds=seconds)
        source=snapshot(at,m,s)
        if transform:transform(source,seconds)
        err=None
        if error_at is not None and seconds in error_at:
            err=error_at[seconds];source=None
            if err in ('HTTP_401','HTTP_403','HTTP_429'):
                state['suspendedHttpStatus']=int(err.split('_')[1])
        rows.append(trial.process_sample(state,source,at,err))
        if source is not None:sources.append(source)
    raw=b''.join(trial.encoded(x)+b'\n' for x in rows)
    state['archiveSha256']=hashlib.sha256(raw).hexdigest()
    report_at=e.aware(state['lastReceiptAt'])+timedelta(milliseconds=500)
    collector=trial.make_report(state,report_at,{'attempted':len(rows)},True)
    return rows,sources,state,collector,protocol,plan,report_at+timedelta(seconds=60)


def evaluate(b):return e.evaluate(*b)


class EvaluationTests(unittest.TestCase):
    def test_valid_no_candidate_not_claimed_profitable(self):
        r=evaluate(bundle())
        self.assertEqual(r['status'],'INTERIM_TRIAL_RUNNING')
        self.assertEqual(r['packages']['Palladium M']['usableObservations'],3)
        self.assertEqual(r['packages']['Palladium M']['eligibleConsecutiveComparisons'],2)
        self.assertEqual(r['packages']['Palladium M']['candidateCount'],0)
        self.assertFalse(r['canRaiseSignal']);self.assertFalse(r['canSendNotifications'])
        self.assertFalse(r['canEstimateHitRate']);self.assertIsNone(r['verifiedNetReturnPercent'])
        self.assertEqual(r['verdict'],'NO_VERIFIED_EDGE')

    def test_all_inputs_unchanged(self):
        b=bundle();old=copy.deepcopy(b);evaluate(b)
        self.assertEqual(b,old)

    def test_exact_frozen_three_sample_confirmation(self):
        r=evaluate(bundle(((0,1,1),(60,1.25,1),(120,1.24,1),(180,1.23,1))))
        p=r['packages']['Palladium M'];event=p['episodes'][0]
        self.assertEqual(p['candidateCount'],1);self.assertEqual(p['repeatConfirmedCount'],1)
        self.assertEqual(event['confirmationAfterSeconds'],120)
        self.assertEqual(event['observedSupportSpanSeconds'],120)
        self.assertTrue(event['rightCensoredAtLastSupportedObservation'])
        self.assertIsNone(event['continuousConditionDurationSeconds']);self.assertIsNone(event['miningOutcome'])
        self.assertEqual(r['packages']['Palladium S']['candidateCount'],0)

    def test_s_is_primary_and_separate(self):
        r=evaluate(bundle(((0,1,1),(60,1,1.25),(120,1,1.24),(180,1,1.23))))
        self.assertEqual(r['packages']['Palladium S']['priority'],'PRIMARY')
        self.assertEqual(r['packages']['Palladium S']['repeatConfirmedCount'],1)
        self.assertEqual(r['packages']['Palladium M']['repeatConfirmedCount'],0)

    def test_missing_package_not_confirmed_available_false(self):
        def missing(s,t):
            x=s['scryptEconomics'];x['status']='UNAVAILABLE';x['reasons']=['NO_USABLE_PALLADIUM_QUOTE']
            x['packages']=[dict(package=n,status='UNAVAILABLE',reasons=['MISSING_OR_DUPLICATE_PACKAGE']) for n in trial.PACKAGES]
        r=evaluate(bundle(transform=missing));p=r['packages']['Palladium M']
        self.assertEqual(p['usableObservations'],0)
        self.assertEqual(p['unusableObservations'],3)
        self.assertEqual(p['unusableReasonCounts']['MISSING_OR_DUPLICATE_PACKAGE'],3)
        self.assertIsNone(p['confirmedUnavailableFalseObservations']);self.assertIsNone(p['exactAvailableMinutes'])
        self.assertEqual(r['coverage']['publicResponses'],3)
        self.assertIsNone(p['longestUsableStreak'])

    def test_non_btc_or_unavailable_reason_remains_ambiguous(self):
        def unavailable(s,t):
            s['scryptEconomics']['packages'][0].update(status='UNAVAILABLE',reasons=['UNAVAILABLE_OR_NON_BTC_PACKAGE'])
        r=evaluate(bundle(transform=unavailable))
        for p in r['packages'].values():
            self.assertIsNone(p['confirmedUnavailableFalseObservations'])

    def test_valid_but_math_critical_is_usable_not_buy(self):
        def critical(s,t):
            for p in s['scryptEconomics']['packages']:
                p['sourceMathClear']=False
                for c in p['chains']:c['sourceMathStatus']='CRITICAL'
        r=evaluate(bundle(transform=critical));p=r['packages']['Palladium M']
        self.assertEqual(p['usableObservations'],3)
        self.assertEqual(p['bothModelReturnsAndMathGateObservations'],0)
        self.assertEqual(p['mathStatusCounts']['DOGE:CRITICAL'],3)

    def test_sample_error_separate_from_package_availability(self):
        r=evaluate(bundle(error_at={60:'HTTP_503'}));p=r['packages']['Palladium M']
        self.assertEqual(r['coverage']['publicResponses'],2)
        self.assertEqual(p['unusableReasonCounts']['ACQUISITION_ERROR:HTTP_503'],1)
        self.assertEqual(p['streakCount'],2)
        self.assertEqual(p['eligibleConsecutiveComparisons'],0)

    def test_suspended_http_replayed_exactly(self):
        r=evaluate(bundle(((0,1,1),),error_at={0:'HTTP_429'}))
        self.assertEqual(r['collectorReportedStatus'],'SUSPENDED_HTTP_POLICY')
        self.assertEqual(r['coverage']['publicResponses'],0)

    def test_source_duplicate_not_a_new_usable_confirmation(self):
        def duplicate(s,t):
            if t==120:s['scryptEconomics']['sourceQuoteAt']=(T+timedelta(seconds=59)).isoformat()
        r=evaluate(bundle(((0,1,1),(60,1.25,1),(120,1.25,1)),transform=duplicate))
        p=r['packages']['Palladium M']
        self.assertEqual(p['usableObservations'],2)
        self.assertEqual(p['repeatConfirmedCount'],0)
        self.assertEqual(p['episodes'][0]['recordedEndClass'],'OBSERVATION_INTERRUPTED')

    def test_invalid_sample_ends_observation_not_mining_loss(self):
        r=evaluate(bundle(((0,1,1),(60,1.25,1),(120,1.25,1)),error_at={120:'HTTP_503'}))
        event=r['packages']['Palladium M']['episodes'][0]
        self.assertEqual(event['recordedEndClass'],'OBSERVATION_INTERRUPTED')
        self.assertIsNone(event['miningOutcome'])

    def test_observed_gate_failure_has_bracket_not_exact_duration(self):
        r=evaluate(bundle(((0,1,1),(60,1.25,1),(120,.8,1))))
        event=r['packages']['Palladium M']['episodes'][0]
        self.assertEqual(event['recordedEndClass'],'OBSERVED_GATE_OR_WORK_FAILURE')
        self.assertEqual(event['changeTimeBracketSeconds'],60)
        self.assertEqual(event['observedSupportSpanSeconds'],0)
        self.assertIsNone(event['continuousConditionDurationSeconds'])

    def test_gap_breaks_streak_and_no_interpolation(self):
        r=evaluate(bundle(((0,1,1),(60,1,1),(600,1,1))))
        p=r['packages']['Palladium M'];c=r['coverage']
        self.assertEqual(p['longestUsableStreak']['observations'],2)
        self.assertEqual(p['eligibleConsecutiveComparisons'],1)
        self.assertEqual(c['gapsOver90Seconds'],1)
        self.assertEqual(c['receiptGapsSeconds']['max'],540)
        self.assertGreater(c['closedMinuteSlotsWithNoPersistedAttempt'],0)

    def test_tail_is_not_proven_outage(self):
        b=list(bundle());b[-1]+=timedelta(minutes=20)
        r=evaluate(b)
        self.assertGreater(r['coverage']['unobservedTailSeconds'],1200)
        self.assertFalse(r['coverage']['inFlightUnpublishedSamplesKnown'])
        self.assertEqual(r['status'],'INTERIM_TRIAL_RUNNING')

    def test_partial_last_minute_excluded_from_closed_slot_denominator(self):
        b=list(bundle(((0,1,1),(60,1,1))));b[-1]=T+timedelta(seconds=90)
        r=evaluate(b);c=r['coverage']
        self.assertEqual(c['closedMinuteSlotsElapsed'],1)
        self.assertEqual(c['occupiedClosedMinuteSlots'],1)
        self.assertEqual(c['persistedAttempts'],2)

    def test_multiple_receipts_in_one_slot_not_double_coverage(self):
        r=evaluate(bundle(((0,1,1),(40,1,1),(80,1,1))))
        self.assertLess(r['coverage']['occupiedClosedMinuteSlots'],r['coverage']['persistedAttempts'])
        self.assertLessEqual(r['coverage']['occupiedClosedSlotSharePercent'],100)

    def test_expired_clock_does_not_claim_actual_collector_stopped(self):
        b=list(bundle());b[-1]=T+timedelta(hours=73)
        r=evaluate(b)
        self.assertTrue(r['trialClockElapsed'])
        self.assertFalse(r['actualCollectorStopIndependentlyVerified'])
        self.assertEqual(r['coverage']['closedMinuteSlotsElapsed'],4320)
        self.assertEqual(r['collectorReportedStatus'],'COLLECTING')

    def test_last_batch_delay_is_not_phone_or_git_latency(self):
        r=evaluate(bundle());p=r['publication']
        self.assertAlmostEqual(p['lastBatchObservationToReportGenerationSeconds']['max'],120.5)
        self.assertIsNone(p['trueGitPublicationLatencySeconds']);self.assertIsNone(p['phoneDeliveryLatencySeconds'])

    def test_cutoff_before_source_rejected_not_selectively_truncated(self):
        b=list(bundle());b[-1]=T+timedelta(seconds=30)
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_cutoff_before_report_rejected(self):
        b=list(bundle());b[-1]=T+timedelta(seconds=120)
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_missing_hashed_public_source_rejected(self):
        b=list(bundle());b[1]=b[1][:-1]
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_wrong_public_source_not_used_as_fallback(self):
        b=list(bundle());b[1][1]['scryptEconomics']['relayVersion']='tampered'
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_tampered_evaluation_label_rejected(self):
        b=list(bundle());b[0][1]['packages'][0]['evaluation']='CANDIDATE_SHADOW_ONLY'
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_tampered_observation_value_rejected(self):
        b=list(bundle());b[0][1]['packages'][0]['observation']['returnDifficultyPercent']=999
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_wrong_protocol_and_code_refused(self):
        for field in ('protocolHash','modelCodeHash'):
            b=list(bundle());b[2][field]='wrong'
            with self.assertRaises(ValueError):evaluate(b)

    def test_stored_state_counter_or_episode_tampering_refused(self):
        for field in ('attempts','publicSamples'):
            b=list(bundle());b[2][field]+=1
            with self.assertRaises(ValueError):evaluate(b)
        b=list(bundle());b[2]['episodes']=[{'invented':True}]
        with self.assertRaises(ValueError):evaluate(b)

    def test_collector_report_mismatch_refused(self):
        b=list(bundle());b[3]['packages']['Palladium M']['validSamples']=1000
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_smoke_and_pretrial_rows_rejected(self):
        b=list(bundle());b[0][0]['source']='PR_SMOKE'
        with self.assertRaises(e.EvidenceError):evaluate(b)
        b=list(bundle());b[3]['evidenceRole']='PR_SMOKE_EXCLUDED_FROM_TRIAL'
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_duplicate_attempt_not_an_independent_trial(self):
        b=list(bundle());b[0].append(copy.deepcopy(b[0][-1]));b[2]['attempts']+=1
        with self.assertRaises(ValueError):evaluate(b)

    def test_omission_of_losing_or_invalid_row_refused(self):
        b=list(bundle(error_at={60:'HTTP_503'}));b[0].pop(1)
        with self.assertRaises(e.EvidenceError):evaluate(b)

    def test_strict_nan_json_rejected(self):
        with self.assertRaises(e.EvidenceError):e.strict_json('{"x":NaN}')

    def test_output_alias_and_source_directories_protected(self):
        for out in (trial.STATE,trial.PROTOCOL,Path('scripts/evaluate_palladium_shadow.py'),trial.HISTORY):
            with self.assertRaises(e.EvidenceError):e.check_outputs((out,e.MARKDOWN),e.SOURCE_FILES)
        with self.assertRaises(e.EvidenceError):e.check_outputs((e.OUTPUT,e.OUTPUT),())
        e.check_outputs((e.OUTPUT,e.MARKDOWN),e.SOURCE_FILES)

    def test_local_dates_not_utc_dates(self):
        # Patch the actual loaded module under BOTH unittest module/discovery modes.
        with patch.dict(bundle.__globals__,{'T':T.replace(hour=23)}):
            r=evaluate(bundle())
        self.assertIn('2026-09-27',r['packages']['Palladium M']['byLocalDate'])

    def test_zero_denominator_is_unknown_not_zero_availability(self):
        b=list(bundle(((0,1,1),)));b[-1]=T+timedelta(seconds=1)
        r=evaluate(b)
        self.assertIsNone(r['coverage']['occupiedClosedSlotSharePercent'])
        self.assertIsNone(r['packages']['Palladium M']['usableClosedSlotSharePercent'])

    def test_markdown_explicitly_qualifies_unavailable_and_profit(self):
        text=e.render(evaluate(bundle()))
        self.assertIn('available:false',text)
        self.assertIn('ni isto kot MATH PASS',text)
        self.assertIn('ni dokaz',text)


if __name__=='__main__':unittest.main()
