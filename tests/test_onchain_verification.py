import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("onchain", ROOT / "scripts" / "verify_onchain_blocks.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = "2026-09-19T12:00:00+00:00"
BLOCK_HASH = "00" * 32


def nicehash(coin="BCH", height=969090, package="Silver M", block_hash=BLOCK_HASH, payout="3.03137009"):
    return {
        "coin": coin,
        "blockHeight": height,
        "blockHash": block_hash,
        "packageName": package,
        "packageId": "pkg-1",
        "payoutReward": payout,
        "payoutRewardBtc": "0.009",
        "time": 1780000000000,
        "createdTs": 1780000000000,
        "shared": False,
    }


def fetched(height=969090, block_hash=BLOCK_HASH, reward=312512380, difficulty=410.5, extra=None):
    block = {
        "id": height,
        "hash": block_hash,
        "time": "2026-09-19 10:00:00",
        "difficulty": difficulty,
        "reward": reward,
        "transaction_count": 12,
    }
    if extra:
        block.update(extra)
    return {
        "found": True,
        "provider": "BLOCKCHAIR",
        "chain": "bitcoin-cash",
        "block": block,
        "firstTransactionHash": "aa" * 32,
    }


class OnchainVerificationTests(unittest.TestCase):
    def test_verified_bch_height_hash_and_reward_ratio(self):
        row = m.build_verification(m.project_source(nicehash()), fetched(), NOW)
        self.assertEqual(row["status"], "VERIFIED_ONCHAIN")
        self.assertEqual(row["confidence"], "HEIGHT_AND_HASH")
        self.assertAlmostEqual(row["checks"]["payoutVsBlockRewardPercent"], 97.000004, places=5)
        self.assertFalse(row["canSupplyMissDenominator"])
        self.assertFalse(row["canRaiseBuySignal"])

    def test_hash_mismatch_is_not_verified(self):
        row = m.build_verification(m.project_source(nicehash()), fetched(block_hash="11" * 32), NOW)
        self.assertEqual(row["status"], "HASH_OR_HEIGHT_MISMATCH")
        self.assertEqual(row["confidence"], "MISMATCH")
        self.assertFalse(row["checks"]["hashMatches"])

    def test_height_only_when_nicehash_hash_missing(self):
        source = m.project_source(nicehash(block_hash=None))
        row = m.build_verification(source, fetched(), NOW)
        self.assertEqual(row["status"], "VERIFIED_HEIGHT_ONLY")
        self.assertIsNone(row["checks"]["hashMatches"])

    def test_not_found_is_recorded_without_inventing_hit_detail(self):
        source = m.project_source(nicehash())
        row = m.build_verification(source, {"found": False, "provider": "BLOCKCHAIR"}, NOW)
        self.assertEqual(row["status"], "NOT_FOUND_ONCHAIN")
        self.assertNotIn("checks", row)

    def test_nicehash_tag_when_provider_exposes_it(self):
        row = m.build_verification(
            m.project_source(nicehash()),
            fetched(extra={"coinbase_data": "/NiceHash/"}),
            NOW,
        )
        self.assertEqual(row["onchain"]["nicehashTagStatus"], "OBSERVED")

    def test_missing_reward_keeps_ratio_unknown(self):
        row = m.build_verification(m.project_source(nicehash()), fetched(reward=None), NOW)
        self.assertIsNone(row["checks"]["payoutVsBlockRewardPercent"])

    def test_unsupported_coin_skipped_by_run(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.jsonl"
            out = Path(d) / "out.jsonl"
            src.write_text(json.dumps(nicehash(coin="KAS")) + "\n")
            records, report = m.run(src, out, lambda chain, height: fetched(), 10, NOW)
        self.assertEqual(records, [])
        self.assertEqual(report["skipped"]["unsupportedOrInvalid"], 1)

    def test_duplicate_sources_conflict_excluded(self):
        a = nicehash()
        b = nicehash(payout="2.0")
        rows, conflicts = m.unique_sources([a, b])
        self.assertEqual(rows, {})
        self.assertEqual(len(conflicts), 1)

    def test_existing_verification_not_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.jsonl"
            old = Path(d) / "old.jsonl"
            source = nicehash()
            src.write_text(json.dumps(source) + "\n")
            existing = m.build_verification(m.project_source(source), fetched(), NOW)
            old.write_text(m.canonical(existing) + "\n")
            def fail_fetch(chain, height):
                raise AssertionError("should not fetch existing verification")
            records, report = m.run(src, old, fail_fetch, 10, NOW)
        self.assertEqual(len(records), 1)
        self.assertEqual(report["verifiedRecordCount"], 1)

    def test_limit_creates_pending_count(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.jsonl"
            old = Path(d) / "old.jsonl"
            rows = [nicehash(height=1, package="A"), nicehash(height=2, package="B")]
            src.write_text("".join(json.dumps(row) + "\n" for row in rows))
            records, report = m.run(src, old, lambda chain, height: fetched(height=height), 1, NOW)
        self.assertEqual(len(records), 1)
        self.assertEqual(report["pendingCandidateCount"], 1)

    def test_explorer_urls_include_bch_human_link(self):
        urls = m.explorer_urls("BCH", 969090, BLOCK_HASH)
        self.assertIn("blockchainCom", urls)
        self.assertIn("blockchair", urls)

    def test_merge_conflict_preserves_existing(self):
        row1 = m.build_verification(m.project_source(nicehash()), fetched(), NOW)
        row2 = m.build_verification(m.project_source(nicehash()), fetched(extra={"coinbase_data": "/NiceHash/"}), "2026-09-19T12:01:00+00:00")
        merged = m.merge([row1], [row2])
        self.assertEqual(merged[0]["status"], "VERIFIED_ONCHAIN")
        self.assertTrue(any(x["status"] == "CONFLICT_WITH_EXISTING_VERIFICATION" for x in merged))


if __name__ == "__main__":
    unittest.main(verbosity=2)
