import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "palladium_controls",
    ROOT / "scripts" / "build_palladium_hit_control_comparison.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def pair(at, *, work=1.0e19, merge_diff=100.0, package="Palladium M"):
    dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
    return {
        "package": package,
        "size": "M",
        "currency": "BTC",
        "coin": "LTC",
        "mergeCoin": "DOGE",
        "marketAlgorithm": "SCRYPT",
        "relayVersion": "2.9.0",
        "quoteAt": dt.isoformat(),
        "observedAt": (dt + timedelta(seconds=1)).isoformat(),
        "pairStatus": "PAIRED",
        "priceNative": 0.001,
        "durationSeconds": 7200,
        "hashrateHps": work * 0.001 / 7200,
        "workPerNative": work,
        "marketPriceRaw": 6.0e-7,
        "marketOrders": 90,
        "marketSpeedRaw": 2e14,
        "primaryDifficulty": 88_000_000,
        "mergeDifficulty": merge_diff,
        "feedExpectedReturnPercent": 85.0,
        "currentSignal": "WAIT",
    }


def make_hit(pairs):
    prepared = m.pair_rows(pairs)
    hit_at = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)
    pre = m.build_pre_hit_context(prepared, hit_at)
    return {
        "eventId": "hit-1",
        "coin": "DOGE",
        "packageName": "Palladium M",
        "hitAt": hit_at.isoformat(),
        "marketContextStatus": "MATCHED_FRESH_EXACT_PACKAGE",
        "marketContext": {
            "status": "MATCHED_FRESH_EXACT_PACKAGE",
            "currency": "BTC",
            "primaryCoin": "LTC",
            "mergeCoin": "DOGE",
            "marketAlgorithm": "SCRYPT",
            "relayVersion": "2.9.0",
        },
        "preHitContext": pre,
    }


class PalladiumHitControlTests(unittest.TestCase):
    def rows(self):
        return [
            pair("2026-09-19T09:30:00+00:00", work=1.00e19, merge_diff=100),
            pair("2026-09-19T09:55:00+00:00", work=0.95e19, merge_diff=90),
            pair("2026-09-19T10:30:00+00:00", work=1.00e19, merge_diff=100),
            pair("2026-09-19T10:55:00+00:00", work=1.00e19, merge_diff=100),
            # This would be a plausible control endpoint but lies inside the
            # two-hour winner-exclusion window and must be removed.
            pair("2026-09-19T13:30:00+00:00", work=1.00e19, merge_diff=100),
            pair("2026-09-19T13:55:00+00:00", work=0.98e19, merge_diff=95),
            # Actual HIT pre-context.
            pair("2026-09-19T14:30:00+00:00", work=1.00e19, merge_diff=100),
            pair("2026-09-19T14:55:00+00:00", work=0.90e19, merge_diff=60),
        ]

    def test_hit_vs_controls_finds_lower_merge_difficulty_at_hit(self):
        pairs = self.rows()
        hit = make_hit(pairs)
        result = m.build([hit], pairs)
        self.assertEqual(result["verifiedHitCount"], 1)
        self.assertGreaterEqual(result["controlComparisonCount"], 1)
        thirty = result["comparisonByHorizon"]["30m"]
        self.assertEqual(thirty["matchedHits"], 1)
        self.assertGreaterEqual(thirty["matchedControls"], 1)
        self.assertLess(
            thirty["hitMinusControlMedianMergeDifficultyChangePercent"],
            0,
        )
        self.assertLessEqual(
            thirty["hitMedianPercentileAmongControlsMergeDifficultyChangePercent"],
            50,
        )
        self.assertFalse(result["canRaiseSignal"])

    def test_control_events_inside_winner_duration_window_are_excluded(self):
        pairs = self.rows()
        hit = make_hit(pairs)
        prepared = m.pair_rows(pairs)
        controls = m.matched_controls_for_hit(
            m.hit_rows([hit])[0],
            prepared,
            m.hit_rows([hit]),
        )
        times = [datetime.fromisoformat(row["syntheticEventAt"]) for row in controls]
        self.assertTrue(times)
        self.assertTrue(all(t < datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc) for t in times))

    def test_exact_package_is_required_for_controls(self):
        pairs = [
            pair("2026-09-19T14:30:00+00:00"),
            pair("2026-09-19T14:55:00+00:00"),
            pair("2026-09-19T10:30:00+00:00", package="Palladium S"),
            pair("2026-09-19T10:55:00+00:00", package="Palladium S"),
        ]
        hit = make_hit(pairs[:2])
        result = m.build([hit], pairs)
        self.assertEqual(result["controlComparisonCount"], 0)

    def test_non_palladium_m_hits_are_not_included(self):
        pairs = self.rows()
        hit = make_hit(pairs)
        hit["packageName"] = "Palladium L"
        result = m.build([hit], pairs)
        self.assertEqual(result["verifiedHitCount"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
