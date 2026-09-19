import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "git_feed_context",
    ROOT / "scripts" / "match_private_orders_git_feed_context.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def package(*, work_scale=1.0, primary_diff=100.0, merge_diff=100.0,
            expected=80.0, signal="WAIT", version="2.9.0"):
    p = {
        "name": "Palladium S",
        "size": "S",
        "currency_market": "BTC",
        "price_native": 0.0001,
        "price_btc": 0.0001,
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


def commit(*, checked, committed, p=None, version="2.9.0"):
    return {
        "committedAt": committed,
        "feed": {
            "relay_version": version,
            "checked_at": checked,
            "packages": [p or package(version=version)],
        },
    }


def order(*, result=False, start="2026-09-19T12:00:00+00:00"):
    return {
        "startTs": start,
        "packageName": "Palladium S",
        "currencyMarket": "BTC",
        "soloMiningCoin": "LTC",
        "soloMiningMergeCoin": "DOGE",
        "isReward": result,
    }


class GitFeedContextTests(unittest.TestCase):
    def test_hit_and_miss_generate_direct_30m_comparison(self):
        commits = [
            commit(
                checked="2026-09-19T11:30:00+00:00",
                committed="2026-09-19T11:31:00+00:00",
                p=package(work_scale=1.0, merge_diff=100, expected=80),
            ),
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(work_scale=1.2, merge_diff=60, expected=84, signal="GOOD"),
            ),
            commit(
                checked="2026-09-19T12:30:00+00:00",
                committed="2026-09-19T12:31:00+00:00",
                p=package(work_scale=1.0, merge_diff=100, expected=80),
            ),
            commit(
                checked="2026-09-19T12:55:00+00:00",
                committed="2026-09-19T12:56:00+00:00",
                p=package(work_scale=1.05, merge_diff=90, expected=81),
            ),
        ]
        doc = {
            "list": [
                order(result=True),
                order(result=False, start="2026-09-19T13:00:00+00:00"),
            ]
        }
        result = m.analyze(doc, commits)
        summary = result["summary"]
        self.assertEqual(summary["byPackageHorizonOutcome"]["Palladium S|30m|HIT"]["matchedOrders"], 1)
        self.assertEqual(summary["byPackageHorizonOutcome"]["Palladium S|30m|MISS"]["matchedOrders"], 1)
        comp = summary["hitMinusMiss"]["Palladium S|30m"]
        self.assertEqual(comp["hitOrders"], 1)
        self.assertEqual(comp["missOrders"], 1)
        self.assertLess(comp["hitMinusMissMergeDifficultyChangePercent"], 0)
        self.assertFalse(summary["canRaiseSignal"])

    def test_commit_after_entry_cannot_supply_endpoint(self):
        commits = [
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T12:01:00+00:00",
            )
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        self.assertEqual(
            result["orders"][0]["gitFeedPreEntry"]["status"],
            "NO_CAUSAL_EXACT_GIT_FEED_POINT",
        )

    def test_checked_at_after_entry_cannot_supply_endpoint(self):
        commits = [
            commit(
                checked="2026-09-19T12:01:00+00:00",
                committed="2026-09-19T12:02:00+00:00",
            )
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        self.assertEqual(
            result["orders"][0]["gitFeedPreEntry"]["status"],
            "NO_CAUSAL_EXACT_GIT_FEED_POINT",
        )

    def test_stale_endpoint_is_excluded_from_windows(self):
        commits = [
            commit(
                checked="2026-09-19T11:30:00+00:00",
                committed="2026-09-19T11:31:00+00:00",
            )
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        ctx = result["orders"][0]["gitFeedPreEntry"]
        self.assertEqual(ctx["status"], "NO_FRESH_ENDPOINT")
        self.assertEqual(ctx["windows"], [])

    def test_legacy_282_btc_only_schema_is_supported(self):
        commits = [
            commit(
                checked="2026-09-19T11:30:00+00:00",
                committed="2026-09-19T11:31:00+00:00",
                p=package(version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(version="2.8.2"),
                version="2.8.2",
            ),
        ]
        result = m.analyze({"list": [order()]}, commits)
        ctx = result["orders"][0]["gitFeedPreEntry"]
        self.assertEqual(ctx["status"], "FRESH_ENDPOINT")
        self.assertEqual(ctx["currencySource"], "LEGACY_2_8_2_BTC_ONLY_SCHEMA")

    def test_relay_version_change_breaks_window_series(self):
        commits = [
            commit(
                checked="2026-09-19T11:30:00+00:00",
                committed="2026-09-19T11:31:00+00:00",
                p=package(version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(version="2.9.0"),
                version="2.9.0",
            ),
        ]
        result = m.analyze({"list": [order()]}, commits)
        windows = result["orders"][0]["gitFeedPreEntry"]["windows"]
        row30 = next(x for x in windows if x["horizonMinutes"] == 30)
        self.assertNotEqual(row30["status"], "MATCHED")

    def test_window_status_summary_explains_missing_hit_baseline(self):
        commits = [
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(version="2.9.0"),
                version="2.9.0",
            ),
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        summary = result["summary"]
        self.assertEqual(
            summary["windowStatusByPackageHorizonOutcome"][
                "Palladium S|15m|HIT|NO_BASELINE_AT_OR_BEFORE_TARGET"
            ],
            1,
        )
        self.assertEqual(
            summary["windowStatusByPackageHorizonOutcome"][
                "Palladium S|30m|HIT|NO_BASELINE_AT_OR_BEFORE_TARGET"
            ],
            1,
        )
        self.assertEqual(
            summary["endpointSeriesByPackageOutcome"][
                "Palladium S|HIT|RELAY_2.9.0|CURRENCY_EXPLICIT_CURRENCY_MARKET"
            ],
            1,
        )

    def test_tolerance_sensitivity_can_recover_a_12_minute_baseline_without_changing_primary(self):
        commits = [
            commit(
                checked="2026-09-19T11:18:00+00:00",
                committed="2026-09-19T11:19:00+00:00",
                p=package(work_scale=1.0, merge_diff=100),
            ),
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(work_scale=1.2, merge_diff=60),
            ),
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        summary = result["summary"]
        self.assertNotIn(
            "Palladium S|30m|HIT",
            summary["byPackageHorizonOutcome"],
        )
        sensitivity = summary["baselineToleranceSensitivity"]
        self.assertEqual(
            sensitivity["windowStatusByTolerancePackageHorizonOutcome"][
                "10m|Palladium S|30m|HIT|BASELINE_TOO_FAR_FROM_TARGET"
            ],
            1,
        )
        self.assertEqual(
            sensitivity["windowStatusByTolerancePackageHorizonOutcome"][
                "15m|Palladium S|30m|HIT|MATCHED"
            ],
            1,
        )
        self.assertEqual(
            sensitivity["byTolerancePackageHorizonOutcome"][
                "15m|Palladium S|30m|HIT"
            ]["matchedOrders"],
            1,
        )
        self.assertFalse(sensitivity["canRaiseSignal"])

    def test_same_relay_endpoint_and_sensitivity_comparison_excludes_other_relay(self):
        commits = [
            commit(
                checked="2026-09-19T11:00:00+00:00",
                committed="2026-09-19T11:01:00+00:00",
                p=package(work_scale=1.0, merge_diff=100, version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
                p=package(work_scale=1.4, merge_diff=180, expected=95, version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T12:00:00+00:00",
                committed="2026-09-19T12:01:00+00:00",
                p=package(work_scale=1.0, merge_diff=100, version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T12:55:00+00:00",
                committed="2026-09-19T12:56:00+00:00",
                p=package(work_scale=1.05, merge_diff=95, expected=81, version="2.8.2"),
                version="2.8.2",
            ),
            commit(
                checked="2026-09-19T13:00:00+00:00",
                committed="2026-09-19T13:01:00+00:00",
                p=package(work_scale=1.0, merge_diff=100, version="2.9.0"),
                version="2.9.0",
            ),
            commit(
                checked="2026-09-19T13:55:00+00:00",
                committed="2026-09-19T13:56:00+00:00",
                p=package(work_scale=3.0, merge_diff=20, expected=50, version="2.9.0"),
                version="2.9.0",
            ),
        ]
        doc = {"list": [
            order(result=True, start="2026-09-19T12:00:00+00:00"),
            order(result=False, start="2026-09-19T13:00:00+00:00"),
            order(result=False, start="2026-09-19T14:00:00+00:00"),
        ]}
        result = m.analyze(doc, commits)
        summary = result["summary"]
        endpoint_key = (
            "Palladium S|RELAY_2.8.2|"
            "CURRENCY_LEGACY_2_8_2_BTC_ONLY_SCHEMA"
        )
        endpoint_comp = summary["endpointHitMinusMissByRelay"][endpoint_key]
        self.assertEqual(endpoint_comp["hitOrders"], 1)
        self.assertEqual(endpoint_comp["missOrders"], 1)

        sensitivity = summary["baselineToleranceSensitivity"]
        key = (
            "20m|Palladium S|60m|RELAY_2.8.2|"
            "CURRENCY_LEGACY_2_8_2_BTC_ONLY_SCHEMA"
        )
        comp = sensitivity["hitMinusMissByRelay"][key]
        self.assertEqual(comp["hitOrders"], 1)
        self.assertEqual(comp["missOrders"], 1)

    def test_summary_has_no_order_timestamps_or_amounts(self):
        commits = [
            commit(
                checked="2026-09-19T11:55:00+00:00",
                committed="2026-09-19T11:56:00+00:00",
            )
        ]
        result = m.analyze({"list": [order(result=True)]}, commits)
        summary_text = str(result["summary"])
        self.assertNotIn("startTs", summary_text)
        self.assertNotIn("12:00:00", summary_text)
        self.assertFalse(result["canRaiseSignal"])
        self.assertFalse(result["privateDataPersistedToRepository"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
