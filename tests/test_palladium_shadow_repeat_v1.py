import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import palladium_shadow_trial as core
import palladium_shadow_repeat_v1 as repeat


class PalladiumShadowRepeatV1Tests(unittest.TestCase):
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

    def test_frozen_protocol_is_exactly_reused(self):
        protocol = core.load_protocol()
        self.assertEqual(core.digest(protocol), repeat.EXPECTED_PROTOCOL_HASH)
        self.assertEqual(protocol["rules"], core.RULES)
        self.assertFalse(repeat.manifest()["frozenRuleChanged"])

    def test_repeat_paths_are_isolated_from_completed_trial(self):
        repeat.configure_core()
        self.assertEqual(core.HISTORY, repeat.HISTORY)
        self.assertEqual(core.PUBLIC_HISTORY, repeat.PUBLIC_HISTORY)
        self.assertEqual(core.STATE, repeat.STATE)
        self.assertEqual(core.REPORT, repeat.REPORT)
        self.assertNotEqual(core.HISTORY, Path("calibration/palladium-shadow-history.jsonl"))
        self.assertNotEqual(core.STATE, Path("research/palladium-shadow-state.json"))

    def test_repeat_model_hash_binds_harness_and_manifest(self):
        digest = repeat.repeat_model_code_digest()
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, repeat._ORIGINAL_MODEL_CODE_DIGEST())

    def test_no_kv_contract_accepts_exact_response(self):
        data = {
            "status": "BUY FEED OK",
            "relay_version": "2.9.0",
            "shadow_sampling_mode": "NO_KV",
            "shadow_history_access": "NONE",
            "history_status": "HISTORY BYPASSED",
            "history_saved": False,
            "history_key": None,
            "packages": [],
        }
        self.assertIs(repeat.validate_shadow_feed(data), data)

    def test_no_kv_contract_fails_closed_on_each_marker(self):
        base = {
            "status": "BUY FEED OK",
            "relay_version": "2.9.0",
            "shadow_sampling_mode": "NO_KV",
            "shadow_history_access": "NONE",
            "history_status": "HISTORY BYPASSED",
            "history_saved": False,
            "history_key": None,
            "packages": [],
        }
        bad_values = {
            "shadow_sampling_mode": "NORMAL",
            "shadow_history_access": "RADAR_HISTORY",
            "history_status": "HISTORY SAVED",
            "history_saved": True,
            "history_key": "snapshot:1",
        }
        for key, value in bad_values.items():
            with self.subTest(key=key):
                data = dict(base)
                data[key] = value
                with self.assertRaises(ValueError):
                    repeat.validate_shadow_feed(data)

    def test_manifest_forbids_trading_and_private_sources(self):
        m = repeat.manifest()
        for key in (
            "canRaiseSignal", "canSendNotifications", "automaticPurchase",
            "currentProductionModelChanged", "adminApiUsed", "privateApiUsed",
        ):
            self.assertIs(m[key], False)


if __name__ == "__main__":
    unittest.main()
