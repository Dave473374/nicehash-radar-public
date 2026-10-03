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

    def test_cancelled_run_does_not_restart_itself(self):
        block = step(self.text, 'Schedule next BUY feed refresh')
        self.assertIn("if: ${{ !cancelled() && github.ref == 'refs/heads/main' }}", block)
        self.assertNotIn('always()', block)
        self.assertNotIn('success()', block)  # A transient failure may recover.
        self.assertEqual(block.count('--request POST'), 1)
        self.assertIn('sleep 240', block)
        self.assertNotIn('--retry', block)  # No ambiguous duplicate dispatch.

    def test_workflow_and_http_operations_are_bounded(self):
        self.assertIn('timeout-minutes: 15', self.text)
        fetch = step(self.text, 'Fetch BUY feed')
        dispatch = step(self.text, 'Schedule next BUY feed refresh')
        for block in (fetch, dispatch):
            self.assertIn('--connect-timeout 10', block)
            self.assertIn('--max-time ', block)
        self.assertIn('--max-time 60', fetch)
        self.assertIn('--max-time 30', dispatch)

    def test_only_main_is_published_and_cron_watchdog_is_preserved(self):
        job = self.text.split('  update-feed:\n', 1)[1]
        self.assertIn("if: github.ref == 'refs/heads/main'", job)
        checkout = step(self.text, 'Checkout repository')
        self.assertIn('ref: main', checkout)
        self.assertIn('fetch-depth: 1', checkout)
        self.assertIn('cron: "*/5 * * * *"', self.text)

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
