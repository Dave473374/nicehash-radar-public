import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "git_feed_coverage",
    ROOT / "scripts" / "audit_private_order_git_feed_coverage.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def order(*, start="2026-09-19T12:00:00+00:00", result=True, name="Palladium S"):
    return {
        "startTs": start,
        "packageName": name,
        "currencyMarket": "BTC",
        "soloMiningCoin": "LTC",
        "soloMiningMergeCoin": "DOGE",
        "isReward": result,
    }


def feed(*, checked="2026-09-19T11:50:00+00:00", version="2.9.0",
         name="Palladium S", currency="BTC"):
    package = {
        "name": name,
        "size": "S" if name.endswith(" S") else "M",
        "currency_market": currency,
        "price_native": 0.0001,
        "price_btc": 0.0001,
        "primary_chain": {"currency": "LTC"},
        "merge_chain": {"currency": "DOGE"},
    }
    if version == "2.8.2":
        package.pop("currency_market")
        package.pop("price_native")
    return {
        "relay_version": version,
        "checked_at": checked,
        "packages": [package],
    }


class GitFeedCoverageTests(unittest.TestCase):
    def test_fresh_exact_git_feed_is_found(self):
        commits = [{
            "committedAt": "2026-09-19T11:55:00+00:00",
            "feed": feed(checked="2026-09-19T11:50:00+00:00"),
        }]
        result = m.analyze({"list": [order()]}, commits)
        self.assertEqual(
            result["gitFeedAgeBucketsByPackageOutcome"]["Palladium S|HIT|FRESH_LE_15M"],
            1,
        )
        self.assertEqual(
            result["freshExactGitFeedByPackageOutcome"]["Palladium S|HIT"],
            1,
        )

    def test_commit_after_entry_is_not_causal_even_if_feed_checked_before(self):
        commits = [{
            "committedAt": "2026-09-19T12:01:00+00:00",
            "feed": feed(checked="2026-09-19T11:55:00+00:00"),
        }]
        result = m.analyze({"list": [order()]}, commits)
        self.assertEqual(
            result["gitFeedAgeBucketsByPackageOutcome"]["Palladium S|HIT|NO_CAUSAL_GIT_COMMIT"],
            1,
        )

    def test_stale_git_feed_is_bucketed_not_called_fresh(self):
        commits = [{
            "committedAt": "2026-09-19T11:42:00+00:00",
            "feed": feed(checked="2026-09-19T11:35:00+00:00"),
        }]
        result = m.analyze({"list": [order()]}, commits)
        self.assertEqual(
            result["gitFeedAgeBucketsByPackageOutcome"]["Palladium S|HIT|STALE_15_30M"],
            1,
        )
        self.assertNotIn("Palladium S|HIT", result["freshExactGitFeedByPackageOutcome"])

    def test_legacy_282_btc_only_feed_is_accepted(self):
        commits = [{
            "committedAt": "2026-09-19T11:55:00+00:00",
            "feed": feed(
                checked="2026-09-19T11:50:00+00:00",
                version="2.8.2",
            ),
        }]
        result = m.analyze({"list": [order(result=False)]}, commits)
        self.assertEqual(
            result["gitFeedAgeBucketsByPackageOutcome"]["Palladium S|MISS|FRESH_LE_15M"],
            1,
        )

    def test_unknown_missing_currency_is_not_inferred(self):
        f = feed(checked="2026-09-19T11:50:00+00:00", version="unknown")
        f["packages"][0].pop("currency_market")
        commits = [{
            "committedAt": "2026-09-19T11:55:00+00:00",
            "feed": f,
        }]
        result = m.analyze({"list": [order()]}, commits)
        self.assertEqual(
            result["gitFeedAgeBucketsByPackageOutcome"][
                "Palladium S|HIT|NO_EXACT_CAUSAL_GIT_FEED"
            ],
            1,
        )

    def test_summary_contains_no_private_timestamps_or_amounts(self):
        commits = [{
            "committedAt": "2026-09-19T11:55:00+00:00",
            "feed": feed(checked="2026-09-19T11:50:00+00:00"),
        }]
        result = m.analyze({"list": [order()]}, commits)
        text = str(result)
        self.assertFalse(result["containsTimestamps"])
        self.assertFalse(result["containsAmountsOrRoi"])
        self.assertNotIn("startTs", text)
        self.assertNotIn("12:00:00", text)
        self.assertFalse(result["canRaiseSignal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
