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
        package = {"BCH":"Silver M","BTC":"Gold S","ZEC":"Bronze S","KAS":"Titanium S"}.get(coin,"X")
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
        r=m.verify_event(ev("DOGE"))
        self.assertEqual(r["status"],"UNSUPPORTED_COIN")

    def test_build_never_supplies_miss_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/"in.jsonl"; led=Path(d)/"led.jsonl"; rep=Path(d)/"rep.json"
            inp.write_text(json.dumps(ev("DOGE"))+"\n")
            report=m.build(inp,led,rep,20,verifier=lambda e,now=None:m.verify_event(e,now=now),now="2026-09-19T00:00:00+00:00")
            self.assertFalse(report["canSupplyMissDenominator"])
            self.assertFalse(report["canRaiseBuySignal"])
            self.assertEqual(report["packageCoverageIntent"]["Bronze"],"ZEC")
            self.assertEqual(report["packageCoverageIntent"]["Titanium"],"KAS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
