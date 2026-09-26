import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_palladium_m_day_comparison as m


def row(at, h=1e12, duration=7200, price=.001, ev=80, version="v1", paired=True):
    work = h * duration / price
    return {
        "package": "Palladium M", "currency": "BTC", "coin": "LTC", "mergeCoin": "DOGE",
        "quoteAt": at, "relayVersion": version, "priceNative": price,
        "hashrateHps": h, "durationSeconds": duration, "workPerNative": work,
        "feedExpectedReturnPercent": ev, "primaryDifficulty": 100.0, "mergeDifficulty": 50.0,
        "pairStatus": "PAIRED" if paired else "STALE_MARKET",
        "marketPriceRaw": 2.0 if paired else None,
        "marketUnitSignature": ["TH/s", 1, 1] if paired else None,
        "currentSignal": "WAIT", "mathStatus": "WARNING",
    }


class TestDayComparison(unittest.TestCase):
    def test_units_and_local_day(self):
        rows = [row("2026-09-06T22:30:00Z"), row("2026-09-07T10:00:00Z", h=2e12, ev=90)]
        r = m.build(rows, ("2026-09-07",), "Europe/Ljubljana")
        d = r["days"][0]
        self.assertEqual(d["quoteCount"], 2)
        self.assertAlmostEqual(d["workFor001BtcThHours"]["min"], 2.0)
        self.assertAlmostEqual(d["workFor001BtcThHours"]["max"], 4.0)
        self.assertAlmostEqual(d["impliedCostBtcPerThDay"]["max"], 0.012)
        self.assertEqual(d["pairedQuoteCount"], 2)
        self.assertFalse(r["guardrails"]["usesAdminData"])
        self.assertFalse(r["guardrails"]["usesOrderOutcomes"])

    def test_missing_day_is_explicit(self):
        r = m.build([row("2026-09-07T10:00:00Z")], ("2026-09-20",), "Europe/Ljubljana")
        self.assertEqual(r["days"][0]["status"], "NO_DATA")

    def test_wrong_package_excluded(self):
        x = row("2026-09-07T10:00:00Z")
        x["package"] = "Palladium S"
        r = m.build([x], ("2026-09-07",), "Europe/Ljubljana")
        self.assertEqual(r["sourcePairRowsForPackage"], 0)

    def test_unpaired_keeps_quote_metrics_not_market_raw(self):
        r = m.build([row("2026-09-07T10:00:00Z", paired=False)], ("2026-09-07",), "Europe/Ljubljana")
        d = r["days"][0]
        self.assertEqual(d["quoteCount"], 1)
        self.assertEqual(d["pairedQuoteCount"], 0)
        self.assertEqual(d["pairedPublicMarketPriceRaw"]["count"], 0)

    def test_series_normalization_respects_version(self):
        rows = [
            row("2026-09-07T10:00:00Z", ev=80, version="v1"),
            row("2026-09-20T10:00:00Z", h=2e12, ev=90, version="v2"),
        ]
        r = m.build(rows, ("2026-09-07", "2026-09-20"), "Europe/Ljubljana")
        self.assertFalse(r["crossDayRelayVersionComparable"])
        for d in r["days"]:
            self.assertAlmostEqual(d["costVsSameSeriesMedianPercent"]["median"], 0)
            self.assertAlmostEqual(d["expectedReturnVsSameSeriesMedianPercentagePoints"]["median"], 0)


if __name__ == "__main__":
    unittest.main()
