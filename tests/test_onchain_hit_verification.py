import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("onchain", ROOT / "scripts" / "verify_onchain_hits.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def ev(coin="BCH", height=969090, block_hash=None, package=None):
    if block_hash is None:
        block_hash = f"{height:064x}"
    if package is None:
        package = {"BCH":"Silver M","BTC":"Gold S","ZEC":"Bronze S","KAS":"Titanium S","DOGE":"Palladium M","LTC":"Palladium M"}.get(coin,"X")
    return {
        "eventId": f"{coin}-{height}",
        "coin": coin,
        "blockHeight": height,
        "blockHash": block_hash,
        "packageName": package,
        "packageId": "p",
        "payoutReward": "3.03137009",
        "time": 1_700_000_000_000,
    }


class FakeResponse:
    def __init__(self, url, data, status=200):
        self._url=url; self._data=data; self.status=status
    def geturl(self): return self._url
    def read(self, n=-1): return self._data
    def __enter__(self): return self
    def __exit__(self,*args): return False


class FakeOpener:
    def __init__(self, mapping): self.mapping=mapping
    def open(self, request, timeout=30):
        url=request.full_url
        if url not in self.mapping: raise RuntimeError("missing fixture")
        val=self.mapping[url]
        if isinstance(val, Exception):
            raise val
        data = val if isinstance(val, bytes) else val.encode() if isinstance(val, str) else json.dumps(val).encode()
        return FakeResponse(url,data)


def mempool_fixture(coin="BCH", tag=b"/NiceHash/", height=969090):
    h=f"{height:064x}"; base=m.MEMPOOL_BASES[coin]
    scriptsig=(b"abc"+tag+b"xyz").hex()
    op=FakeOpener({
        base+f"/api/block-height/{height}": h,
        base+f"/api/block/{h}": {"id":h,"height":height,"timestamp":1_700_000_000,"difficulty":413808617276.17},
        base+f"/api/block/{h}/txs/0": [{"vin":[{"is_coinbase":True,"scriptsig":scriptsig}],"vout":[{"value":312512380}]}],
    })
    return h,op


class OnchainTests(unittest.TestCase):
    def test_nicehash_tag(self):
        h,op=mempool_fixture()
        r=m.verify_event(ev(block_hash=h), opener=op, now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_NICEHASH_TAG")
        self.assertAlmostEqual(r["coinbaseRewardNative"],3.1251238,places=7)

    def test_other_tag_not_equal_easymining_tag(self):
        h,op=mempool_fixture(tag=b"/NiceHashMining/")
        r=m.verify_event(ev(block_hash=h), opener=op)
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG")

    def test_bch_blockchair_fallback_verifies_969090_and_units(self):
        height=969090
        h="000000000000000000661729e061860fc71d945b3adc7b91ebb211dcdf3abc8e"
        primary=m.MEMPOOL_BASES["BCH"]+f"/api/block-height/{height}"
        fallback=m.BLOCKCHAIR_BASE+f"/bitcoin-cash/dashboards/block/{height}"
        op=FakeOpener({
            primary: RuntimeError("primary unavailable"),
            fallback: {
                "data": {str(height): {"block": {
                    "id": height,
                    "hash": h,
                    "time": "2026-09-18 22:30:00",
                    "difficulty": 413808617276.17,
                    "reward": 312512380,
                    "guessed_miner": "Unknown",
                }}},
                "context": {"code": 200},
            },
        })
        event=ev("BCH",height,h)
        event["payoutReward"]=303137009
        r=m.verify_event(event, opener=op, now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_BLOCK_MATCH")
        self.assertEqual(r["verificationStrength"],"BLOCK_HEIGHT_HASH_INDEPENDENT_EXPLORER_FALLBACK")
        self.assertEqual(r["primaryErrorType"],"RuntimeError")
        self.assertAlmostEqual(r["niceHashPayoutRewardNative"],3.03137009,places=8)
        self.assertAlmostEqual(r["coinbaseRewardNative"],3.1251238,places=7)
        self.assertAlmostEqual(r["payoutToCoinbasePercent"],97.000000,places=4)
        self.assertEqual(r["coinbaseTagClass"],"NOT_CHECKED_BCH_BLOCKCHAIR_FALLBACK")

    def test_bch_ninja_second_fallback_verifies_969090_hash_and_label(self):
        height=969090
        h="000000000000000000661729e061860fc71d945b3adc7b91ebb211dcdf3abc8e"
        primary=m.MEMPOOL_BASES["BCH"]+f"/api/block-height/{height}"
        blockchair=m.BLOCKCHAIR_BASE+f"/bitcoin-cash/dashboards/block/{height}"
        ninja=m.BCH_NINJA_BASE+f"/api/blocks-by-height/{height}"
        op=FakeOpener({
            primary: RuntimeError("primary unavailable"),
            blockchair: RuntimeError("blockchair unavailable"),
            ninja: [{"height":height,"hash":h,"time":1700000000,"poolInfo":{"poolName":"NiceHash"}}],
        })
        event=ev("BCH",height,h)
        event["payoutReward"]=303137009
        r=m.verify_event(event, opener=op, now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO")
        self.assertEqual(r["onchainBlockHash"],h)
        self.assertAlmostEqual(r["niceHashPayoutRewardNative"],3.03137009,places=8)
        self.assertEqual(r["explorerMinerLabel"],"NiceHash")
        self.assertEqual(r["coinbaseTagClass"],"BCH_NINJA_NICEHASH_MINER_LABEL")

    def test_bch_ninja_hash_conflict_fails_closed(self):
        height=969090
        expected="a"*64
        actual="b"*64
        primary=m.MEMPOOL_BASES["BCH"]+f"/api/block-height/{height}"
        blockchair=m.BLOCKCHAIR_BASE+f"/bitcoin-cash/dashboards/block/{height}"
        ninja=m.BCH_NINJA_BASE+f"/api/blocks-by-height/{height}"
        op=FakeOpener({
            primary: RuntimeError("primary unavailable"),
            blockchair: RuntimeError("blockchair unavailable"),
            ninja: {"blocks":[{"height":height,"hash":actual,"poolInfo":{"poolName":"NiceHash"}}]},
        })
        r=m.verify_event(ev("BCH",height,expected), opener=op)
        self.assertEqual(r["status"],"CONFLICT_BLOCK_HASH")
        self.assertEqual(r["explorer"],m.BCH_NINJA_BASE)

    def test_bch_ninja_ambiguous_hash_response_fails_closed(self):
        height=969090
        primary=m.MEMPOOL_BASES["BCH"]+f"/api/block-height/{height}"
        blockchair=m.BLOCKCHAIR_BASE+f"/bitcoin-cash/dashboards/block/{height}"
        ninja=m.BCH_NINJA_BASE+f"/api/blocks-by-height/{height}"
        op=FakeOpener({
            primary: RuntimeError("primary unavailable"),
            blockchair: RuntimeError("blockchair unavailable"),
            ninja: [
                {"height":height,"hash":"a"*64},
                {"height":height,"hash":"b"*64},
            ],
        })
        r=m.verify_event(ev("BCH",height,"a"*64), opener=op)
        self.assertEqual(r["status"],"SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH")

    def test_bch_primary_hash_conflict_is_not_hidden_by_fallback(self):
        height=969090
        actual=f"{height:064x}"
        wrong="f"*64
        base=m.MEMPOOL_BASES["BCH"]
        op=FakeOpener({
            base+f"/api/block-height/{height}": actual,
        })
        r=m.verify_event(ev("BCH",height,wrong), opener=op)
        self.assertEqual(r["status"],"CONFLICT_BLOCK_HASH")
        self.assertEqual(r["explorer"],base)

    def test_palladium_doge_blockchair_chain_match_and_units(self):
        height=6380758
        h="1d407ec5d1f0d35df2089784cfa410bd58db85a6ff62ae1fede6b60c6a0e2150"
        url=m.BLOCKCHAIR_BASE+f"/dogecoin/dashboards/block/{height}"
        op=FakeOpener({url:{
            "data": {str(height): {"block": {
                "id": height, "hash": h, "time":"2026-09-19 12:00:00",
                "difficulty":12345678.0, "reward":1000000000000,
                "guessed_miner":"NiceHash"
            }}},
            "context":{"code":200}
        }})
        event=ev("DOGE",height,h)
        event["payoutReward"]=970034412690
        r=m.verify_event(event, opener=op, now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO")
        self.assertEqual(r["mergedMiningFamily"],"Palladium")
        self.assertEqual(r["mergedMiningChain"],"DOGE")
        self.assertEqual(r["mergedMiningChainRole"],"AUXPOW_CHILD_CHAIN")
        self.assertEqual(r["mergedMiningEvidenceScope"],"THIS_CHAIN_EVENT_ONLY")
        self.assertFalse(r["pairedChainEvidenceClaimed"])
        self.assertAlmostEqual(r["niceHashPayoutRewardNative"],9700.3441269,places=7)
        self.assertAlmostEqual(r["coinbaseRewardNative"],10000.0,places=7)
        self.assertAlmostEqual(r["payoutToCoinbasePercent"],97.003441,places=6)

    def test_palladium_ltc_blockchair_chain_match(self):
        height=3000000
        h="a"*64
        url=m.BLOCKCHAIR_BASE+f"/litecoin/dashboards/block/{height}"
        op=FakeOpener({url:{
            "data": {str(height): {"block": {
                "id": height, "hash": h, "time":"2026-09-19 12:00:00",
                "difficulty":999.0, "reward":625000000,
                "guessed_miner":"Unknown"
            }}},
            "context":{"code":200}
        }})
        event=ev("LTC",height,h)
        event["payoutReward"]=606250000
        r=m.verify_event(event, opener=op)
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_BLOCK_MATCH")
        self.assertEqual(r["mergedMiningChainRole"],"PARENT_SCRYPT_CHAIN")
        self.assertFalse(r["pairedChainEvidenceClaimed"])
        self.assertAlmostEqual(r["niceHashPayoutRewardNative"],6.0625,places=8)
        self.assertAlmostEqual(r["coinbaseRewardNative"],6.25,places=8)
        self.assertAlmostEqual(r["payoutToCoinbasePercent"],97.0,places=6)

    def test_palladium_hash_conflict_fails_closed(self):
        height=6380758
        actual="b"*64
        expected="a"*64
        url=m.BLOCKCHAIR_BASE+f"/dogecoin/dashboards/block/{height}"
        op=FakeOpener({url:{
            "data": {str(height): {"block": {
                "id": height, "hash": actual, "reward":1000000000000
            }}},
            "context":{"code":200}
        }})
        r=m.verify_event(ev("DOGE",height,expected), opener=op)
        self.assertEqual(r["status"],"CONFLICT_BLOCK_HASH")
        self.assertEqual(r["onchainBlockHash"],actual)
        self.assertFalse(r["pairedChainEvidenceClaimed"])

    def test_zec_block_match(self):
        height=3243734; h=f"{height:064x}"
        url=m.ZEC_BASE+f"/zcash/dashboards/block/{height}"
        op=FakeOpener({url:{
            "data": {str(height): {"block": {
                "id": height, "hash": h, "time":"2026-02-17 17:41:00",
                "difficulty":123.4, "reward":156250000, "guessed_miner":"Example"
            }}},
            "context":{"code":200}
        }})
        r=m.verify_event(ev("ZEC",height,h), opener=op)
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_BLOCK_MATCH")
        self.assertEqual(r["verificationStrength"],"BLOCK_HEIGHT_HASH_INDEPENDENT_EXPLORER")

    def test_kas_block_match_and_nicehash_miner_info(self):
        h="a"*64
        path=f"/blocks/{h}?includeTransactions=true&includeColor=false"
        op=FakeOpener({m.KAS_BASE+path:{
            "header":{"timestamp":"1700000000000","daaScore":"1","blueScore":"2"},
            "verboseData":{"hash":h,"difficulty":55.5,"blueScore":"2"},
            "transactions":[],
            "extra":{"minerInfo":"NiceHash","minerAddress":"kaspa:q"}
        }})
        r=m.verify_event(ev("KAS",123,h), opener=op)
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO")
        self.assertEqual(r["onchainDifficulty"],55.5)

    def test_hash_conflict_fails_closed(self):
        h,op=mempool_fixture()
        r=m.verify_event(ev(block_hash="f"*64), opener=op)
        self.assertEqual(r["status"],"CONFLICT_BLOCK_HASH")

    def test_unsupported_coin(self):
        r=m.verify_event(ev("RVN"))
        self.assertEqual(r["status"],"UNSUPPORTED_COIN")

    def test_newly_supported_coin_retries_old_unsupported_ledger_row(self):
        calls = []
        def fake_verify(e, now=None):
            calls.append(e["coin"])
            return {**e, "eventId": e["eventId"], "status": "VERIFIED_ON_CHAIN_BLOCK_MATCH", "coin": e["coin"]}
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/"in.jsonl"; led=Path(d)/"led.jsonl"; rep=Path(d)/"rep.json"
            event=ev("DOGE",6380758,"1d407ec5d1f0d35df2089784cfa410bd58db85a6ff62ae1fede6b60c6a0e2150")
            inp.write_text(json.dumps(event)+"\n")
            led.write_text(json.dumps({
                "eventId":event["eventId"],"coin":"DOGE","blockHeight":event["blockHeight"],
                "status":"UNSUPPORTED_COIN"
            })+"\n")
            report=m.build(inp,led,rep,1,verifier=fake_verify,now="2026-09-19T00:00:00+00:00")
        self.assertEqual(calls,["DOGE"])
        self.assertEqual(report["verifiedByCoin"].get("DOGE"),1)

    def test_round_robin_prevents_kas_starvation(self):
        calls = []
        def fake_verify(e, now=None):
            calls.append(e["coin"])
            return {**e, "eventId": e["eventId"], "status": "VERIFIED_ON_CHAIN_BLOCK_MATCH", "coin": e["coin"]}
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/"in.jsonl"; led=Path(d)/"led.jsonl"; rep=Path(d)/"rep.json"
            rows = [ev("KAS", i, f"{i:064x}") for i in range(20, 10, -1)] + [
                ev("ZEC", 9, "9"*64), ev("BCH", 8, "8"*64), ev("BTC", 7, "7"*64)
            ]
            inp.write_text("".join(json.dumps(x)+"\n" for x in rows))
            m.build(inp,led,rep,4,verifier=fake_verify,now="2026-09-19T00:00:00+00:00")
        self.assertEqual(set(calls), {"BTC","BCH","ZEC","KAS"})

    def test_build_never_supplies_miss_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/"in.jsonl"; led=Path(d)/"led.jsonl"; rep=Path(d)/"rep.json"
            inp.write_text(json.dumps(ev("RVN"))+"\n")
            report=m.build(inp,led,rep,20,verifier=lambda e,now=None:m.verify_event(e,now=now),now="2026-09-19T00:00:00+00:00")
            self.assertFalse(report["canSupplyMissDenominator"])
            self.assertFalse(report["canRaiseBuySignal"])
            self.assertEqual(report["packageCoverageIntent"]["Bronze"],"ZEC")
            self.assertEqual(report["packageCoverageIntent"]["Titanium"],"KAS")
            self.assertIn("LTC + DOGE", report["packageCoverageIntent"]["Palladium"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
