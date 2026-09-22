"""Synthetic checks for reward-event versus winning-order semantics."""
import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_functions(path, wanted):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    keep = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    scope = {}
    exec(
        compile(ast.Module(body=keep, type_ignores=[]), path, "exec"),
        scope,
    )
    return scope


class RewardOrderSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.global_match = load_functions(
            "scripts/match_global_orders.py",
            {
                "get_reward_record_count_with_availability",
                "get_had_reward",
                "make_hit_miss_stats",
            },
        )
        self.collector = load_functions(
            "scripts/collect_completed_orders.py",
            {"first_value", "sanitize_reward", "sanitize_order"},
        )

    def test_binary_hit_does_not_invent_reward_count_one(self):
        row = {"isReward": True}
        count, available, source = self.global_match[
            "get_reward_record_count_with_availability"
        ](row)
        self.assertIsNone(count)
        self.assertFalse(available)
        self.assertEqual(source, "UNAVAILABLE")
        self.assertIs(
            self.global_match["get_had_reward"](row),
            True,
        )

    def test_one_winning_order_can_have_many_reward_records(self):
        row = {
            "isReward": True,
            "rewardRecordCount": 217,
        }
        count, available, _ = self.global_match[
            "get_reward_record_count_with_availability"
        ](row)
        self.assertEqual(count, 217)
        self.assertTrue(available)
        self.assertIs(
            self.global_match["get_had_reward"](row),
            True,
        )

    def test_explicit_reward_count_can_infer_outcome_without_binary_field(self):
        self.assertIs(
            self.global_match["get_had_reward"](
                {"rewardRecordCount": 217}
            ),
            True,
        )
        self.assertIs(
            self.global_match["get_had_reward"](
                {"rewardRecordCount": 0}
            ),
            False,
        )

    def test_unknown_outcome_is_not_counted_as_miss(self):
        stats = self.global_match["make_hit_miss_stats"](
            [
                {"hadReward": True},
                {"hadReward": False},
                {"hadReward": None},
            ]
        )[0]
        self.assertEqual(stats["orders"], 3)
        self.assertEqual(stats["knownOutcomes"], 2)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["unknownOutcomes"], 1)
        self.assertEqual(stats["hitRatePercent"], 50.0)

    def test_sanitizer_preserves_count_availability(self):
        missing = self.collector["sanitize_order"](
            {
                "startTs": "2026-09-22T00:00:00Z",
                "packageName": "Titanium S",
                "isReward": True,
            }
        )
        self.assertIsNone(missing["rewardRecordCount"])
        self.assertFalse(missing["rewardRecordCountAvailable"])

        raw_rewards = [
            {"payoutRewardBtc": "0.00000001"}
            for _ in range(217)
        ]
        present = self.collector["sanitize_order"](
            {
                "startTs": "2026-09-22T00:00:00Z",
                "packageName": "Titanium S",
                "isReward": True,
                "soloReward": raw_rewards,
            }
        )
        self.assertEqual(present["rewardRecordCount"], 217)
        self.assertTrue(present["rewardRecordCountAvailable"])
        self.assertEqual(len(present["soloReward"]), 217)
        self.assertNotIn("orderId", json.dumps(present))


if __name__ == "__main__":
    unittest.main(verbosity=2)
