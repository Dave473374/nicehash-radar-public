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


collector = load("nicehash_pool_collector", "scripts/collect_mempool_nicehash_btc_blocks.py")
classifier = load("nicehash_pool_classifier", "scripts/classify_nicehash_btc_blocks.py")


def block(height, h=None, address=None):
    h = h or f"{height:064x}"
    return {
        "height": height,
        "id": h,
        "timestamp": 1_790_000_000,
        "extras": {"reward": 316000000, "totalFees": 3500000},
        "_test_address": address,
    }


class NiceHashBtcAttributionTests(unittest.TestCase):
    def test_normalize_block_page_accepts_list_and_wrapped_blocks(self):
        rows = [block(100)]
        self.assertEqual(collector.normalize_block_page(rows), rows)
        self.assertEqual(collector.normalize_block_page({"blocks": rows}), rows)

    def test_parse_coinbase_outputs(self):
        payload = [{
            "txid": "a" * 64,
            "vin": [{"is_coinbase": True, "scriptsig": "00"}],
            "vout": [
                {"scriptpubkey_address": "bc1qabc", "value": 312500000, "scriptpubkey_type": "v0_p2wpkh"},
                {"value": 10, "scriptpubkey_type": "op_return"},
            ],
        }]
        got = collector.parse_coinbase_outputs(payload)
        self.assertEqual(got["coinbaseTxid"], "a" * 64)
        self.assertEqual(got["coinbaseOutputAddresses"], ["bc1qabc"])
        self.assertEqual(got["coinbaseOutputs"][0]["valueSats"], 312500000)

    def test_collect_pages_preserves_history_and_enriches_only_limit(self):
        pages = {
            None: [block(h) for h in range(110, 100, -1)],
            100: [block(100), block(99)],
        }

        calls = []
        def page_fetcher(cursor):
            return pages[cursor]

        def coinbase_fetcher(block_hash):
            calls.append(block_hash)
            return {
                "coinbaseTxid": "b" * 64,
                "coinbaseOutputs": [{"address": "bc1qtest", "valueSats": 1, "scriptType": "v0_p2wpkh"}],
                "coinbaseOutputAddresses": ["bc1qtest"],
            }

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "pool.jsonl"
            result = collector.collect(
                out,
                max_pages=2,
                coinbase_limit=3,
                page_fetcher=page_fetcher,
                coinbase_fetcher=coinbase_fetcher,
                now="2026-09-21T06:00:00+00:00",
            )
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            # A later run with a smaller current page must preserve older rows.
            result2 = collector.collect(
                out,
                max_pages=1,
                coinbase_limit=2,
                page_fetcher=lambda cursor: [block(h) for h in range(111, 101, -1)],
                coinbase_fetcher=coinbase_fetcher,
                now="2026-09-21T07:00:00+00:00",
            )
            rows2 = [json.loads(line) for line in out.read_text().splitlines()]

        self.assertEqual(result["recordsStored"], 12)
        self.assertEqual(result["coinbaseRowsAttempted"], 3)
        self.assertEqual(len(calls), 5)
        self.assertFalse(rows[0]["canRaiseBuySignal"])
        self.assertFalse(rows[0]["canSupplyMissDenominator"])
        self.assertEqual(rows[0]["sourceRole"], "NICEHASH_POOL_BLOCKS_NOT_PRODUCT_ATTRIBUTION")
        self.assertEqual(result2["recordsStored"], 13)
        self.assertTrue(any(r["blockHeight"] == 99 for r in rows2))

    def test_exact_height_hash_confirms_easymining(self):
        h = "a" * 64
        pool = collector.to_record(block(967915, h), "x")
        pool.update({
            "coinbaseFetchStatus": "OK",
            "coinbaseOutputAddresses": ["bc1qeasypayout"],
            "coinbaseOutputs": [],
        })
        easy = {
            "coin": "BTC",
            "blockHeight": 967915,
            "blockHash": h,
            "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
            "eventId": "easy-1",
            "packageId": "gold-l-id",
            "packageName": "Gold L",
        }
        result = classifier.classify_row(pool, classifier.easy_index([easy]), "now")
        self.assertEqual(result["easyMiningAttribution"], "EASYMINING_CONFIRMED")
        self.assertEqual(result["packageName"], "Gold L")
        self.assertFalse(result["needsExplicitNonEasySourceEvidence"])

    def test_nicehash_without_easy_source_stays_unknown_not_non_easy(self):
        h = "b" * 64
        pool = collector.to_record(block(967930, h), "x")
        result = classifier.classify_row(pool, {}, "now")
        self.assertEqual(result["easyMiningAttribution"], "NICEHASH_UNKNOWN")
        self.assertTrue(result["absenceFromEasyMiningArchiveDoesNotProveNonEasy"])
        self.assertTrue(result["needsExplicitNonEasySourceEvidence"])
        self.assertNotEqual(result["easyMiningAttribution"], "NON_EASY_CONFIRMED")

    def test_height_hash_conflict_fails_closed(self):
        pool = collector.to_record(block(100, "a" * 64), "x")
        easy = {
            "coin": "BTC",
            "blockHeight": 100,
            "blockHash": "b" * 64,
            "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
        }
        result = classifier.classify_row(pool, classifier.easy_index([easy]), "now")
        self.assertEqual(result["easyMiningAttribution"], "CONFLICTING_EVIDENCE")

    def test_address_stats_do_not_turn_address_into_classifier(self):
        rows = [
            {
                "easyMiningAttribution": "EASYMINING_CONFIRMED",
                "coinbaseOutputAddresses": ["bc1qsame"],
            },
            {
                "easyMiningAttribution": "NICEHASH_UNKNOWN",
                "coinbaseOutputAddresses": ["bc1qsame"],
            },
        ]
        stats = classifier._address_stats(rows)
        self.assertEqual(stats[0]["address"], "bc1qsame")
        self.assertEqual(stats[0]["totalBlocks"], 2)
        self.assertEqual(stats[0]["easyMiningConfirmedBlocks"], 1)
        self.assertEqual(stats[0]["niceHashUnknownBlocks"], 1)

    def test_regression_967915_vs_967930(self):
        easy_hash = "00000000000000000000638e9c176baf3671caddb143023e0ca1519d69e8e6a8"
        non_easy_example_hash = "0000000000000000000062b25ebb689e0866e3f4830373311291267ede20972f"
        easy = {
            "coin": "BTC",
            "blockHeight": 967915,
            "blockHash": easy_hash,
            "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
            "eventId": "gold-l-967915",
            "packageId": "cf69f836-5908-47ce-931a-9b6f6c4e2571",
            "packageName": "Gold L",
        }
        idx = classifier.easy_index([easy])
        r1 = classifier.classify_row(collector.to_record(block(967915, easy_hash), "x"), idx, "now")
        r2 = classifier.classify_row(collector.to_record(block(967930, non_easy_example_hash), "x"), idx, "now")
        self.assertEqual(r1["easyMiningAttribution"], "EASYMINING_CONFIRMED")
        # Public-only classifier deliberately stops short of asserting NON-Easy.
        self.assertEqual(r2["easyMiningAttribution"], "NICEHASH_UNKNOWN")

    def test_report_counts_and_address_cross_class_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pool_path = d / "pool.jsonl"
            easy_path = d / "easy.jsonl"
            out = d / "classified.jsonl"
            report_path = d / "report.json"

            h1, h2 = "a" * 64, "b" * 64
            p1 = collector.to_record(block(10, h1), "x")
            p2 = collector.to_record(block(11, h2), "x")
            for p in (p1, p2):
                p["coinbaseFetchStatus"] = "OK"
                p["coinbaseOutputAddresses"] = ["bc1qshared"]
            pool_path.write_text(json.dumps(p1) + "\n" + json.dumps(p2) + "\n")
            easy_path.write_text(json.dumps({
                "coin": "BTC", "blockHeight": 10, "blockHash": h1,
                "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
                "eventId": "e", "packageId": "p", "packageName": "Gold S",
            }) + "\n")

            report = classifier.build(pool_path, easy_path, out, report_path, now="now")

        self.assertEqual(report["niceHashPoolBlockCount"], 2)
        self.assertEqual(report["easyMiningConfirmedCount"], 1)
        self.assertEqual(report["niceHashUnknownCount"], 1)
        self.assertEqual(report["nonEasyConfirmedCount"], 0)
        stat = report["coinbaseAddressStats"][0]
        self.assertEqual(stat["easyMiningConfirmedBlocks"], 1)
        self.assertEqual(stat["niceHashUnknownBlocks"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
