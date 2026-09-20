import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = load("soloblocks_collector", "scripts/collect_soloblocks_btc_hits.py")
compare = load("soloblocks_compare", "scripts/compare_soloblocks_nicehash.py")


def page(heights, include_noise=True):
    noise_before = '<a href="/block/999999">noise</a>' if include_noise else ""
    noise_after = '<a href="/block/888888">noise</a>' if include_noise else ""
    rows = "".join(
        f'<tr><td><a href="/block/{h}">#{h:,}</a></td><td>NiceHash EasyMining</td></tr>'
        for h in heights
    )
    return (
        "<html>" + noise_before +
        "<h2>Full Block History</h2><table>" + rows + "</table>"
        "<h2>Blocks by Pool</h2>" + noise_after + "</html>"
    )


class SoloBlocksResearchTests(unittest.TestCase):
    def test_extracts_only_history_section(self):
        heights = list(range(967775, 967765, -1))
        got = collector.extract_block_heights(page(heights))
        self.assertEqual(got, heights)
        self.assertNotIn(999999, got)
        self.assertNotIn(888888, got)

    def test_attribution_mismatch_fails_closed(self):
        html = (
            '<h2>Full Block History</h2>'
            '<a href="/block/100">100</a>'
            '<h2>Blocks by Pool</h2>'
        )
        with self.assertRaises(ValueError):
            collector.extract_block_heights(html)

    def test_collect_dedupes_and_stops_on_partial_page(self):
        p1 = list(range(110, 100, -1))
        p2 = [100, 99]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "solo.jsonl"
            result = collector.collect(
                out,
                5,
                fetcher=lambda p: p1 if p == 1 else p2,
                now="2026-09-20T00:00:00+00:00",
            )
            rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual(result["stopReason"], "PARTIAL_PAGE")
        self.assertEqual(result["recordsStored"], 12)
        self.assertEqual(rows[0]["blockHeight"], 110)
        self.assertFalse(rows[0]["canRaiseBuySignal"])
        self.assertFalse(rows[0]["canSupplyMissDenominator"])
        self.assertFalse(rows[0]["packageVariantKnown"])

    def test_collect_preserves_existing_record(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "solo.jsonl"
            old = collector.to_record(110, "old")
            out.write_text(json.dumps(old) + "\n")
            collector.collect(
                out,
                1,
                fetcher=lambda p: list(range(110, 100, -1)),
                now="new",
            )
            rows = [json.loads(x) for x in out.read_text().splitlines()]
        row110 = next(x for x in rows if x["blockHeight"] == 110)
        self.assertEqual(row110["collectedAt"], "old")

    def test_abc_report_and_onchain_verified_count(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            solo = d / "solo.jsonl"
            nh = d / "recent.json"
            ledger = d / "ledger.jsonl"
            out = d / "report.json"

            solo.write_text(
                json.dumps(collector.to_record(100, "x")) + "\n" +
                json.dumps(collector.to_record(90, "x")) + "\n"
            )
            nh.write_text(json.dumps([
                {"coin": "BTC", "blockHeight": 90},
                {"coin": "BTC", "blockHeight": 80},
                {"coin": "KAS", "blockHeight": 100},
            ]))
            ledger.write_text(json.dumps({
                "coin": "BTC",
                "blockHeight": 100,
                "status": "VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG",
            }) + "\n")

            report = compare.build(
                solo, nh, ledger, out,
                now="2026-09-20T00:00:00+00:00",
            )

        self.assertEqual(report["A_bothHeights"], [90])
        self.assertEqual(report["B_niceHashOnlyHeights"], [80])
        self.assertEqual(report["C_soloBlocksOnlyHeights"], [100])
        self.assertEqual(report["soloBlocksOnchainVerifiedCount"], 1)
        self.assertFalse(report["canRaiseBuySignal"])
        self.assertFalse(report["canSupplyMissDenominator"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
