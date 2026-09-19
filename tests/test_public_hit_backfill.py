import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hit_backfill", ROOT / "scripts" / "backfill_public_easymining_hits.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def event(height=100, reward="3.0"):
    return {"coin":"BCH","blockHeight":height,"blockHash":f"{height:064x}","payoutReward":reward,"payoutRewardBtc":"0.01","time":1_700_000_000_000,"createdTs":1_700_000_000_000,"packageId":"p1","packageName":"Silver S","shared":False}


class PublicHitBackfillTests(unittest.TestCase):
    def test_normalise_supported_shapes(self):
        self.assertEqual(m.normalise_page([1]), [1])
        for key in ("list", "data", "items"):
            self.assertEqual(m.normalise_page({key:[1]}), [1])

    def test_event_id_stable(self):
        self.assertEqual(m.event_id(event()), m.event_id(dict(event())))

    def test_collect_stops_on_empty_and_dedupes(self):
        pages = {0:[event(100), event(99)], 1:[event(99)], 2:[]}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/"h.jsonl"
            r = m.collect(5, 50, out, fetcher=lambda p,l: pages.get(p, []), now="2026-09-19T00:00:00+00:00")
            rows=[json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual(len(rows),2)
        self.assertEqual(r["stopReason"],"EMPTY_PAGE")
        self.assertTrue(all(x["canSupplyMissDenominator"] is False for x in rows))

    def test_repeated_page_stops(self):
        same=[event(100)]
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/"h.jsonl"
            r=m.collect(5,50,out,fetcher=lambda p,l:same,now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["stopReason"],"REPEATED_PAGE")

    def test_existing_identity_conflict_refused(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/"h.jsonl"
            m.collect(1,50,out,fetcher=lambda p,l:[event(100)],now="2026-09-19T00:00:00+00:00")
            changed=event(100); changed["packageName"]="Silver M"
            with self.assertRaises(ValueError):
                m.collect(1,50,out,fetcher=lambda p,l:[changed],now="2026-09-20T00:00:00+00:00")

    def test_public_policy_fields(self):
        r=m.canonical_source(event(),"x")
        self.assertFalse(r["credentialsUsed"])
        self.assertFalse(r["privateApiUsed"])
        self.assertFalse(r["adminApiUsed"])
        self.assertFalse(r["canRaiseBuySignal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
