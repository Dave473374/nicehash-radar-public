import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "private_market_context",
    ROOT / "scripts" / "match_private_orders_market_context.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def private_match(
    *,
    start="2026-09-20T00:10:00+00:00",
    snapshot="2026-09-20T00:05:00+00:00",
    package="Palladium S",
    coin="LTC",
    merge="DOGE",
    currency="BTC",
    outcome="MISS",
):
    return {
        "orderStartTs": start,
        "orderEndTs": "2026-09-20T01:10:00+00:00",
        "packageName": package,
        "coin": coin,
        "mergeCoin": merge,
        "currencyMarket": currency,
        "actualCostBtcEquivalent": 0.0001,
        "realizedReturnBtc": 0.0 if outcome == "MISS" else 0.001,
        "roiAvailable": outcome in {"HIT", "MISS"},
        "outcome": outcome,
        "snapshotFeedTime": snapshot,
        "snapshotAgeMinutes": 5.0,
        "finalSignal": "WAIT",
        "expectedReturnPercent": 88.0,
        "qualityVs24hPercent": 2.0,
        "qualityVs7dPercent": 1.0,
    }


def pair(
    *,
    quote="2026-09-20T00:05:00+00:00",
    observed="2026-09-20T00:05:01+00:00",
    package="Palladium S",
    coin="LTC",
    merge="DOGE",
    currency="BTC",
    status="PAIRED",
    signal="WAIT",
):
    return {
        "package": package,
        "currency": currency,
        "coin": coin,
        "mergeCoin": merge,
        "quoteAt": quote,
        "observedAt": observed,
        "pairStatus": status,
        "currentSignal": signal,
        "feedExpectedReturnPercent": 88.1,
        "marketAlgorithm": "SCRYPT",
        "relayVersion": "2.9.0",
        "priceNative": 0.0001,
        "costEur": 7.0,
        "durationSeconds": 3600,
        "hashrateHps": 3e11,
        "workPerNative": 1.08e19,
        "marketAt": "2026-09-20T00:04:00+00:00",
        "marketAgeSeconds": 60,
        "marketPriceRaw": 6.1e-7,
        "marketOrders": 90,
        "marketSpeedRaw": 2e14,
        "relativeValueLogIndex": 29.0,
        "primaryDifficulty": 88_000_000,
        "mergeDifficulty": 33_000_000,
        "mathStatus": "WARNING",
    }


def episode(
    *,
    package="Palladium S",
    entry="2026-09-20T00:00:00+00:00",
    available="2026-09-20T00:00:01+00:00",
    recorded="2026-09-20T00:01:00+00:00",
    last_observed="2026-09-20T00:20:00+00:00",
):
    return {
        "id": "ep1",
        "sourceRevision": False,
        "cohort": "VALIDATION",
        "entry": {
            "package": package,
            "coin": "LTC",
            "quoteAt": entry,
            "availableAt": available,
            "features": {"divergencePercent": 8.0},
            "signature": [package, "BTC", "LTC", "SCRYPT", "DOGE", "2.9.0"],
        },
        "episode": {
            "status": "OPEN",
            "lastObservedAt": last_observed,
        },
        "firstRecordedAt": recorded,
    }


PROTOCOL = {
    "version": 1,
    "validationStart": "2026-09-20T00:00:00Z",
    "packages": ["Palladium S", "Silver S", "Silver 5", "Silver 20"],
}


class PrivateEntryMarketContextTests(unittest.TestCase):
    def build(self, matches, pairs=None, episodes=None, protocol=None):
        return m.build(
            {"matches": matches},
            pairs or [],
            episodes or [],
            protocol or PROTOCOL,
        )

    def test_same_entry_snapshot_quote_is_preferred(self):
        result = self.build([private_match()], [pair()])
        row = result["matches"][0]
        self.assertEqual(row["entryMarketContextStatus"], "MATCHED_SAME_ENTRY_QUOTE")
        self.assertEqual(row["entryMarketContext"]["alignment"], "SAME_ENTRY_RADAR_QUOTE")
        self.assertEqual(row["entryMarketContext"]["snapshotQuoteDeltaSeconds"], 0.0)
        self.assertTrue(row["entryMarketContext"]["entrySignalConsistent"])

    def test_latest_causal_fallback_is_labeled_separately(self):
        result = self.build(
            [private_match(snapshot="2026-09-20T00:04:00+00:00")],
            [pair(quote="2026-09-20T00:05:00+00:00")],
        )
        row = result["matches"][0]
        self.assertEqual(
            row["entryMarketContextStatus"],
            "MATCHED_CAUSAL_FALLBACK_QUOTE",
        )
        self.assertEqual(row["entryMarketContext"]["alignment"], "LATEST_CAUSAL_FALLBACK")

    def test_future_observation_is_never_used(self):
        result = self.build(
            [private_match()],
            [pair(observed="2026-09-20T00:11:00+00:00")],
        )
        self.assertEqual(
            result["matches"][0]["entryMarketContextStatus"],
            "NO_CAUSAL_MARKET_QUOTE_FOR_ENTRY",
        )

    def test_exact_package_is_required(self):
        result = self.build(
            [private_match(package="Palladium L")],
            [pair(package="Palladium M")],
        )
        self.assertEqual(
            result["matches"][0]["entryMarketContextStatus"],
            "NO_EXACT_PACKAGE_MARKET_SERIES",
        )

    def test_present_private_merge_coin_must_agree(self):
        result = self.build(
            [private_match(merge="DOGE")],
            [pair(merge="BELLS")],
        )
        self.assertEqual(
            result["matches"][0]["entryMarketContextStatus"],
            "NO_CAUSAL_MARKET_QUOTE_FOR_ENTRY",
        )

    def test_missing_private_merge_coin_can_use_exact_primary_series(self):
        result = self.build(
            [private_match(merge=None)],
            [pair(merge="DOGE")],
        )
        self.assertEqual(
            result["matches"][0]["entryMarketContextStatus"],
            "MATCHED_SAME_ENTRY_QUOTE",
        )

    def test_unpaired_entry_market_quote_is_not_called_valid(self):
        result = self.build(
            [private_match()],
            [pair(status="STALE_MARKET")],
        )
        self.assertEqual(
            result["matches"][0]["entryMarketContextStatus"],
            "ENTRY_QUOTE_NO_VALID_MARKET_PAIR",
        )

    def test_pre_validation_order_is_exploratory_even_with_episode(self):
        match = private_match(
            start="2026-09-19T23:50:00+00:00",
            snapshot="2026-09-19T23:45:00+00:00",
        )
        p = pair(
            quote="2026-09-19T23:45:00+00:00",
            observed="2026-09-19T23:45:01+00:00",
        )
        ep = episode(
            entry="2026-09-19T23:40:00+00:00",
            available="2026-09-19T23:40:01+00:00",
            recorded="2026-09-19T23:41:00+00:00",
            last_observed="2026-09-19T23:55:00+00:00",
        )
        result = self.build([match], [p], [ep])
        row = result["matches"][0]
        self.assertFalse(row["protocolValidationEligible"])
        self.assertEqual(
            row["entryLagContext"]["protocolStatus"],
            "PRE_VALIDATION_START_EXPLORATORY_ONLY",
        )
        self.assertFalse(row["entryLagContext"]["registeredLiveExposure"])

    def test_post_registration_episode_is_live_only_if_recorded_before_entry(self):
        result = self.build(
            [private_match()],
            [pair()],
            [episode(recorded="2026-09-20T00:01:00+00:00")],
        )
        row = result["matches"][0]
        self.assertTrue(row["protocolValidationEligible"])
        self.assertTrue(row["entryLagContext"]["featureAvailableBeforeOrderEntry"])
        self.assertTrue(row["entryLagContext"]["episodeRecordedBeforeOrderEntry"])
        self.assertTrue(row["entryLagContext"]["registeredLiveExposure"])

        late = self.build(
            [private_match()],
            [pair()],
            [episode(recorded="2026-09-20T00:11:00+00:00")],
        )["matches"][0]
        self.assertFalse(late["entryLagContext"]["episodeRecordedBeforeOrderEntry"])
        self.assertFalse(late["entryLagContext"]["registeredLiveExposure"])

    def test_package_outside_locked_protocol_is_not_validation_eligible(self):
        match = private_match(package="Palladium M")
        p = pair(package="Palladium M")
        result = self.build([match], [p])
        row = result["matches"][0]
        self.assertFalse(row["protocolPackageEligible"])
        self.assertFalse(row["protocolValidationEligible"])
        self.assertEqual(
            row["entryLagContext"]["protocolStatus"],
            "PACKAGE_NOT_IN_LOCKED_LAG_PROTOCOL",
        )

    def test_descriptive_stats_keep_hit_miss_and_weighted_roi(self):
        miss = private_match(outcome="MISS")
        hit = private_match(
            start="2026-09-20T00:20:00+00:00",
            snapshot="2026-09-20T00:15:00+00:00",
            outcome="HIT",
        )
        p1 = pair()
        p2 = pair(
            quote="2026-09-20T00:15:00+00:00",
            observed="2026-09-20T00:15:01+00:00",
        )
        result = self.build([miss, hit], [p1, p2])
        overall = result["overall"]
        self.assertEqual(overall["knownOutcomes"], 2)
        self.assertEqual(overall["hits"], 1)
        self.assertEqual(overall["misses"], 1)
        self.assertEqual(overall["hitRatePercent"], 50.0)
        self.assertEqual(overall["roiOrders"], 2)
        self.assertAlmostEqual(overall["realizedReturnMultiple"], 5.0, places=6)
        self.assertAlmostEqual(overall["realizedRoiPercent"], 400.0, places=4)

    def test_output_policy_is_non_production_and_ephemeral(self):
        result = self.build([private_match()], [pair()])
        self.assertFalse(result["privateDataPersistedToRepository"])
        self.assertFalse(result["privateDataUploadedAsArtifact"])
        self.assertFalse(result["canRaiseSignal"])
        self.assertFalse(result["automaticPurchase"])
        self.assertFalse(result["automaticCancel"])
        self.assertFalse(result["lockedLagProtocolChanged"])
        self.assertEqual(
            result["verdict"],
            "PRIVATE_DESCRIPTIVE_ENTRY_CONTEXT_ONLY_NO_AUTOMATIC_EDGE_CLAIM",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
