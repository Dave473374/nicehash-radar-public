"""No-network tests for a bounded sampling diagnostic."""
from pathlib import Path
import sys
import unittest
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sample_public_market_burst as m


class Clock:
    def __init__(self):self.now=0
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


def snapshot():
    return {'source':'NICEHASH_PUBLIC_MARKET','credentials_used':False,
        'private_api_used':False,'admin_api_used':False,
        'scryptEconomics':{'status':'CONDITIONAL_MODEL_ONLY',
            'sourceQuoteAt':'2026-09-26T06:00:00Z','quoteAgeAtMarketReceiptSeconds':1}}


class BurstTests(unittest.TestCase):
    def test_exact_bounded_cadence(self):
        c=Clock()
        rows=m.run_burst(snapshot,6,60,monotonic=c.monotonic,sleep=c.sleep)
        self.assertEqual([r['elapsedStartSeconds'] for r in rows],[0,60,120,180,240,300])
        report=m.summarize(rows,6,60)
        self.assertEqual(report['collectedSamples'],6)
        self.assertEqual(report['uniqueSourceQuoteTimestamps'],1)
        self.assertEqual(report['repeatedSourceTimestamps'],5)
        self.assertFalse(report['continuous24hCoverageVerified'])
        self.assertFalse(report['recurringDeploymentEnabled'])
        self.assertFalse(report['canRaiseSignal'])

    def test_slow_requests_do_not_bunch(self):
        c=Clock()
        def slow():
            c.now+=75
            return snapshot()
        rows=m.run_burst(slow,3,60,monotonic=c.monotonic,sleep=c.sleep)
        self.assertEqual([r['elapsedStartSeconds'] for r in rows],[0,75,150])
        self.assertEqual(m.summarize(rows,3,60)['maxObservedStartGapSeconds'],75)

    def test_rate_limit_or_authorization_stops(self):
        for status in (401,403,429):
            c=Clock()
            calls=[]
            def fail():
                calls.append(1)
                raise urllib.error.HTTPError(m.RELAY,status,'not logged',{},None)
            rows=m.run_burst(fail,6,60,monotonic=c.monotonic,sleep=c.sleep)
            self.assertEqual(len(calls),1)
            self.assertTrue(rows[0]['stoppedForHttpPolicy'])
            self.assertEqual(rows[0]['httpStatus'],status)
            self.assertNotIn('not logged',str(rows))

    def test_transient_error_is_missing_not_zero_price(self):
        c=Clock()
        def fail():raise ValueError('SENSITIVE_SENTINEL')
        rows=m.run_burst(fail,2,60,monotonic=c.monotonic,sleep=c.sleep)
        self.assertNotIn('SENSITIVE_SENTINEL',str(rows))
        self.assertNotIn('snapshot',rows[0])
        self.assertEqual(m.summarize(rows,2,60)['collectedSamples'],0)

    def test_invalid_provenance_rejected(self):
        c=Clock()
        def bad():
            s=snapshot()
            s['admin_api_used']=True
            return s
        rows=m.run_burst(bad,1,60,monotonic=c.monotonic,sleep=c.sleep)
        self.assertEqual(rows[0]['status'],'UNAVAILABLE')
        self.assertNotIn('snapshot',rows[0])

    def test_parameter_bounds_before_requests(self):
        def unexpected():raise AssertionError('Should not request')
        for n,interval in ((0,60),(32,60),(True,60),(3,59),(3,float('nan')),(3,True),(31,300)):
            with self.subTest(n=n,interval=interval):
                with self.assertRaises(ValueError):m.run_burst(unexpected,n,interval)

    def test_unavailable_economics_not_reported_usable(self):
        s=snapshot()
        s['scryptEconomics']={'status':'UNAVAILABLE'}
        c=Clock()
        rows=m.run_burst(lambda:s,1,60,monotonic=c.monotonic,sleep=c.sleep)
        r=m.summarize(rows,1,60)
        self.assertEqual(r['collectedSamples'],1)
        self.assertEqual(r['usableEconomicsSamples'],0)
        self.assertIsNone(r['maxQuoteAgeAtMarketReceiptSeconds'])


if __name__=='__main__':unittest.main()
