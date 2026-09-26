"""Synthetic diagnostics only; these tests are not profitability evidence."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import palladium_quote_repetition as m
from tests.test_palladium_m_intraday import sample


def p(minutes,work=2,ret=90,clear=True):
    x=sample(minutes)
    x['workThHoursFor001Btc']=work
    x['costBtcPerThDay']=.024/work
    x['returnDifficultyPercent']=ret
    x['returnHashratePercent']=ret
    x['sourceMathClear']=clear
    x['economicFingerprint']=m.base.sha256([minutes,work,ret,clear])
    return x


class RepetitionTests(unittest.TestCase):
    def test_known_shape_is_candidate_not_buy(self):
        rows=[p(0),p(15,2.4,108),p(29,1.8,75)]
        old=copy.deepcopy(rows)
        r=m.analyze(rows,{'2026-09-24'})
        self.assertEqual(rows,old)
        self.assertEqual(len(r['economicCandidates']),1)
        e=r['economicCandidates'][0]
        self.assertAlmostEqual(e['workChangePercent'],20)
        self.assertAlmostEqual(e['nextWorkChangePercent'],-25)
        self.assertFalse(e['confirmationWithin5m'])
        self.assertFalse(r['canRaiseSignal'])
        self.assertFalse(r['prospectiveValidation'])
        self.assertIsNone(r['verifiedNetReturnPercent'])

    def test_requires_both_economic_models(self):
        x=p(15,2.4,108)
        x['returnHashratePercent']=90
        r=m.analyze([p(0),x],{'2026-09-24'})
        self.assertEqual(len(r['economicCandidates']),0)
        self.assertEqual(len(r['allWorkRiseEvents']),1)

    def test_requires_math_check(self):
        r=m.analyze([p(0),p(15,2.4,108,False)],{'2026-09-24'})
        self.assertEqual(len(r['economicCandidates']),0)

    def test_positive_models_without_work_improvement_not_candidate(self):
        r=m.analyze([p(0),p(15,2,108)],{'2026-09-24'})
        self.assertEqual(len(r['economicCandidates']),0)

    def test_large_gap_not_bridged(self):
        r=m.analyze([p(0),p(21,2.4,108)],{'2026-09-24'})
        self.assertEqual(r['groups']['discovery']['eligibleAdjacentComparisons'],0)

    def test_series_boundary_not_bridged(self):
        x=p(15,2.4,108)
        x['relayVersion']='new'
        self.assertEqual(len(m.analyze([p(0),x],{'2026-09-24'})['economicCandidates']),0)

    def test_rapid_second_quote_is_only_recorded_confirmation(self):
        r=m.analyze([p(0),p(15,2.4,108),p(18,2.35,105)],{'2026-09-24'})
        e=r['economicCandidates'][0]
        self.assertTrue(e['confirmationWithin5m'])
        self.assertFalse(e['canRaiseSignal'])

    def test_missing_next_is_unknown_not_false_outcome(self):
        r=m.analyze([p(0),p(15,2.4,108)],{'2026-09-24'})
        e=r['economicCandidates'][0]
        self.assertIsNone(e['nextObserved'])
        self.assertIsNone(e['nextEconomicGate'])

    def test_additional_days_reported_separately(self):
        rows=[p(0),p(15,2.4,108)]
        for x in rows:x['dateLocal']='2026-09-25'
        r=m.analyze(rows,{'2026-09-24','2026-09-25'})
        self.assertEqual(r['groups']['additional']['workRiseAndEconomicGateEvents'],1)
        self.assertEqual(r['groups']['discovery']['quoteCount'],0)
        self.assertEqual(r['unavailableDates'],['2026-09-24'])

    def test_duplicate_does_not_create_trial(self):
        rows=[p(0),p(15,2.4,108)]
        rows.append(copy.deepcopy(rows[-1]))
        self.assertEqual(m.analyze(rows,{'2026-09-24'})['quoteCount'],2)

    def test_confirmation_does_not_change_entry_screen(self):
        before=m.analyze([p(0),p(15,2.4,108)],{'2026-09-24'})
        after=m.analyze([p(0),p(15,2.4,108),p(18,2.35,105)],{'2026-09-24'})
        self.assertEqual(before['economicCandidates'][0]['quote'],after['economicCandidates'][0]['quote'])
        self.assertEqual(before['economicCandidates'][0]['economicGate'],after['economicCandidates'][0]['economicGate'])


if __name__=='__main__':unittest.main()
