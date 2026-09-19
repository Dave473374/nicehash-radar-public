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

collector = load("collector", "scripts/collect_realized_blocks.py")
backfill = load("backfill", "scripts/backfill_public_easymining_hits.py")


def event(height=100, coin="BCH", package="Silver S"):
    return {
        "coin": coin,
        "blockHeight": height,
        "blockHash": f"{height:064x}",
        "payoutReward": "3.0",
        "payoutRewardBtc": "0.01",
        "time": 1_700_000_000_000,
        "createdTs": 1_700_000_000_000,
        "packageId": f"p-{height}",
        "packageName": package,
        "shared": False,
    }


class PublicHitArchiveTests(unittest.TestCase):
    def test_collector_normalises_supported_shapes(self):
        self.assertEqual(collector.normalise_page([1]), [1])
        for key in ("list", "data", "items"):
            self.assertEqual(collector.normalise_page({key: [1]}), [1])

    def test_collector_dedupes_and_stops_empty(self):
        pages = {0: [event(100), event(99)], 1: [event(99)], 2: []}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.jsonl"
            r = collector.collect(out, 5, 50, fetcher=lambda p, l: pages.get(p, []), now="2026-09-19T00:00:00+00:00")
            rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(r["stopReason"], "EMPTY_PAGE")

    def test_collector_repeated_page_stops(self):
        same = [event(100)]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.jsonl"
            r = collector.collect(out, 5, 50, fetcher=lambda p, l: same, now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["stopReason"], "REPEATED_PAGE")

    def test_research_archive_is_local_only_and_no_miss_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "r.jsonl"
            dst = Path(d) / "h.jsonl"
            rows = [
                collector.to_record(event(100, "ZEC", "Bronze S"), "2026-09-19T00:00:00+00:00"),
                collector.to_record(event(101, "KAS", "Titanium S"), "2026-09-19T00:00:00+00:00"),
            ]
            src.write_text("".join(json.dumps(x) + "\n" for x in rows))
            result = backfill.build(src, dst)
            got = [json.loads(x) for x in dst.read_text().splitlines()]
        self.assertEqual(result["recordsStored"], 2)
        self.assertEqual({x["coin"] for x in got}, {"ZEC", "KAS"})
        self.assertTrue(all(x["canSupplyMissDenominator"] is False for x in got))
        self.assertTrue(all(x["canRaiseBuySignal"] is False for x in got))

    def test_event_id_stable(self):
        row = collector.to_record(event(100), "x")
        self.assertEqual(backfill.event_id(row), backfill.event_id(dict(row)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
