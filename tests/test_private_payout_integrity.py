"""Offline, synthetic-only private payout and pre-entry causality checks."""
import ast
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def functions(path, imports=True):
    tree = ast.parse((ROOT / path).read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef)
            or imports and isinstance(n, (ast.Import, ast.ImportFrom))]
    scope = {"MAX_ENTRY_SNAPSHOT_AGE_SECONDS": 900, "snapshot_points": []}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), scope)
    return scope


def order(rewards=None, hit=True):
    return {"startTs": "2026-09-20T12:00:00+00:00", "endTs": "2026-09-20T13:00:00+00:00",
            "packageName": "Palladium S", "currencyMarket": "BTC", "soloMiningCoin": "LTC",
            "soloMiningMergeCoin": "DOGE", "isReward": hit, "payedAmount": 0.0001,
            "packagePrice": 0.0001, "soloReward": [] if rewards is None else rewards}


def snapshot():
    return {"collected_at": "2026-09-20T11:56:00+00:00",
            "feed_generated_at": "2026-09-20T11:55:00+00:00", "feed": {
                "checked_at": "2026-09-20T11:55:00+00:00", "relay_version": "2.9.0",
                "packages": [{"name": "Palladium S", "size": "S", "price_btc": 0.0001,
                              "currency_market": "BTC", "primary_chain": {"currency": "LTC"},
                              "merge_chain": {"currency": "DOGE"}, "final_signal": "WAIT"}]}}


class PrivatePayoutIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.m = functions("scripts/match_private_orders.py")
        self.s = functions("scripts/collect_completed_orders.py", imports=False)

    def test_numeric_parser_rejects_nonfinite_boolean_and_overflow(self):
        for value in (True, False, "NaN", "Infinity", "-Infinity", float("nan"),
                      float("inf"), 10 ** 1000, None, "", {}, []):
            self.assertIsNone(self.m["to_float"](value))
        self.assertEqual(self.m["to_float"]("0.0001"), 0.0001)

    def test_complete_multiple_rewards_sum(self):
        value, available = self.m["realized_return_btc"](
            order([{"payoutRewardBtc": "0.0002"}, {"payoutRewardBtc": "0.0001"}]), "HIT")
        self.assertTrue(available)
        self.assertAlmostEqual(value, 0.0003)

    def test_partial_missing_or_malformed_payout_never_complete(self):
        for leg in ({}, None, "bad", {"payoutRewardBtc": None}, {"payoutRewardBtc": "NaN"},
                    {"payoutRewardBtc": -0.1}, {"payoutRewardBtc": True}):
            with self.subTest(leg=leg):
                self.assertEqual(self.m["realized_return_btc"](
                    order([{"payoutRewardBtc": 0.0002}, leg]), "HIT"), (None, False))

    def test_sanitizer_keeps_missing_leg_without_private_identifiers(self):
        raw = order([{"payoutRewardBtc": 0.0002, "address": "PRIVATE"}, {}])
        raw["orderId"] = "PRIVATE"
        sanitized = self.s["sanitize_order"](raw)
        self.assertEqual(len(sanitized["soloReward"]), 2)
        self.assertIsNone(sanitized["soloReward"][1])
        self.assertNotIn("PRIVATE", json.dumps(sanitized))
        self.assertEqual(self.m["realized_return_btc"](sanitized, "HIT"), (None, False))

    def test_nonlist_rewards_cannot_become_known_zero(self):
        for rewards in ({}, "bad", 0):
            raw = order(rewards, False)
            sanitized = self.s["sanitize_order"](raw)
            self.assertEqual(self.m["realized_return_btc"](sanitized, "MISS"), (None, False))

    def test_known_miss_zero_and_unknown_hit_payout_remain_distinct(self):
        self.assertEqual(self.m["realized_return_btc"](order([], False), "MISS"), (0.0, True))
        self.assertEqual(self.m["realized_return_btc"](order([], True), "HIT"), (None, False))
        self.assertEqual(self.m["realized_return_btc"](order([], None), "UNKNOWN"), (None, False))

    def test_contradictory_miss_positive_payout_not_complete(self):
        self.assertEqual(self.m["realized_return_btc"](
            order([{"payoutRewardBtc": 0.0002}], False), "MISS"), (None, False))

    def test_zero_only_hit_and_overflow_sum_not_complete(self):
        for values in ([0], [1e308, 1e308]):
            self.assertEqual(self.m["realized_return_btc"](
                order([{"payoutRewardBtc": n} for n in values]), "HIT"), (None, False))

    def test_invalid_paid_amount_never_falls_back_to_nominal_price(self):
        for paid in (0, -1, "NaN", "Infinity", True):
            row = order(); row["payedAmount"] = paid
            self.assertIsNone(self.m["actual_native_cost"](row)[0])

    def test_usdt_conversion_is_finite_and_still_currency_aware(self):
        row = order(); row.update(currencyMarket="USDT", payedAmount=5)
        cost = self.m["cost_btc_equivalent"](row, {"price_native": 5, "price_btc_equiv": 0.00007})
        self.assertAlmostEqual(cost[1], 0.00007)
        row["payedAmount"] = 1e308
        self.assertIsNone(self.m["cost_btc_equivalent"](
            row, {"price_native": 1e-308, "price_btc_equiv": 1e308})[1])

    def set_history(self, snap):
        ts = self.m["snapshot_feed_time"](snap)
        self.m["snapshot_points"] = [(ts, snap)] if ts is not None else []

    def test_receipt_after_entry_cannot_be_backdated(self):
        s = snapshot(); s["collected_at"] = "2026-09-20T12:01:00+00:00"
        self.set_history(s)
        self.assertIsNone(self.m["find_match"](order([], False))[0])

    def test_missing_invalid_conflicting_source_does_not_use_receipt(self):
        s = snapshot(); s["feed"].pop("checked_at"); s.pop("feed_generated_at")
        self.assertIsNone(self.m["snapshot_feed_time"](s))
        s = snapshot(); s["feed"]["checked_at"] = "bad"
        self.assertIsNone(self.m["snapshot_feed_time"](s))
        s = snapshot(); s["feed_generated_at"] = "2026-09-20T11:54:00+00:00"
        self.assertIsNone(self.m["snapshot_feed_time"](s))
        self.assertIsNone(self.m["parse_ts"]("2026-09-20T11:55:00"))

    def test_fresh_entry_and_legacy_schema_still_match(self):
        s = snapshot(); self.set_history(s)
        self.assertIsNotNone(self.m["find_match"](order([], False))[0])
        s["feed"]["relay_version"] = "2.8.2"
        s["feed"]["packages"][0].pop("currency_market")
        self.set_history(s)
        self.assertEqual(self.m["find_match"](order([], False))[0]["radarCurrencySource"],
                         "LEGACY_2_8_2_BTC_ONLY_SCHEMA")

    def test_end_to_end_private_matcher_counts_without_printing_private_values(self):
        raw = [order([{"payoutRewardBtc": 0.0002}]),
               order([{"payoutRewardBtc": 0.0002}, {}]), order([], False), order([], None)]
        rows = [self.s["sanitize_order"](r) for r in raw]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orders.json").write_text(json.dumps({"list": rows}))
            (root / "radar.jsonl").write_text(json.dumps(snapshot()) + "\n")
            env = dict(os.environ, PRIVATE_ORDERS_PATH=str(root / "orders.json"),
                       RADAR_HISTORY_PATH=str(root / "radar.jsonl"), PRIVATE_MATCH_OUTPUT=str(root / "out.json"))
            run = subprocess.run([sys.executable, str(ROOT / "scripts/match_private_orders.py")],
                                 env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((root / "out.json").read_text())
            self.assertEqual(result["overall"]["hits"], 2)
            self.assertEqual(result["overall"]["misses"], 1)
            self.assertEqual(result["overall"]["unknownOutcomes"], 1)
            self.assertEqual(result["overall"]["roiOrders"], 2)
            self.assertIsNone(result["matches"][1]["realizedRoiPercent"])
            self.assertEqual(result["matches"][2]["realizedRoiPercent"], -100)
            for private in ("2026-09-20", "0.0002", "Palladium"):
                self.assertNotIn(private, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
