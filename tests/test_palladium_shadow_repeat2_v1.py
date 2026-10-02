from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import palladium_shadow_trial as core
import palladium_shadow_repeat2_v1 as repeat


T = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def state(last_seconds_ago=60, *, expired=False, suspended=None):
    start = T - timedelta(hours=1)
    end = T - timedelta(seconds=1) if expired else T + timedelta(hours=71)
    return {
        "startedAt": start.isoformat(),
        "expiresAt": end.isoformat(),
        "lastReceiptAt": (T - timedelta(seconds=last_seconds_ago)).isoformat(),
        "suspendedHttpStatus": suspended,
    }


class PalladiumShadowRepeat2V1Tests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "HISTORY": core.HISTORY,
            "PUBLIC_HISTORY": core.PUBLIC_HISTORY,
            "STATE": core.STATE,
            "REPORT": core.REPORT,
            "LATEST_ECONOMICS": core.LATEST_ECONOMICS,
            "model_code_digest": core.model_code_digest,
        }

    def tearDown(self):
        for key, value in self.original.items():
            setattr(core, key, value)

    def test_exact_frozen_protocol_reused(self):
        protocol = core.load_protocol()
        self.assertEqual(core.digest(protocol), repeat.EXPECTED_PROTOCOL_HASH)
        self.assertEqual(protocol["rules"], core.RULES)
        self.assertFalse(repeat.manifest()["frozenRuleChanged"])

    def test_repeat2_storage_is_isolated(self):
        repeat.configure_core()
        self.assertEqual(core.HISTORY, repeat.HISTORY)
        self.assertEqual(core.PUBLIC_HISTORY, repeat.PUBLIC_HISTORY)
        self.assertEqual(core.STATE, repeat.STATE)
        self.assertEqual(core.REPORT, repeat.REPORT)
        self.assertNotEqual(core.STATE, Path("research/palladium-shadow-repeat-v1-state.json"))
        self.assertNotEqual(core.HISTORY, Path("calibration/palladium-shadow-repeat-v1-history.jsonl"))

    def test_model_hash_binds_wrapper_and_manifest(self):
        digest = repeat.repeat_model_code_digest()
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, repeat._ORIGINAL_MODEL_CODE_DIGEST())

    def test_no_kv_contract_fails_closed(self):
        good = {
            "status": "BUY FEED OK",
            "relay_version": "2.9.0",
            "shadow_sampling_mode": "NO_KV",
            "shadow_history_access": "NONE",
            "history_status": "HISTORY BYPASSED",
            "history_saved": False,
            "history_key": None,
            "packages": [],
        }
        self.assertIs(repeat.validate_shadow_feed(good), good)
        for key, bad in {
            "shadow_sampling_mode": "NORMAL",
            "shadow_history_access": "RADAR_HISTORY",
            "history_status": "HISTORY SAVED",
            "history_saved": True,
            "history_key": "snapshot:1",
        }.items():
            with self.subTest(key=key):
                changed = dict(good)
                changed[key] = bad
                with self.assertRaises(ValueError):
                    repeat.validate_shadow_feed(changed)

    def test_manifest_freezes_collection_contract_not_signal_thresholds(self):
        m = repeat.manifest()
        c = m["collection"]
        self.assertEqual(c["trialHours"], 72)
        self.assertEqual(c["intervalSeconds"], 60)
        self.assertEqual(c["samplesPerRun"], 31)
        self.assertEqual(c["targetCoveragePercent"], 90)
        self.assertFalse(c["targetIsGuarantee"])
        for key in (
            "canRaiseSignal", "canSendNotifications", "automaticPurchase",
            "currentProductionModelChanged", "adminApiUsed", "privateApiUsed",
        ):
            self.assertIs(m[key], False)

    def test_watchdog_dispatches_if_activation_missing(self):
        with patch.object(repeat, "_load", return_value=({}, "code", None)),              patch.object(core, "utcnow", return_value=T):
            self.assertTrue(repeat.watchdog())

    def test_watchdog_skips_fresh_receipt(self):
        with patch.object(repeat, "_load", return_value=({}, "code", state(60))),              patch.object(core, "utcnow", return_value=T):
            self.assertFalse(repeat.watchdog())

    def test_watchdog_recovers_stale_receipt(self):
        with patch.object(repeat, "_load", return_value=({}, "code", state(1200))),              patch.object(core, "utcnow", return_value=T):
            self.assertTrue(repeat.watchdog())

    def test_watchdog_never_restarts_complete_or_suspended(self):
        for s in (state(1200, expired=True), state(1200, suspended=429)):
            with patch.object(repeat, "_load", return_value=({}, "code", s)),                  patch.object(core, "utcnow", return_value=T):
                self.assertFalse(repeat.watchdog())


if __name__ == "__main__":
    unittest.main()
