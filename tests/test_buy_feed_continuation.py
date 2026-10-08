"""Regression tests for the existing publisher: one scheduler, explicit diagnostic feeds."""
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from recovery_health import validate_transport
WORKFLOW=ROOT/'.github/workflows/update-buy-feed.yml'
def step(text,name): return text.split('      - name: '+name+'\n',1)[1].split('\n      - name:',1)[0]
class BuyFeedContinuationSafetyTests(unittest.TestCase):
    def setUp(self): self.text=WORKFLOW.read_text()
    def test_successor_does_not_cancel_parent(self):
        self.assertIn('group: update-nicehash-buy-feed',self.text);self.assertIn('cancel-in-progress: false',self.text)
    def test_no_recursive_dispatch_or_sleep_scheduler(self):
        for item in ['/dispatches','sleep 240','actions: write']: self.assertNotIn(item,self.text)
        self.assertEqual(self.text.count('cron:'),1)
    def test_workflow_and_http_operations_are_bounded(self):
        self.assertIn('timeout-minutes: 15',self.text)
        for item in ['--connect-timeout 10','--max-time 60']: self.assertIn(item,step(self.text,'Fetch BUY feed'))
    def test_history_health_is_checked_before_shadows_are_attached(self):
        self.assertLess(self.text.index('Annotate production history health'),self.text.index('Apply NEW calibrated shadow'))
        self.assertIn('scripts/recovery_health.py buy-feed.json --annotate',self.text)
    def test_only_main_is_published_and_cron_watchdog_is_preserved(self):
        self.assertIn("if: github.ref == 'refs/heads/main'",self.text)
        self.assertIn('ref: main',step(self.text,'Checkout repository'));self.assertIn('fetch-depth: 1',self.text)
        self.assertIn('cron: "*/5 * * * *"',self.text)
    def test_degraded_feed_skips_shadow_stages_but_remains_publishable(self):
        for name in ['Apply NEW calibrated shadow','Apply EDGE shadow audit','Apply math consistency audit']:
            self.assertIn("if: steps.health.outputs.ready == 'true'",step(self.text,name))
        code=textwrap.dedent(step(self.text,'Validate final JSON').split('        run: |\n',1)[1])
        f={'ok':False,'status':'BUY FEED WARNING','production_health':{'signal_engine_ready':False,'reasons':['HISTORY_UNAVAILABLE']},'packages':[{'final_signal':'WAIT'}]}
        with tempfile.TemporaryDirectory() as td:
            Path(td,'buy-feed.json').write_text(json.dumps(f));r=subprocess.run(['bash','-e','-o','pipefail','-c',code],cwd=td,capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('DEGRADED HEALTH PUBLISHED',r.stdout)
    def feed(self,**changes):
        f={'status':'BUY FEED OK','ok':True,'schema_version':12,'packages':[{'available':True}],
           'checked_at':(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()};f.update(changes);return f
    def test_fresh_healthy_feed_is_accepted(self): self.assertEqual(validate_transport(self.feed()),'FRESH_DATA')
    def test_stale_feed_is_rejected(self):
        with self.assertRaises(ValueError): validate_transport(self.feed(checked_at=(datetime.now(timezone.utc)-timedelta(seconds=421)).isoformat()))
    def test_future_feed_is_rejected(self):
        with self.assertRaises(ValueError): validate_transport(self.feed(checked_at=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()))
    def test_missing_or_invalid_timezone_is_rejected(self):
        for value in [None,'','bad','2026-01-01T00:00:00']:
            with self.subTest(value=value),self.assertRaises(ValueError): validate_transport(self.feed(checked_at=value))
    def test_unhealthy_or_unavailable_data_is_rejected(self):
        for case in [{'status':'BUY FEED WARNING'},{'ok':False},{'packages':[]},{'packages':[{'available':False}]}]:
            with self.subTest(case=case),self.assertRaises(ValueError): validate_transport(self.feed(**case))
    def test_malformed_json_is_rejected(self):
        with self.assertRaises(json.JSONDecodeError): json.loads('{broken')
        for data in [None,[],{},self.feed(schema_version=999)]:
            with self.subTest(data=data),self.assertRaises(ValueError): validate_transport(data)
if __name__=='__main__':unittest.main()
