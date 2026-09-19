import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "private_radar_context",
    ROOT / "scripts" / "match_private_orders_radar_context.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def package(*, name="Palladium S", currency="BTC", version="2.9.0",
            work_scale=1.0, primary_diff=100.0, merge_diff=100.0,
            expected=80.0, signal="WAIT"):
    p = {
        "name": name,
        "size": "S" if name.endswith(" S") else "M",
        "currency_market": currency,
        "price_native": 0.0001 if name.endswith(" S") else 0.001,
        "price_btc": 0.0001 if name.endswith(" S") else 0.001,
        "duration_seconds": 3600,
        "package_hashrate_hps": 1e12 * work_scale,
        "primary_chain": {
            "currency": "LTC",
            "network_difficulty": primary_diff,
            "model_hit_probability_percent": 1.0 * work_scale,
        },
        "merge_chain": {
            "currency": "DOGE",
            "network_difficulty": merge_diff,
            "model_hit_probability_percent": 2.0 * work_scale,
        },
        "profitability": {"expected_return_percent": expected},
        "history_trend": {
            "expected_blocks_per_btc_vs_24h_percent": 5.0 * work_scale,
            "expected_blocks_per_btc_vs_7d_percent": 4.0 * work_scale,
        },
        "final_signal": signal,
    }
    if version == "2.8.2":
        p.pop("currency_market")
        p.pop("price_native")
    return p


def snapshot(at, *, p=None, version="2.9.0", observed=None):
    q = datetime.fromisoformat(at.replace("Z", "+00:00"))
    o = datetime.fromisoformat((observed or at).replace("Z", "+00:00"))
    return {
        "collected_at": o.isoformat(),
        "feed_generated_at": q.isoformat(),
        "feed": {
            "ok": True,
            "status": "BUY FEED OK",
            "relay_version": version,
            "checked_at": q.isoformat(),
            "packages": [p or package(version=version)],
        },
    }


def order(*, start="2026-09-19T12:00:00+00:00", result=False,
          name="Palladium S"):
    return {
        "startTs": start,
        "endTs": "2026-09-19T13:00:00+00:00",
        "packageName": name,
        "currencyMarket": "BTC",
        "soloMiningCoin": "LTC",
        "soloMiningMergeCoin": "DOGE",
        "isReward": result,
    }


class PrivateRadarOnlyTests(unittest.TestCase):
    def test_fresh_order_builds_30_and_60_minute_windows(self):
        snaps = [
            snapshot("2026-09-19T11:00:00+00:00",
                     p=package(work_scale=1.0, merge_diff=100, expected=80)),
            snapshot("2026-09-19T11:30:00+00:00",
                     p=package(work_scale=1.1, merge_diff=80, expected=82)),
            snapshot("2026-09-19T11:55:00+00:00",
                     p=package(work_scale=1.2, merge_diff=60, expected=84, signal="GOOD")),
        ]
        result = m.build({"list": [order()]}, snaps)
        row = result["orders"][0]["radarOnlyPreEntry"]
        self.assertEqual(row["status"], "FRESH_ENDPOINT")
        self.assertEqual(row["endpointAgeBucket"], "FRESH_LE_15M")
        windows = {w["horizonMinutes"]: w for w in row["windows"] if w["status"] == "MATCHED"}
        self.assertIn(30, windows)
        self.assertIn(60, windows)
        self.assertAlmostEqual(windows[30]["mergeDifficultyChangePercent"], -25.0, places=6)
        self.assertAlmostEqual(windows[60]["packageWorkChangePercent"], 20.0, places=6)

    def test_stale_hit_is_bucketed_and_never_gets_strict_windows(self):
        snaps = [
            snapshot("2026-09-19T11:30:00+00:00"),
        ]
        result = m.build({"list": [order(result=True)]}, snaps)
        row = result["orders"][0]["radarOnlyPreEntry"]
        self.assertEqual(row["status"], "STALE_ENDPOINT_DESCRIPTIVE_ONLY")
        self.assertEqual(row["endpointAgeBucket"], "STALE_15_30M")
        self.assertEqual(row["windows"], [])
        summary = result["summary"]
        self.assertEqual(
            summary["endpointAgeBucketsByPackageOutcome"]["Palladium S|HIT|STALE_15_30M"],
            1,
        )

    def test_future_observed_snapshot_is_not_causal(self):
        snaps = [
            snapshot(
                "2026-09-19T11:55:00+00:00",
                observed="2026-09-19T12:01:00+00:00",
            ),
        ]
        result = m.build({"list": [order()]}, snaps)
        self.assertEqual(
            result["orders"][0]["radarOnlyPreEntry"]["status"],
            "NO_CAUSAL_EXACT_RADAR_POINT",
        )

    def test_legacy_282_btc_snapshot_is_accepted_only_under_frozen_contract(self):
        legacy = snapshot(
            "2026-09-19T11:55:00+00:00",
            p=package(version="2.8.2"),
            version="2.8.2",
        )
        result = m.build({"list": [order()]}, [legacy])
        ctx = result["orders"][0]["radarOnlyPreEntry"]
        self.assertEqual(ctx["status"], "FRESH_ENDPOINT")
        self.assertEqual(ctx["currencySource"], "LEGACY_2_8_2_BTC_ONLY_SCHEMA")

    def test_unknown_missing_currency_schema_is_not_inferred(self):
        p = package()
        p.pop("currency_market")
        snap = snapshot("2026-09-19T11:55:00+00:00", p=p, version="unknown")
        result = m.build({"list": [order()]}, [snap])
        self.assertEqual(
            result["orders"][0]["radarOnlyPreEntry"]["status"],
            "NO_CAUSAL_EXACT_RADAR_POINT",
        )

    def test_summary_separates_hit_and_miss_without_order_identifiers(self):
        snaps = [
            snapshot("2026-09-19T11:30:00+00:00", p=package(merge_diff=100)),
            snapshot("2026-09-19T11:55:00+00:00", p=package(merge_diff=50)),
        ]
        result = m.build(
            {"list": [order(result=True), order(result=False)]},
            snaps,
        )
        summary = result["summary"]
        self.assertEqual(summary["ordersByPackageOutcome"]["Palladium S|HIT"], 1)
        self.assertEqual(summary["ordersByPackageOutcome"]["Palladium S|MISS"], 1)
        self.assertNotIn("startTs", str(summary))
        self.assertFalse(summary["canRaiseSignal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
