import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "candidate_eval",
    ROOT / "scripts" / "evaluate_prospective_candidate_v1.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CANDIDATE_PATH = ROOT / "research" / "palladium-s-momentum-candidate-v1.json"


def candidate():
    return json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))


def context_row(
    *,
    start="2026-09-20T01:00:00+00:00",
    outcome="MISS",
    work=1.0,
    expected=1.0,
    q24=1.0,
    q7=1.0,
    relay="2.9.0",
):
    return {
        "orderStartTs": start,
        "packageName": "Palladium S",
        "currencyMarket": "BTC",
        "coin": "LTC",
        "mergeCoin": "DOGE",
        "outcome": outcome,
        "gitFeedPreEntry": {
            "status": "FRESH_ENDPOINT",
            "relayVersion": relay,
            "baselineToleranceSensitivity": {
                "20": [
                    {
                        "horizonMinutes": 60,
                        "status": "MATCHED",
                        "relayVersion": relay,
                        "packageWorkChangePercent": work,
                        "expectedReturnChangePercentagePoints": expected,
                        "qualityVs24hChangePercentagePoints": q24,
                        "qualityVs7dChangePercentagePoints": q7,
                        "mergeDifficultyChangePercent": 10.0,
                        "mergeModelHitProbabilityChangePercentagePoints": 0.1,
                        "primaryModelHitProbabilityChangePercentagePoints": 0.01,
                    }
                ]
            },
        },
    }


class ProspectiveCandidateV1Tests(unittest.TestCase):
    def test_committed_candidate_is_hash_locked(self):
        c = candidate()
        self.assertEqual(m.candidate_hash(c), m.LOCKED_CANDIDATE_HASH)
        self.assertIs(m.validate_candidate(c), c)

    def test_mutating_threshold_invalidates_v1(self):
        c = copy.deepcopy(candidate())
        c["coreMomentumRules"]["packageWorkChangePercent"]["value"] = 0.1
        with self.assertRaises(ValueError):
            m.validate_candidate(c)

    def test_registration_era_rows_are_excluded(self):
        doc = {
            "orders": [
                context_row(
                    start="2026-09-19T23:59:59+00:00",
                    outcome="HIT",
                )
            ]
        }
        result = m.evaluate(candidate(), doc)
        self.assertEqual(result["postValidationScopeOrdersSeen"], 0)
        self.assertEqual(result["eligibleOrders"], 0)
        self.assertEqual(result["status"], "AWAITING_PROSPECTIVE_ORDERS")

    def test_core_partial_and_no_match_counts_are_prospective_only(self):
        doc = {
            "orders": [
                context_row(outcome="HIT", work=2, expected=3, q24=4, q7=5),
                context_row(
                    start="2026-09-20T02:00:00+00:00",
                    outcome="MISS",
                    work=2,
                    expected=3,
                    q24=4,
                    q7=-1,
                ),
                context_row(
                    start="2026-09-20T03:00:00+00:00",
                    outcome="MISS",
                    work=-1,
                    expected=-1,
                    q24=1,
                    q7=-1,
                ),
            ]
        }
        result = m.evaluate(candidate(), doc)
        self.assertEqual(result["eligibleOrders"], 3)
        self.assertEqual(result["eligibleHits"], 1)
        self.assertEqual(result["classificationCounts"]["CORE_MATCH"], 1)
        self.assertEqual(result["classificationCounts"]["PARTIAL_3_OF_4"], 1)
        self.assertEqual(result["classificationCounts"]["NO_MATCH"], 1)
        self.assertEqual(result["performanceGate"]["status"], "AWAITING_MINIMUM_PROSPECTIVE_SAMPLE")
        self.assertIsNone(result["performanceGate"]["coreMatchHitRatePercent"])
        self.assertFalse(result["performanceGate"]["performanceConclusionAllowed"])
        self.assertFalse(result["canRaiseSignal"])

    def test_ineligible_window_does_not_become_candidate_match(self):
        row = context_row(outcome="HIT")
        row["gitFeedPreEntry"]["baselineToleranceSensitivity"]["20"][0]["status"] = (
            "BASELINE_TOO_FAR_FROM_TARGET"
        )
        result = m.evaluate(candidate(), {"orders": [row]})
        self.assertEqual(result["eligibleOrders"], 0)
        self.assertEqual(
            result["ineligibleReasons"]["NO_MATCHED_60M_WINDOW_AT_20M_TOLERANCE"],
            1,
        )

    def test_performance_rates_remain_hidden_until_frozen_gate(self):
        rows = []
        for i in range(30):
            rows.append(
                context_row(
                    start=f"2026-09-{20 + i // 24:02d}T{i % 24:02d}:00:00+00:00",
                    outcome="HIT" if i < 3 else "MISS",
                    work=1 if i % 2 == 0 else -1,
                    expected=1 if i % 2 == 0 else -1,
                    q24=1 if i % 2 == 0 else -1,
                    q7=1 if i % 2 == 0 else -1,
                )
            )
        result = m.evaluate(candidate(), {"orders": rows})
        self.assertTrue(result["performanceGate"]["performanceConclusionAllowed"])
        self.assertEqual(result["performanceGate"]["status"], "GATE_MET")
        self.assertIsNotNone(result["performanceGate"]["coreMatchHitRatePercent"])
        self.assertIsNotNone(result["performanceGate"]["notCoreMatchHitRatePercent"])

    def test_output_contains_no_private_order_timestamps(self):
        result = m.evaluate(candidate(), {"orders": [context_row()]})
        text = json.dumps(result, sort_keys=True)
        self.assertNotIn("orderStartTs", text)
        self.assertNotIn("2026-09-20T01:00:00", text)
        self.assertFalse(result["containsPrivateOrderTimestamps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
