import copy,json,sys,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from recovery_health import validate_transport,assess,annotate

def feed(signal='WAIT'):
    return {'schema_version':12,'status':'BUY FEED OK','ok':True,
      'checked_at':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),
      'history_samples_loaded':168,'history_hourly_samples':168,'history_coverage_hours':167,'history_saved':True,
      'packages':[{'name':'Palladium S','currency_market':'BTC','available':True,'final_signal':signal,
      'economics':{'complete':True,'package_cost_eur':7},'profitability':{'complete':True,'expected_return_percent':102},
      'history_trend':{'sample_count_24h':24,'coverage_hours_24h':23,'quality_signal':'WAIT','trend_status':'7D READY',
      'expected_blocks_per_btc_vs_24h_percent':2,'expected_blocks_per_btc_vs_7d_percent':3}}]}

def warning(state='WARMING_UP'):
    f=feed();f.update(status='BUY FEED WARNING',ok=False,production_health={
      'state':state,'signal_engine_ready':False,'reasons':['HISTORY_24H_NOT_READY']})
    f['packages'][0]['history_trend'].update(sample_count_24h=4,quality_signal='HISTORY WARMING UP',trend_status='HISTORY WARMING UP')
    return f

class SchedulerTransport(unittest.TestCase):
    def test_documented_warning_is_publishable_not_actionable(self):
        f=warning();self.assertEqual(validate_transport(f),'DIAGNOSTIC_ONLY');o,h=annotate(f)
        self.assertEqual(h['state'],'WARMING_UP');self.assertFalse(o['ok']);self.assertFalse(h['signal_engine_ready'])
    def test_warming_is_not_relabelled_as_corrupt_history(self):
        f=warning();o,h=annotate(f);self.assertEqual(o['history_status'],'HISTORY WARMING UP')
        self.assertEqual(o['packages'][0]['final_signal_reason'],'HISTORY_WARMING_UP')
    def test_not_started_diagnostic_has_no_fake_quote_timestamp(self):
        f=warning('NOT_STARTED');f.update(checked_at=None,packages=[])
        self.assertEqual(validate_transport(f),'DIAGNOSTIC_ONLY');o,h=annotate(f)
        self.assertIsNone(o['checked_at']);self.assertFalse(h['signal_engine_ready'])
    def test_stale_diagnostic_can_be_mirrored_without_refreshing_old_quote(self):
        f=warning('DEGRADED');f['checked_at']=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
        self.assertEqual(validate_transport(f),'DIAGNOSTIC_ONLY');o,h=annotate(f)
        self.assertEqual(o['checked_at'],f['checked_at']);self.assertFalse(o['ok'])
    def test_future_time_even_in_diagnostic_is_refused(self):
        f=warning();f['checked_at']=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
        with self.assertRaises(ValueError):validate_transport(f)
    def test_unknown_warning_cannot_bypass_transport(self):
        for change in [{'state':'UNKNOWN'},{'signal_engine_ready':True},{'reasons':[]}]:
            f=warning();f['production_health'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_transport(f)
    def test_warning_with_false_buy_is_suppressed_not_forwarded(self):
        f=warning();f['packages'][0]['final_signal']='STRONG BUY';o,h=annotate(f)
        self.assertEqual(o['packages'][0]['final_signal'],'WAIT');self.assertEqual(f['packages'][0]['final_signal'],'STRONG BUY')
    def test_healthy_no_buy_is_not_an_incident(self):
        f=feed();self.assertEqual(assess(f)['state'],'READY');self.assertTrue(annotate(f)[0]['ok'])
    def test_malformed_nested_objects_cannot_crash_gate(self):
        for key in ['economics','profitability','history_trend']:
            f=feed();f['packages'][0][key]=['bad'];self.assertFalse(assess(f)['signal_engine_ready'])
    def test_no_nan_bool_or_duplicate_can_be_ready(self):
        for value in [float('nan'),True,float('inf'),None]:
            f=feed();f['packages'][0]['history_trend']['expected_blocks_per_btc_vs_24h_percent']=value
            self.assertFalse(assess(f)['signal_engine_ready'])
        f=feed();f['packages']*=2;self.assertFalse(assess(f)['signal_engine_ready'])
    def test_github_delivery_cannot_mutate_dedupe_during_warmup(self):
        s=(ROOT/'.github/workflows/phase3-buy-radar-alerts.yml').read_text()
        self.assertIn('Check decision readiness before alerting',s)
        for title in ['Build alert events','Emit actionable GitHub notices','Persist alert state and history']:
            block=s.split('      - name: '+title+'\n')[1].split('\n      - name:')[0]
            self.assertIn("if: steps.readiness.outputs.ready == 'true'",block)
    def test_true_core_error_is_not_disguised_as_warmup(self):
        f=warning();f['history_saved']=False;self.assertEqual(assess(f)['state'],'DEGRADED')
    def test_7_day_metric_optional_until_mature(self):
        f=feed();h=f['packages'][0]['history_trend'];h['expected_blocks_per_btc_vs_7d_percent']=None
        self.assertFalse(assess(f)['signal_engine_ready']);h['trend_status']='24H READY';self.assertTrue(assess(f)['signal_engine_ready'])
if __name__=='__main__': unittest.main()
