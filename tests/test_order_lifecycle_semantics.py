"""Synthetic lifecycle-status checks for order-level calibration."""
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


class OrderLifecycleSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.matcher = load_functions(
            "scripts/match_global_orders.py",
            {
                "get_lifecycle_status",
                "order_lifecycle_eligibility",
            },
        )
        self.collector = load_functions(
            "scripts/collect_completed_orders.py",
            {"first_value", "sanitize_reward", "sanitize_order"},
        )

    def test_completed_is_verified_eligible(self):
        result = self.matcher["order_lifecycle_eligibility"](
            {"lifecycleStatus": "completed"}
        )
        self.assertTrue(result["eligible"])
        self.assertTrue(result["verifiedCompleted"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["classification"], "VERIFIED_COMPLETED")

    def test_cancelled_and_expired_are_censored_not_miss(self):
        for status in ("CANCELLED", "EXPIRED", "ACTIVE", "CREATE_ERROR"):
            with self.subTest(status=status):
                result = self.matcher["order_lifecycle_eligibility"](
                    {"lifecycleStatus": status}
                )
                self.assertFalse(result["eligible"])
                self.assertFalse(result["verifiedCompleted"])
                self.assertEqual(
                    result["classification"],
                    "NON_COMPLETED_CENSORED",
                )

    def test_missing_status_is_legacy_unknown_not_verified_completed(self):
        result = self.matcher["order_lifecycle_eligibility"]({})
        self.assertTrue(result["eligible"])
        self.assertFalse(result["statusAvailable"])
        self.assertFalse(result["verifiedCompleted"])
        self.assertEqual(
            result["classification"],
            "LEGACY_STATUS_UNKNOWN",
        )

    def test_sanitizer_preserves_and_normalizes_status(self):
        row = self.collector["sanitize_order"](
            {
                "startTs": "2026-09-22T00:00:00Z",
                "packageName": "Palladium M",
                "status": "cancelled",
                "isReward": False,
            }
        )
        self.assertEqual(row["lifecycleStatus"], "CANCELLED")
        self.assertTrue(row["lifecycleStatusAvailable"])
        self.assertNotIn("orderId", json.dumps(row))

    def test_sanitizer_does_not_invent_missing_status(self):
        row = self.collector["sanitize_order"](
            {
                "startTs": "2026-09-22T00:00:00Z",
                "packageName": "Palladium M",
                "isReward": False,
            }
        )
        self.assertIsNone(row["lifecycleStatus"])
        self.assertFalse(row["lifecycleStatusAvailable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
