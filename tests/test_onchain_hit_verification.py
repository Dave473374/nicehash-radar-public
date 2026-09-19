import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("onchain", ROOT/"scripts"/"verify_onchain_hits.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def ev(coin="BCH",height=969090,taghash=None):
    return {"eventId":f"{coin}-{height}","coin":coin,"blockHeight":height,"blockHash":taghash,"packageName":"Silver M","packageId":"p","payoutReward":"3.03137009","time":1_700_000_000_000}


class FakeResponse:
    def __init__(self,url,data,status=200): self._url=url; self._data=data; self.status=status
    def geturl(self): return self._url
    def read(self,n=-1): return self._data
    def __enter__(self): return self
    def __exit__(self,*args): return False


class FakeOpener:
    def __init__(self,mapping): self.mapping=mapping
    def open(self,request,timeout=30):
        url=request.full_url
        if url not in self.mapping: raise RuntimeError("missing fixture")
        val=self.mapping[url]
        data=(json.dumps(val).encode() if not isinstance(val,(str,bytes)) else val.encode() if isinstance(val,str) else val)
        return FakeResponse(url,data)


def fixture(tag=b"/NiceHash/",height=969090):
    h=f"{height:064x}"; base=m.EXPLORERS["BCH"]
    scriptsig=(b"abc"+tag+b"xyz").hex()
    return h, FakeOpener({
        base+f"/api/block-height/{height}":h,
        base+f"/api/block/{h}":{"id":h,"height":height,"timestamp":1_700_000_000,"difficulty":413808617276.17},
        base+f"/api/block/{h}/txs/0":[{"vin":[{"is_coinbase":True,"scriptsig":scriptsig}],"vout":[{"value":312512380}]}],
    })


class OnchainTests(unittest.TestCase):
    def test_tag_classes(self):
        for raw,label in [(b"/NiceHash/","NICEHASH_TAG"),(b"/NiceHashMining/","NICEHASH_MINING_TAG"),(b"/NiceHashSolo/","NICEHASH_SOLO_TAG")]:
            self.assertEqual(m.classify_tag(raw.hex())[0],label)
        self.assertEqual(m.classify_tag(b"other".hex())[0],"UNKNOWN_TAG")

    def test_bch_verified_and_reward_ratio(self):
        h,op=fixture()
        r=m.verify_event(ev(taghash=h),opener=op,now="2026-09-19T00:00:00+00:00")
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_NICEHASH_TAG")
        self.assertEqual(r["coinbaseTag"],"/NiceHash/")
        self.assertAlmostEqual(r["coinbaseRewardNative"],3.1251238,places=7)
        self.assertGreater(r["payoutToCoinbasePercent"],96)
        self.assertLess(r["payoutToCoinbasePercent"],98)

    def test_internal_tag_is_not_called_easymining_verified(self):
        h,op=fixture(tag=b"/NiceHashMining/")
        r=m.verify_event(ev(taghash=h),opener=op)
        self.assertEqual(r["status"],"VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG")

    def test_hash_conflict_fails_closed(self):
        h,op=fixture()
        r=m.verify_event(ev(taghash="f"*64),opener=op)
        self.assertEqual(r["status"],"CONFLICT_BLOCK_HASH")

    def test_unsupported_coin_no_network(self):
        r=m.verify_event(ev(coin="DOGE"))
        self.assertEqual(r["status"],"UNSUPPORTED_COIN")

    def test_network_error_is_recorded_not_crash(self):
        r=m.verify_event(ev(),opener=FakeOpener({}))
        self.assertEqual(r["status"],"SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH")

    def test_build_never_creates_miss_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/"in.jsonl"; led=Path(d)/"led.jsonl"; rep=Path(d)/"rep.json"
            inp.write_text(json.dumps(ev(coin="DOGE"))+"\n")
            report=m.build(inp,led,rep,20,verifier=lambda e,now=None:m.verify_event(e,now=now),now="2026-09-19T00:00:00+00:00")
            self.assertFalse(report["canSupplyMissDenominator"])
            self.assertFalse(report["canRaiseBuySignal"])
            self.assertTrue(report["successEventsOnly"])

    def test_source_time_milliseconds(self):
        dt=m.parse_source_time(1_700_000_000_000)
        self.assertEqual(int(dt.timestamp()),1_700_000_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
