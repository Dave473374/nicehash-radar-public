import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "signal_register",
    ROOT / "scripts" / "validate_research_signal_register.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ResearchSignalRegisterTests(unittest.TestCase):
    def test_real_register_is_valid_and_nonproduction_safe(self):
        result = m.validate(ROOT / "research" / "research-signal-register.json")
        self.assertGreaterEqual(result["signalCount"], 5)
        self.assertTrue(result["allNonProductionSignalsBlockedFromBuy"])
        self.assertGreaterEqual(result["statusCounts"]["TRACKING"], 1)
        self.assertGreaterEqual(result["statusCounts"]["CANDIDATE"], 1)

    def test_duplicate_signal_id_fails(self):
        source = json.loads(
            (ROOT / "research" / "research-signal-register.json").read_text()
        )
        source["signals"].append(dict(source["signals"][0]))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # Linked paths are resolved from repo_root, so use the real repo root.
            path = d / "register.json"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "duplicate signal id"):
                m.validate(path, repo_root=ROOT)

    def test_nonproduction_signal_cannot_affect_buy(self):
        source = json.loads(
            (ROOT / "research" / "research-signal-register.json").read_text()
        )
        source["signals"][0]["canAffectBuySignal"] = True
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "register.json"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "non-PRODUCTION signal may not affect BUY"):
                m.validate(path, repo_root=ROOT)

    def test_missing_evidence_path_fails(self):
        source = json.loads(
            (ROOT / "research" / "research-signal-register.json").read_text()
        )
        source["signals"][0]["linkedEvidence"] = ["research/does-not-exist.json"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "register.json"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "linked evidence path missing"):
                m.validate(path, repo_root=ROOT)

    def test_automatic_promotion_must_stay_disabled(self):
        source = json.loads(
            (ROOT / "research" / "research-signal-register.json").read_text()
        )
        source["governance"]["automaticPromotionAllowed"] = True
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "register.json"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "automaticPromotionAllowed must be false"):
                m.validate(path, repo_root=ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
