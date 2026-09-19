import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("latency", ROOT / "scripts" / "build_alert_latency_report.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

T = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)


def pkg(name="Silver S", size="S", market="BTC", signal="BUY NOW", ev=97.0, math="PASS",
        complete=True, available=True, cost=7.0):
    return {
        "name": name, "size": size, "currency_market": market, "final_signal": signal,
        "available": available,
        "profitability": {"complete": complete, "expected_return_percent": ev},
        "math_consistency_shadow": {"status": math},
        "economics": {"package_cost_eur": cost},
    }


def snap(minutes, packages, version="2.9.0", engine="E"):
    return {
        "checked": T + timedelta(minutes=minutes),
        "available": T + timedelta(minutes=minutes + 1),
        "relayVersion": version,
        "decisionEngine": engine,
        "packages": packages,
    }


class AlertLatencyTests(unittest.TestCase):
    def test_primary_definition(self):
        self.assertTrue(m.primary(pkg()))
        self.assertTrue(m.primary(pkg(name="Silver 5", size="5", market="USDT")))
        self.assertTrue(m.primary(pkg(name="Silver 20", size="20", market="USDT")))
        self.assertFalse(m.primary(pkg(name="Silver M", size="M")))

    def test_verified_candidate_is_strict(self):
        self.assertTrue(m.verified_phone_candidate(pkg()))
        self.assertFalse(m.verified_phone_candidate(pkg(signal="GOOD")))
        self.assertFalse(m.verified_phone_candidate(pkg(ev=94.99)))
        self.assertFalse(m.verified_phone_candidate(pkg(math="WARNING")))
        self.assertFalse(m.verified_phone_candidate(pkg(math="CRITICAL")))
        self.assertTrue(m.verified_phone_candidate(pkg(math="NOT_APPLICABLE")))

    def test_secondary_never_drives_upgrade(self):
        rows = [
            snap(0, [pkg(name="Silver M", size="M")]),
            snap(5, [pkg(name="Silver M", size="M", signal="WAIT")]),
        ]
        self.assertEqual(m.evaluate(rows, T + timedelta(minutes=10))["resolvedWindowCount"], 0)

    def test_resolved_short_window(self):
        rows = [snap(0, [pkg()]), snap(5, [pkg()]), snap(10, [pkg(signal="WAIT")])]
        out = m.evaluate(rows, T + timedelta(minutes=12))
        self.assertEqual(out["resolvedWindowCount"], 1)
        self.assertEqual(out["shortWindowCount"], 1)
        self.assertFalse(out["fiveMinutePushRecommended"])

    def test_three_windows_two_short_trigger(self):
        rows = [
            snap(0, [pkg()]), snap(5, [pkg(signal="WAIT")]),
            snap(20, [pkg()]), snap(25, [pkg(signal="WAIT")]),
            snap(40, [pkg()]), snap(45, [pkg(signal="WAIT")]),
        ]
        out = m.evaluate(rows, T + timedelta(minutes=50))
        self.assertEqual(out["status"], "RECOMMENDED")
        self.assertTrue(out["fiveMinutePushRecommended"])

    def test_need_three_resolved_windows(self):
        rows = [
            snap(0, [pkg()]), snap(5, [pkg(signal="WAIT")]),
            snap(20, [pkg()]), snap(25, [pkg(signal="WAIT")]),
        ]
        self.assertEqual(m.evaluate(rows, T + timedelta(minutes=30))["status"], "INSUFFICIENT_EVIDENCE")

    def test_one_very_short_strong_can_trigger_after_denominator(self):
        rows = [
            snap(0, [pkg(signal="STRONG BUY")]), snap(5, [pkg(signal="WAIT")]),
            snap(20, [pkg()]), snap(25, [pkg()]), snap(30, [pkg()]), snap(35, [pkg()]),
            snap(40, [pkg()]), snap(45, [pkg()]), snap(50, [pkg()]), snap(55, [pkg()]),
            snap(60, [pkg()]), snap(65, [pkg()]), snap(70, [pkg(signal="WAIT")]),
            snap(90, [pkg()]), snap(95, [pkg()]), snap(100, [pkg()]), snap(105, [pkg()]),
            snap(110, [pkg()]), snap(115, [pkg()]), snap(120, [pkg()]), snap(125, [pkg()]),
            snap(130, [pkg()]), snap(135, [pkg()]), snap(140, [pkg(signal="WAIT")]),
        ]
        out = m.evaluate(rows, T + timedelta(minutes=150))
        self.assertTrue(out["fiveMinutePushRecommended"])
        self.assertEqual(out["veryShortStrongWindowCount"], 1)

    def test_gap_censors_instead_of_guessing(self):
        rows = [snap(0, [pkg()]), snap(20, [pkg(signal="WAIT")])]
        out = m.evaluate(rows, T + timedelta(minutes=25))
        self.assertEqual(out["resolvedWindowCount"], 0)
        self.assertEqual(out["windows"][0]["status"], "CENSORED")

    def test_open_window_not_counted_as_resolved(self):
        rows = [snap(0, [pkg()]), snap(5, [pkg()])]
        out = m.evaluate(rows, T + timedelta(minutes=6))
        self.assertEqual(out["resolvedWindowCount"], 0)
        self.assertEqual(out["windows"][0]["status"], "OPEN")

    def test_current_regime_only(self):
        rows = [
            snap(0, [pkg()], version="2.8.2"), snap(5, [pkg(signal="WAIT")], version="2.8.2"),
            snap(20, [pkg()], version="2.9.0"), snap(25, [pkg(signal="WAIT")], version="2.9.0"),
        ]
        out = m.evaluate(rows, T + timedelta(minutes=30))
        self.assertEqual(out["currentRegime"]["relayVersion"], "2.9.0")
        self.assertEqual(out["resolvedWindowCount"], 1)

    def test_load_rejects_late_capture(self):
        import json
        feed = {
            "status": "BUY FEED OK", "ok": True, "upstream_status": 200, "market_status": "MARKET OK",
            "checked_at": T.isoformat(), "relay_version": "2.9.0", "decision_engine": "E",
            "packages": [pkg()],
        }
        row = {"collected_at": (T + timedelta(minutes=8)).isoformat(), "feed": feed}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            p.write_text(json.dumps(row) + "\n")
            rows, counts = m.load_snapshots(p, T + timedelta(minutes=10))
        self.assertEqual(rows, [])
        self.assertEqual(counts["lateCapture"], 1)

    def test_duplicate_snapshot_conflict_is_rejected(self):
        import json
        base = {
            "status": "BUY FEED OK", "ok": True, "upstream_status": 200, "market_status": "MARKET OK",
            "checked_at": T.isoformat(), "relay_version": "2.9.0", "decision_engine": "E",
            "packages": [pkg()],
        }
        changed = dict(base)
        changed["packages"] = [pkg(ev=99.0)]
        rows = [
            {"collected_at": (T + timedelta(minutes=1)).isoformat(), "feed": base},
            {"collected_at": (T + timedelta(minutes=1)).isoformat(), "feed": changed},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            p.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            loaded, counts = m.load_snapshots(p, T + timedelta(minutes=2))
        self.assertEqual(loaded, [])
        self.assertEqual(counts["conflictingSnapshot"], 1)

    def test_report_cannot_raise_buy_signal(self):
        out = m.evaluate([snap(0, [pkg()])], T + timedelta(minutes=1))
        self.assertFalse(out["fiveMinutePushRecommended"])
        self.assertEqual(out["verdict"], "KEEP_HOURLY_FOR_NOW")


if __name__ == "__main__":
    unittest.main(verbosity=2)
