"""Synthetic checks for Palladium M aggregate signal exposure."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "palladium_m_signal_exposure",
    ROOT / "scripts" / "build_palladium_m_signal_exposure.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def snap(source, receipt, signal, currency="BTC", coin="LTC"):
    return {
        "collected_at": receipt,
        "feed_generated_at": source,
        "feed": {
            "checked_at": source,
            "relay_version": "test",
            "packages": [{
                "name": "Palladium M",
                "currency_market": currency,
                "primary_chain": {"currency": coin},
                "final_signal": signal,
            }],
        },
    }


class PalladiumMAggregateExposureTests(unittest.TestCase):
    def write(self, rows):
        d = tempfile.TemporaryDirectory()
        path = Path(d.name) / "history.jsonl"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        self.addCleanup(d.cleanup)
        return path

    def test_receipt_time_starts_exposure_not_source_time(self):
        path = self.write([
            snap(
                "2026-09-20T10:00:00Z",
                "2026-09-20T10:05:00Z",
                "GOOD",
            )
        ])
        points = m.load_points(path)
        rows = m.build_exposure(points, "UTC", 0.8, 0.0 + 0.001)
        self.assertEqual(len(points), 1)
        self.assertEqual(rows[0]["signalMinutes"]["GOOD"], 15.0)

    def test_stale_source_is_rejected(self):
        path = self.write([
            snap(
                "2026-09-20T10:00:00Z",
                "2026-09-20T10:16:00Z",
                "GOOD",
            )
        ])
        self.assertEqual(m.load_points(path), [])

    def test_wrong_package_coin_or_currency_is_rejected(self):
        path = self.write([
            snap("2026-09-20T10:00:00Z", "2026-09-20T10:01:00Z", "GOOD", coin="DOGE"),
            snap("2026-09-20T10:02:00Z", "2026-09-20T10:03:00Z", "GOOD", currency="USDT"),
        ])
        self.assertEqual(m.load_points(path), [])

    def test_mixed_day_is_not_aggregate_eligible(self):
        path = self.write([
            snap("2026-09-20T00:00:00Z", "2026-09-20T00:00:00Z", "GOOD"),
            snap("2026-09-20T00:10:00Z", "2026-09-20T00:10:00Z", "WAIT"),
        ])
        points = m.load_points(path)
        rows = m.build_exposure(points, "UTC", 0.80, 0.001)
        self.assertEqual(rows[0]["dominantSignal"], "WAIT")
        self.assertLess(rows[0]["dominantSignalFraction"], 0.80)
        self.assertFalse(rows[0]["aggregateEligible"])

    def test_duplicate_receipt_conflict_is_dropped(self):
        path = self.write([
            snap("2026-09-20T10:00:00Z", "2026-09-20T10:01:00Z", "GOOD"),
            snap("2026-09-20T10:00:30Z", "2026-09-20T10:01:00Z", "WAIT"),
        ])
        self.assertEqual(m.load_points(path), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
