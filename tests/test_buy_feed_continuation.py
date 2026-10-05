"""Offline regression checks for BUY feed continuity; no network or credentials."""
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/update-buy-feed.yml'


def step(text, name):
    marker = '      - name: ' + name + '\n'
    return text.split(marker, 1)[1].split('\n      - name:', 1)[0]


class BuyFeedContinuationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding='utf-8')

    def test_successor_does_not_cancel_parent(self):
        block = self.text.split('concurrency:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('group: update-nicehash-buy-feed', block)
        self.assertIn('cancel-in-progress: false', block)
        self.assertNotIn('cancel-in-progress: true', block)

    def test_no_recursive_dispatch_or_sleep_scheduler(self):
        self.assertNotIn('/dispatches', self.text)
        self.assertNotIn('sleep 240', self.text)
        self.assertNotIn('actions: write', self.text)
        self.assertEqual(self.text.count('cron:'), 1)

    def test_workflow_and_http_operations_are_bounded(self):
        self.assertIn('timeout-minutes: 15', self.text)
        fetch = step(self.text, 'Fetch BUY feed')
        self.assertIn('--connect-timeout 10', fetch)
        self.assertIn('--max-time 60', fetch)

    def test_history_health_is_checked_before_shadows_are_attached(self):
        self.assertLess(self.text.index('Annotate production history health'), self.text.index('Apply NEW calibrated shadow'))
        self.assertIn('scripts/recovery_health.py buy-feed.json --annotate', self.text)

    def test_only_main_is_published_and_cron_watchdog_is_preserved(self):
        job = self.text.split('  update-feed:\n', 1)[1]
        self.assertIn("if: github.ref == 'refs/heads/main'", job)
        checkout = step(self.text, 'Checkout repository')
        self.assertIn('ref: main', checkout)
        self.assertIn('fetch-depth: 1', checkout)
        self.assertIn('cron: "*/5 * * * *"', self.text)

    def test_degraded_feed_skips_shadow_stages_but_remains_publishable(self):
        for name in ('Apply NEW calibrated shadow', 'Apply EDGE shadow audit', 'Apply math consistency audit'):
            self.assertIn("if: steps.health.outputs.ready == 'true'", step(self.text, name))
        block = step(self.text, 'Validate final JSON')
        code = textwrap.dedent(block.split('        run: |\n', 1)[1])
        feed = {'ok':False,'status':'BUY FEED WARNING',
                'production_health':{'signal_engine_ready':False,'reasons':['HISTORY_UNAVAILABLE']},
                'packages':[{'name':'Palladium S','final_signal':'WAIT'}]}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'buy-feed.json').write_text(json.dumps(feed))
            result = subprocess.run(['bash','-e','-o','pipefail','-c',code],cwd=tmp,
                                    text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('DEGRADED HEALTH PUBLISHED',result.stdout)

    def run_guard(self, overrides=None, raw=None):
        block = step(self.text, 'Reject stale or unhealthy BUY feed')
        self.assertIn('MAX_FEED_AGE_SECONDS: "420"', block)
        code = textwrap.dedent(block.split('        run: |\n', 1)[1])
        feed = {'status': 'BUY FEED OK', 'ok': True,
                'packages': [{'available': True}],
                'checked_at': (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()}
        feed.update(overrides or {})
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'buy-feed.json').write_text(raw if raw is not None else json.dumps(feed))
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', code], cwd=tmp,
                                    env=dict(os.environ, MAX_FEED_AGE_SECONDS='420'),
                                    text=True, capture_output=True, timeout=10)
        return result

    def test_fresh_healthy_feed_is_accepted(self):
        r = self.run_guard()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stale_feed_is_rejected(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=421)).isoformat()
        self.assertNotEqual(self.run_guard({'checked_at': timestamp}).returncode, 0)

    def test_future_feed_is_rejected(self):
        timestamp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.assertNotEqual(self.run_guard({'checked_at': timestamp}).returncode, 0)

    def test_missing_or_invalid_timezone_is_rejected(self):
        for value in (None, '', 'not-a-date', '2026-01-01T00:00:00'):
            with self.subTest(timestamp=value):
                self.assertNotEqual(self.run_guard({'checked_at': value}).returncode, 0)

    def test_unhealthy_or_unavailable_data_is_rejected(self):
        for case in ({'status': 'BUY FEED WARNING'}, {'ok': False}, {'packages': []},
                     {'packages': [{'available': False}]}):
            with self.subTest(case=case):
                self.assertNotEqual(self.run_guard(case).returncode, 0)

    def test_malformed_json_is_rejected(self):
        self.assertNotEqual(self.run_guard(raw='{broken').returncode, 0)


if __name__ == '__main__':
    unittest.main()
