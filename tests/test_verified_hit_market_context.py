import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verified_hit_market_context",
    ROOT / "scripts" / "build_verified_hit_market_context.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def hit(
    *,
    event_id="doge-1",
    package="Palladium M",
    coin="DOGE",
    at="2026-09-19 16:26:27",
    status="VERIFIED_ON_CHAIN_BLOCK_MATCH",
):
    return {
        "eventId": event_id,
        "coin": coin,
        "blockHeight": 6380995,
        "packageName": package,
        "packageId": "pkg",
        "onchainTimestamp": at,
        "status": status,
        "verificationStrength": "BLOCK_HEIGHT_HASH_PALLADIUM_CHAIN_EVENT",
        "explorer": "https://api.blockchair.com",
    }


def pair(
    *,
    package="Palladium M",
    quote="2026-09-19T16:20:53+00:00",
    observed="2026-09-19T16:20:54+00:00",
    status="PAIRED",
    signal="WAIT",
):
    return {
        "package": package,
        "size": "M",
        "currency": "BTC",
        "coin": "LTC",
        "mergeCoin": "DOGE",
        "marketAlgorithm": "SCRYPT",
        "relayVersion": "2.9.0",
        "quoteAt": quote,
        "observedAt": observed,
        "priceNative": 0.001,
        "durationSeconds": 7200,
        "hashrateHps": 1.6e12,
        "workPerNative": 1.15e19,
        "feedExpectedReturnPercent": 84.0,
        "mathStatus": "WARNING",
        "primaryDifficulty": 88_000_000,
        "mergeDifficulty": 48_000_000,
        "costEur": 65.0,
        "currentSignal": signal,
        "pairStatus": status,
        "marketAt": "2026-09-19T16:19:00+00:00",
        "marketAgeSeconds": 113,
        "marketPriceRaw": 6.1e-7,
        "marketOrders": 10,
        "marketSpeedRaw": 123,
        "relativeValueLogIndex": 29.0,
    }


def episode(
    *,
    package="Palladium M",
    entry_at="2026-09-19T16:10:00+00:00",
    available_at="2026-09-19T16:10:01+00:00",
    recorded_at="2026-09-19T16:40:00+00:00",
    last_observed="2026-09-19T16:22:00+00:00",
):
    return {
        "id": "ep1",
        "cohort": "EXPLORATORY",
        "sourceRevision": False,
        "entry": {
            "package": package,
            "coin": "LTC",
            "quoteAt": entry_at,
            "availableAt": available_at,
            "features": {
                "divergencePercent": 10.0,
                "marketMovePercent": 6.0,
                "ticketCostPerWorkMovePercent": 0.0,
            },
            "signature": [
                package,
                "BTC",
                "LTC",
                "SCRYPT",
                "DOGE",
                "2.9.0",
            ],
        },
        "episode": {
            "status": "CENSORED_GAP_OR_SERIES",
            "lastObservedAt": last_observed,
        },
        "firstRecordedAt": recorded_at,
    }


class VerifiedHitMarketContextTests(unittest.TestCase):
    def now(self):
        return datetime(2026, 9, 19, 17, 0, tzinfo=timezone.utc)

    def test_doge_matches_palladium_merge_coin_exact_package(self):
        rows, report = m.build_context([hit()], [pair()], [], self.now())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["marketContextStatus"], "MATCHED_FRESH_EXACT_PACKAGE")
        self.assertEqual(row["marketContext"]["primaryCoin"], "LTC")
        self.assertEqual(row["marketContext"]["mergeCoin"], "DOGE")
        self.assertEqual(row["marketContext"]["currentSignal"], "WAIT")
        self.assertFalse(row["entryTimeEligible"])
        self.assertFalse(row["canSupplyMissDenominator"])
        self.assertFalse(row["canRaiseSignal"])
        self.assertEqual(report["freshExactMarketMatchCount"], 1)
        self.assertEqual(report["signalAtFreshHitContextCounts"]["WAIT"], 1)
        self.assertEqual(
            report["verdict"],
            "DESCRIPTIVE_SUCCESS_CONTEXT_ONLY_NO_VERIFIED_INCREMENTAL_EDGE",
        )

    def test_future_observation_is_not_used(self):
        p = pair(
            quote="2026-09-19T16:20:00+00:00",
            observed="2026-09-19T16:27:00+00:00",
        )
        rows, _ = m.build_context([hit()], [p], [], self.now())
        self.assertEqual(
            rows[0]["marketContextStatus"],
            "NO_CAUSAL_EXACT_PACKAGE_QUOTE",
        )

    def test_exact_package_only_does_not_substitute_m_for_l(self):
        rows, report = m.build_context(
            [hit(package="Palladium L")],
            [pair(package="Palladium M")],
            [],
            self.now(),
        )
        self.assertEqual(rows[0]["marketContextStatus"], "NO_EXACT_PACKAGE_SERIES")
        self.assertEqual(report["freshExactMarketMatchCount"], 0)

    def test_stale_exact_quote_is_not_called_fresh(self):
        p = pair(
            quote="2026-09-19T15:50:00+00:00",
            observed="2026-09-19T15:50:01+00:00",
        )
        rows, _ = m.build_context([hit()], [p], [], self.now())
        self.assertEqual(rows[0]["marketContextStatus"], "STALE_EXACT_PACKAGE_QUOTE")

    def test_invalid_market_pair_is_not_called_fresh(self):
        rows, _ = m.build_context(
            [hit()],
            [pair(status="STALE_MARKET")],
            [],
            self.now(),
        )
        self.assertEqual(
            rows[0]["marketContextStatus"],
            "EXACT_PACKAGE_QUOTE_NO_VALID_MARKET_PAIR",
        )

    def test_lag_episode_tracks_feature_vs_recording_time(self):
        rows, report = m.build_context(
            [hit()],
            [pair()],
            [episode()],
            self.now(),
        )
        lag = rows[0]["lagContext"]
        self.assertEqual(lag["status"], "RECENT_EXACT_PACKAGE_LAG_EPISODE")
        self.assertTrue(lag["featureAvailableBeforeHit"])
        self.assertFalse(lag["episodeRecordedBeforeHit"])
        self.assertFalse(lag["hitWithinObservedEpisodeWindow"])
        self.assertEqual(report["lagContextCounts"]["recentExactPackageEpisode"], 1)
        self.assertEqual(report["lagContextCounts"]["featureAvailableBeforeHit"], 1)
        self.assertNotIn(
            "episodeRecordedBeforeHit",
            report["lagContextCounts"],
        )

    def test_lag_episode_must_match_exact_package(self):
        rows, _ = m.build_context(
            [hit()],
            [pair()],
            [episode(package="Palladium S")],
            self.now(),
        )
        self.assertEqual(
            rows[0]["lagContext"]["status"],
            "NO_RECENT_EXACT_PACKAGE_LAG_EPISODE",
        )

    def test_pre_hit_windows_use_only_exact_package_causal_pairs(self):
        early = pair(
            quote="2026-09-19T15:20:00+00:00",
            observed="2026-09-19T15:20:01+00:00",
            signal="WAIT",
        )
        early.update({
            "workPerNative": 1.00e19,
            "marketPriceRaw": 5.5e-7,
            "primaryDifficulty": 90_000_000,
            "mergeDifficulty": 50_000_000,
            "feedExpectedReturnPercent": 80.0,
        })
        middle = pair(
            quote="2026-09-19T15:55:00+00:00",
            observed="2026-09-19T15:55:01+00:00",
            signal="WAIT",
        )
        middle.update({
            "workPerNative": 1.05e19,
            "marketPriceRaw": 5.8e-7,
            "primaryDifficulty": 89_000_000,
            "mergeDifficulty": 49_000_000,
            "feedExpectedReturnPercent": 82.0,
        })
        late = pair(
            quote="2026-09-19T16:20:53+00:00",
            observed="2026-09-19T16:20:54+00:00",
            signal="GOOD",
        )
        late.update({
            "workPerNative": 1.10e19,
            "marketPriceRaw": 6.1e-7,
            "primaryDifficulty": 88_000_000,
            "mergeDifficulty": 48_000_000,
            "feedExpectedReturnPercent": 84.0,
        })
        rows, report = m.build_context([hit()], [early, middle, late], [], self.now())
        ctx = rows[0]["preHitContext"]
        self.assertEqual(ctx["status"], "AVAILABLE")
        matched = {w["horizonMinutes"]: w for w in ctx["windows"] if w["status"] == "MATCHED"}
        self.assertIn(30, matched)
        self.assertIn(60, matched)
        self.assertAlmostEqual(matched[30]["workPerNativeChangePercent"], (1.10/1.05-1)*100, places=6)
        self.assertAlmostEqual(matched[60]["marketPriceRawChangePercent"], (6.1/5.5-1)*100, places=6)
        self.assertEqual(matched[30]["baselineSignal"], "WAIT")
        self.assertEqual(matched[30]["endpointSignal"], "GOOD")
        self.assertEqual(report["byCoin"]["DOGE"]["preHitContextAvailable"], 1)
        self.assertTrue(any(key.startswith("DOGE|Palladium M|") for key in report["preHitWindowSummary"]))

    def test_pre_hit_windows_never_use_future_observation(self):
        baseline = pair(
            quote="2026-09-19T15:55:00+00:00",
            observed="2026-09-19T16:30:00+00:00",
        )
        endpoint = pair(
            quote="2026-09-19T16:20:53+00:00",
            observed="2026-09-19T16:20:54+00:00",
        )
        rows, _ = m.build_context([hit()], [baseline, endpoint], [], self.now())
        windows = rows[0]["preHitContext"]["windows"]
        self.assertFalse(any(w.get("status") == "MATCHED" for w in windows))

    def test_pre_hit_windows_do_not_substitute_package_size(self):
        rows, _ = m.build_context(
            [hit(package="Palladium L")],
            [
                pair(package="Palladium M", quote="2026-09-19T15:55:00+00:00"),
                pair(package="Palladium M", quote="2026-09-19T16:20:53+00:00"),
            ],
            [],
            self.now(),
        )
        self.assertEqual(
            rows[0]["preHitContext"]["status"],
            "NO_CAUSAL_PAIRED_EXACT_PACKAGE_QUOTES",
        )

    def test_pre_hit_requires_fresh_paired_endpoint(self):
        old = pair(
            quote="2026-09-19T15:50:00+00:00",
            observed="2026-09-19T15:50:01+00:00",
        )
        rows, _ = m.build_context([hit()], [old], [], self.now())
        self.assertEqual(rows[0]["preHitContext"]["status"], "NO_FRESH_PAIRED_ENDPOINT")

    def test_non_verified_ledger_rows_are_excluded(self):
        rows, report = m.build_context(
            [hit(status="CONFLICT_BLOCK_HASH")],
            [pair()],
            [],
            self.now(),
        )
        self.assertEqual(rows, [])
        self.assertEqual(report["verifiedHitCount"], 0)
        self.assertEqual(report["counts"]["nonVerifiedLedgerRowsSkipped"], 1)

    def test_naive_explorer_timestamp_is_explicitly_assumed_utc(self):
        rows, _ = m.build_context([hit()], [pair()], [], self.now())
        self.assertEqual(
            rows[0]["hitTimeBasis"],
            "NAIVE_EXPLORER_TIME_ASSUMED_UTC",
        )
        self.assertEqual(
            rows[0]["hitAt"],
            "2026-09-19T16:26:27+00:00",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
