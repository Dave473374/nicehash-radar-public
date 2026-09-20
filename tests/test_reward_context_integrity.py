"""Synthetic, offline regression tests for the existing collector and matcher."""
import ast
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
AT = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def iso(minutes=0):
    return (AT + timedelta(minutes=minutes)).isoformat()


def package(currency="BTC"):
    return {"name": "Palladium S", "size": "S", "currency_market": currency,
            "price_btc": 0.0001, "primary_chain": {"currency": "LTC"},
            "merge_chain": {"currency": "DOGE"}, "final_signal": "WAIT"}


def feed(checked=None):
    return {"status": "BUY FEED OK", "ok": True, "upstream_status": 200,
            "market_status": "MARKET OK", "relay_version": "2.9.0",
            "checked_at": checked or iso(-2), "packages": [package()]}


def snapshot(source=-2, receipt=-1):
    return {"collected_at": iso(receipt), "feed_generated_at": iso(source),
            "feed": feed(iso(source))}


def event():
    return {"eventId": "synthetic", "time": int(AT.timestamp() * 1000),
            "packageName": "Palladium S", "coins": ["LTC", "DOGE"],
            "rewards": [], "rewardCount": 2, "mergedMining": True,
            "totalPayoutRewardBtc": 0.0002}


def collector_functions():
    tree = ast.parse((ROOT / "scripts/collect_calibration.py").read_text())
    # Load only definitions; importing the script itself would run the collector.
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    scope = {"MAX_LIVE_FEED_AGE_SECONDS": 900}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "collector-functions", "exec"), scope)
    return scope


class CollectorFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.validate = collector_functions()["validate_feed"]

    def test_fresh_feed_and_exact_boundary(self):
        self.validate(feed(), AT)
        self.validate(feed(iso(-15)), AT)

    def test_old_future_missing_naive_or_invalid_source_rejected(self):
        for source in (iso(-15.01), iso(0.01), None, "2026-09-20T11:59:00", "bad", 123):
            with self.subTest(source=source):
                f = feed(); f["checked_at"] = source
                with self.assertRaises(AssertionError):
                    self.validate(f, AT)

    def test_bad_shapes_and_status_rejected(self):
        for value in ([], None, {"packages": []}):
            with self.assertRaises(AssertionError):
                self.validate(value, AT)
        for key, value in (("ok", False), ("upstream_status", 500), ("packages", [None])):
            f = feed(); f[key] = value
            with self.assertRaises(AssertionError):
                self.validate(f, AT)

    def test_real_collector_preserves_history_on_stale_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "calibration").mkdir()
            path = root / "calibration/radar-snapshots.jsonl"
            path.write_text("")
            source = root / "buy-feed.json"
            script = ROOT / "scripts/collect_calibration.py"
            source.write_text(json.dumps(feed()))  # Intentionally historic.
            first = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True)
            self.assertNotEqual(first.returncode, 0)
            self.assertEqual(path.read_text(), "")
            f = feed((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat())
            source.write_text(json.dumps(f))
            for _ in range(2):
                good = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True)
                self.assertEqual(good.returncode, 0, good.stderr.decode())
            rows = path.read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["feed_generated_at"], f["checked_at"])
            before = path.read_bytes()
            f["packages"][0]["price_btc"] = float("nan")
            source.write_text(json.dumps(f))
            bad = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(path.read_bytes(), before)


    def test_already_backfilled_stale_feed_is_noop_not_new_live_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "calibration").mkdir()
            f = feed()
            h = collector_functions()["canonical_feed_sha256"](f)
            history = root / "calibration/radar-snapshots.jsonl"
            before = json.dumps({"collected_at": iso(-1), "feed_sha256": h, "feed": f}) + "\n"
            history.write_text(before)
            (root / "buy-feed.json").write_text(json.dumps(f))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/collect_calibration.py")],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already present", result.stdout)
            self.assertEqual(history.read_text(), before)


class RewardContextTests(unittest.TestCase):
    def run_match(self, snaps, ev=None, success=True):
        ev = event() if ev is None else ev
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snapshots.jsonl").write_text("".join(json.dumps(s) + "\n" for s in snaps))
            (root / "events.json").write_text(json.dumps([ev]))
            output = root / "matches.jsonl"; output.write_text("PREVIOUS")
            env = dict(os.environ, RADAR_SNAPSHOTS_FILE=str(root / "snapshots.jsonl"),
                       MINING_EVENTS_FILE=str(root / "events.json"), MINING_EVENT_MATCH_OUTPUT=str(output))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/match_mining_events.py")],
                                    env=env, capture_output=True, text=True, timeout=15)
            if not success:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_text(), "PREVIOUS")
                return None
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["entry_time_eligible"])
            self.assertFalse(rows[0]["can_supply_miss_denominator"])
            self.assertEqual(rows[0]["evidence_role"], "REWARD_TIME_CONTEXT_ONLY")
            return rows[0]

    def test_fresh_merged_context_and_source_age(self):
        row = self.run_match([snapshot()])
        self.assertTrue(row["radar_matched"])
        self.assertEqual(row["snapshot_age_seconds"], 60)
        self.assertEqual(row["snapshot_feed_age_seconds"], 120)
        self.assertEqual(row["total_payout_reward_btc"], event()["totalPayoutRewardBtc"])

    def test_recent_capture_cannot_refresh_old_feed(self):
        self.assertFalse(self.run_match([snapshot(-30, -1)])["radar_matched"])

    def test_missing_source_never_falls_back_to_receipt(self):
        s = snapshot(); s.pop("feed_generated_at"); s["feed"].pop("checked_at")
        self.assertFalse(self.run_match([s])["radar_matched"])

    def test_invalid_canonical_time_not_replaced_by_valid_alternative(self):
        s = snapshot(); s["feed"]["checked_at"] = "bad"
        self.assertFalse(self.run_match([s])["radar_matched"])

    def test_conflicting_source_times_rejected(self):
        s = snapshot(); s["feed_generated_at"] = iso(-5)
        self.assertFalse(self.run_match([s])["radar_matched"])

    def test_source_after_capture_or_receipt_after_event_rejected(self):
        for s in (snapshot(1, -1), snapshot(-1, -2), snapshot(-2, 1)):
            self.assertFalse(self.run_match([s])["radar_matched"])

    def test_exact_fifteen_minute_boundary(self):
        self.assertTrue(self.run_match([snapshot(-15, -14)])["radar_matched"])
        self.assertFalse(self.run_match([snapshot(-15.01, -14)])["radar_matched"])

    def test_naive_receipt_and_unhealthy_feed_rejected(self):
        s = snapshot(); s["collected_at"] = "2026-09-20T11:59:00"
        self.assertFalse(self.run_match([s])["radar_matched"])
        s = snapshot(); s["feed"]["ok"] = False
        self.assertFalse(self.run_match([s])["radar_matched"])

    def test_coin_and_explicit_currency_mismatch(self):
        for change in ({"coins": ["BTC"]}, {"coins": []}, {"coins": [None]}, {"currencyMarket": "USDT"}):
            e = event(); e.update(change)
            self.assertFalse(self.run_match([snapshot()], e)["radar_matched"])

    def test_ambiguous_package_currency_not_guessed(self):
        s = snapshot(); s["feed"]["packages"].append(package("USDT"))
        self.assertFalse(self.run_match([s])["radar_matched"])
        e = event(); e["currencyMarket"] = "BTC"
        self.assertTrue(self.run_match([s], e)["radar_matched"])

    def test_merge_chain_only_event_is_supported(self):
        e = event(); e["coins"] = ["DOGE"]
        self.assertTrue(self.run_match([snapshot()], e)["radar_matched"])

    def test_legacy_282_btc_contract_preserved_but_unknown_schema_rejected(self):
        s = snapshot(); s["feed"]["relay_version"] = "2.8.2"
        s["feed"]["packages"][0].pop("currency_market")
        self.assertTrue(self.run_match([s])["radar_matched"])
        s["feed"]["relay_version"] = "unknown"
        self.assertFalse(self.run_match([s])["radar_matched"])

    def test_conflicting_duplicate_capture_not_arbitrarily_selected(self):
        a = snapshot(); b = copy.deepcopy(a); b["feed"]["packages"][0]["final_signal"] = "GOOD"
        self.assertFalse(self.run_match([a, b])["radar_matched"])
        self.assertTrue(self.run_match([a, copy.deepcopy(a)])["radar_matched"])

    def test_latest_preceding_snapshot_selected_from_unsorted_history(self):
        row = self.run_match([snapshot(-2, -1), snapshot(-10, -9), snapshot(2, 3)])
        self.assertEqual(row["snapshot_time"], iso(-1))

    def test_invalid_numeric_output_does_not_truncate_previous_output(self):
        e = event(); e["totalPayoutRewardBtc"] = float("nan")
        self.run_match([snapshot()], e, success=False)


if __name__ == "__main__":
    unittest.main()
