"""Offline regression: an unavailable current quote must not discard backfill."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import collect_calibration as c
import radar_snapshot_archive as a


class CollectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'current.json'
        self.output = self.root / 'calibration' / 'radar-snapshots.jsonl'
        self.addCleanup(patch.stopall)
        patch.object(c, 'OUTPUT_FILE', self.output).start()
        self.now = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)
        self.feed = {
            'status': 'BUY FEED OK', 'ok': True, 'upstream_status': 200,
            'market_status': 'MARKET OK', 'relay_version': '2.9.0-history-recovery.2',
            'checked_at': (self.now - timedelta(seconds=30)).isoformat(),
            'packages': [{'name': 'Palladium S', 'final_signal': 'WAIT'}],
        }
        self.write()

    def write(self, feed=None):
        self.source.write_text(json.dumps(self.feed if feed is None else feed))

    def collect(self, **kwargs):
        return c.collect_snapshot(self.source, now=self.now, **kwargs)

    def rows(self):
        return [json.loads(x) for x in a.read_history_text(self.output).splitlines() if x.strip()]

    def test_valid_snapshot_preserves_observation_and_signal(self):
        before = copy.deepcopy(self.feed)
        result = self.collect(source_kind='SCHEDULED_RELAY_READ')
        row = self.rows()[0]
        self.assertEqual(result['status'], 'SNAPSHOT_APPENDED')
        self.assertEqual(row['feed'], before)
        self.assertEqual(row['feed_generated_at'], before['checked_at'])
        self.assertEqual(row['collected_at'], self.now.isoformat())
        self.assertEqual(row['source'], 'SCHEDULED_RELAY_READ')
        self.assertFalse(result['canRaiseSignal'])
        self.assertFalse(result['automaticPurchase'])

    def test_identical_feed_is_not_appended_twice(self):
        self.collect()
        digest = a.history_digest(self.output)
        self.assertEqual(self.collect()['status'], 'SNAPSHOT_ALREADY_PRESENT')
        self.assertEqual(a.history_digest(self.output), digest)
        self.assertEqual(len(self.rows()), 1)

    def test_default_mode_still_rejects_stale_feed(self):
        self.feed['checked_at'] = (self.now - timedelta(minutes=16)).isoformat()
        self.write()
        with self.assertRaises(AssertionError):
            self.collect()
        self.assertFalse(a.history_exists(self.output))

    def test_skip_stale_keeps_verified_backfill_byte_for_byte(self):
        a.append_history(self.output, [{'source': 'GIT_BUY_FEED_HISTORY', 'collected_at': self.now.isoformat(), 'feed': self.feed}])
        digest = a.history_digest(self.output)
        self.feed['checked_at'] = (self.now - timedelta(minutes=16)).isoformat()
        self.write()
        result = self.collect(skip_unavailable=True)
        self.assertEqual(result['status'], 'CURRENT_INPUT_SKIPPED')
        self.assertFalse(result['sourceEligible'])
        self.assertFalse(result['snapshotAppended'])
        self.assertEqual(a.history_digest(self.output), digest)
        self.assertEqual(a.verify_history(self.output)['records'], 1)

    def test_warning_is_not_promoted_or_archived_as_healthy(self):
        self.feed.update(status='BUY FEED WARNING', ok=False)
        self.write()
        before = self.source.read_bytes()
        result = self.collect(skip_unavailable=True)
        self.assertEqual(result['status'], 'CURRENT_INPUT_SKIPPED')
        self.assertEqual(result['sourceStatus'], 'BUY FEED WARNING')
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(a.history_exists(self.output))

    def test_future_observation_is_not_backdated(self):
        self.feed['checked_at'] = (self.now + timedelta(seconds=1)).isoformat()
        self.write()
        result = self.collect(skip_unavailable=True)
        self.assertFalse(result['sourceEligible'])
        self.assertEqual(result['sourceCheckedAt'], self.feed['checked_at'])

    def test_timezone_required(self):
        self.feed['checked_at'] = '2026-10-09T18:59:30'
        self.write()
        self.assertFalse(self.collect(skip_unavailable=True)['sourceEligible'])

    def test_missing_or_malformed_source_has_no_fallback(self):
        for text in ('', '<html>error</html>', '{}', '[]'):
            with self.subTest(text=text):
                self.source.write_text(text)
                self.assertEqual(self.collect(skip_unavailable=True)['status'], 'CURRENT_INPUT_SKIPPED')
        self.source.unlink()
        self.assertEqual(self.collect(skip_unavailable=True)['status'], 'CURRENT_INPUT_SKIPPED')
        self.assertFalse(a.history_exists(self.output))

    def test_nonfinite_package_value_is_not_archived(self):
        self.feed['packages'][0]['value'] = float('nan')
        self.write()
        self.assertFalse(self.collect(skip_unavailable=True)['sourceEligible'])
        self.assertFalse(a.history_exists(self.output))

    def test_missing_packages_and_bad_market_remain_rejected(self):
        for changes in ({'packages': []}, {'packages': [None]}, {'market_status': 'MARKET WARNING'}, {'upstream_status': 503}):
            with self.subTest(changes=changes):
                self.write({**self.feed, **changes})
                self.assertFalse(self.collect(skip_unavailable=True)['sourceEligible'])

    def test_corrupt_archive_is_not_swallowed_by_skip_mode(self):
        with patch.object(c, 'history_exists', side_effect=a.ArchiveError('corrupt')):
            with self.assertRaises(a.ArchiveError):
                self.collect(skip_unavailable=True)

    def test_write_failure_is_not_swallowed_by_skip_mode(self):
        with patch.object(c, 'append_history', side_effect=a.ArchiveError('write failed')):
            with self.assertRaises(a.ArchiveError):
                self.collect(skip_unavailable=True)

    def test_status_file_does_not_claim_missing_source_is_fresh(self):
        self.source.unlink()
        report = self.root / 'research' / 'status.json'
        result = c.main(['--source', str(self.source), '--skip-unavailable', '--status-file', str(report)])
        self.assertEqual(result, 0)
        status = json.loads(report.read_text())
        self.assertEqual(status['status'], 'CURRENT_INPUT_SKIPPED')
        self.assertIsNone(status['sourceCheckedAt'])
        self.assertFalse(status['sourceEligible'])

    def test_git_backfill_can_be_committed_after_current_warning(self):
        # Reproduces the production failing sequence with real Git and archive.
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True)
        git('init', '-q')
        git('config', 'user.name', 'Offline regression')
        git('config', 'user.email', 'test@localhost')
        mirror = self.root / 'buy-feed.json'
        mirror.write_text(json.dumps(self.feed))
        git('add', 'buy-feed.json')
        git('commit', '-qm', 'Actual recorded healthy quote')
        healthy_commit = git('rev-parse', 'HEAD').stdout.strip()
        mirror.write_text(json.dumps({**self.feed, 'status': 'BUY FEED WARNING', 'ok': False}))
        git('add', 'buy-feed.json')
        git('commit', '-qm', 'Actual recorded warning')
        subprocess.run([sys.executable, str(ROOT / 'scripts/backfill_radar_snapshots.py')],
                       cwd=self.root, check=True, capture_output=True, text=True)
        digest = a.history_digest(self.output)
        result = c.collect_snapshot(mirror, now=self.now, skip_unavailable=True)
        self.assertEqual(result['status'], 'CURRENT_INPUT_SKIPPED')
        self.assertEqual(a.history_digest(self.output), digest)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            a.stage_history(Path('calibration/radar-snapshots.jsonl'))
        finally:
            os.chdir(previous)
        git('commit', '-qm', 'Persist valid backfill despite unavailable current input')
        self.assertEqual(self.rows()[0]['source_commit'], healthy_commit)
        self.assertEqual(len(self.rows()), 1)


class WorkflowSafetyTests(unittest.TestCase):
    def test_existing_schedule_and_single_producer_are_preserved(self):
        text = (ROOT / '.github/workflows/collect-calibration.yml').read_text()
        self.assertEqual(text.count("cron: '7,37 * * * *'"), 1)
        self.assertIn('--source-kind SCHEDULED_RELAY_READ --skip-unavailable', text)
        self.assertIn('research/radar-collection-status.json', text)
        self.assertIn('git add research/radar-archive-status.json research/radar-collection-status.json', text)
        self.assertIn('--connect-timeout 10 --max-time 60', text)
        self.assertNotIn('continue-on-error:', text)
        self.assertNotIn('curl -L', text)
        self.assertNotIn('--location', text)
        self.assertNotIn('api2.nicehash.com', text)
        self.assertNotIn('secrets.', text)


if __name__ == '__main__':
    unittest.main()
