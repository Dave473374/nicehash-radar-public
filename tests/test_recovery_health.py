import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from recovery_health import assess, annotate

def healthy(signal='WAIT'):
    return {'status':'BUY FEED OK','ok':True,'checked_at':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),
        'history_samples_loaded':168,'history_hourly_samples':168,'history_coverage_hours':167,'history_saved':True,
        'history_diagnostics':{'list_complete':True},'history_key':'snapshot:test',
        'packages':[{'name':'Palladium S','size':'S','currency_market':'BTC','available':True,'final_signal':signal,
        'price_btc':.0001,'economics':{'complete':True,'package_cost_eur':7},
        'profitability':{'complete':True,'expected_return_percent':102},
        'primary_chain':{'currency':'LTC','algorithm':'SCRYPT'},
        'history_trend':{'sample_count_24h':24,'coverage_hours_24h':24,'quality_signal':'WAIT','trend_status':'7D READY',
        'expected_blocks_per_btc_vs_24h_percent':1,'expected_blocks_per_btc_vs_7d_percent':2}}]}

class Health(unittest.TestCase):
    def test_ready_wait_is_healthy(self): self.assertTrue(assess(healthy())['signal_engine_ready'])
    def test_179_keys_zero_hours_is_not_healthy(self):
        f=healthy();f.update(history_samples_loaded=179,history_hourly_samples=0,history_coverage_hours=0,history_status='HISTORY SAVED')
        r=assess(f);self.assertFalse(r['signal_engine_ready']);self.assertIn('HISTORY_LISTED_BUT_NOT_USABLE',r['reasons'])
    def test_stale_and_future_and_naive(self):
        for value in [(datetime.now(timezone.utc)-timedelta(seconds=421)).isoformat(),(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'2026-10-05T18:00:00',None]:
            with self.subTest(value=value):
                f=healthy();f['checked_at']=value;self.assertFalse(assess(f)['signal_engine_ready'])
    def test_unusable_q24_rejected(self):
        for value in [None,True,float('nan'),float('inf')]:
            with self.subTest(value=value):
                f=healthy();f['packages'][0]['history_trend']['expected_blocks_per_btc_vs_24h_percent']=value
                self.assertFalse(assess(f)['signal_engine_ready'])
    def test_7d_optional_until_ready(self):
        f=healthy();h=f['packages'][0]['history_trend'];h['expected_blocks_per_btc_vs_7d_percent']=None
        self.assertFalse(assess(f)['signal_engine_ready']);h['trend_status']='24H READY';self.assertTrue(assess(f)['signal_engine_ready'])
    def test_write_failure_rejected(self):
        f=healthy();f['history_saved']=False;self.assertFalse(assess(f)['signal_engine_ready'])
    def test_invalid_economics_and_empty_package_set(self):
        f=healthy();f['packages'][0]['profitability']['complete']=False;self.assertFalse(assess(f)['signal_engine_ready'])
        f['packages']=[];self.assertFalse(assess(f)['signal_engine_ready'])
    def test_duplicate_package_rejected(self):
        f=healthy();f['packages']*=2;self.assertFalse(assess(f)['signal_engine_ready'])
    def test_shadow_not_production(self):
        f=healthy();f['shadow_sampling_mode']='NO_KV';self.assertFalse(assess(f)['signal_engine_ready'])
    def test_fail_closed_never_raises_signals_and_preserves_original(self):
        f=healthy('BUY NOW');f['history_hourly_samples']=0
        out,r=annotate(f);self.assertFalse(out['ok']);self.assertEqual(out['packages'][0]['final_signal'],'WAIT')
        self.assertEqual(f['packages'][0]['final_signal'],'BUY NOW');self.assertEqual(out['packages'][0]['upstream_final_signal'],'BUY NOW')
    def test_ready_does_not_change_signal_or_shadow(self):
        f=healthy('GOOD');f['math_consistency_shadow']={'production_override':False}
        out,r=annotate(f);self.assertEqual(out['packages'],f['packages']);self.assertEqual(out['math_consistency_shadow'],f['math_consistency_shadow'])

class AlertTransitions(unittest.TestCase):
    def test_existing_alert_engine_transitions_and_stale_dedupe(self):
        script=ROOT/'scripts/build_alert_events.py'
        self.assertTrue(script.exists(),'Existing production alert script is required')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state=root/'state.json'; output=root/'out.json'; feed=root/'feed.json'
            env=dict(os.environ,BUY_RADAR_FEED=str(feed),BUY_RADAR_ALERT_STATE=str(state),BUY_RADAR_ALERT_OUTPUT=str(output),
                BUY_RADAR_ALERT_HISTORY=str(root/'history.jsonl'),BUY_RADAR_PUBLIC_MARKET_HISTORY=str(root/'missing.jsonl'))
            def run(f):
                feed.write_text(json.dumps(f));r=subprocess.run([sys.executable,str(script)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr);return json.loads(output.read_text())['eventCount']
            f=healthy('WAIT');self.assertEqual(run(f),0)
            f=healthy('GOOD');self.assertEqual(run(f),1);self.assertEqual(run(f),0)
            f=healthy('BUY NOW');self.assertEqual(run(f),1);self.assertEqual(run(f),0)
            saved=state.read_bytes();f['checked_at']=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat();f['packages'][0]['final_signal']='WAIT'
            self.assertEqual(run(f),0);self.assertEqual(state.read_bytes(),saved)
            self.assertEqual(run(healthy('WAIT')),0)
            self.assertEqual(run(healthy('GOOD')),1)

if __name__=='__main__': unittest.main()
