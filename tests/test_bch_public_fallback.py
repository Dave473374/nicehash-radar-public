"""BCH regression fixtures are synthetic; live smoke is separate and explicit."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bch_verifier", ROOT / "scripts/verify_onchain_hits.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HEIGHT = 969090
HASH = "000000000000000000661729e061860fc71d945b3adc7b91ebb211dcdf3abc8e"
NOW = "2026-09-19T15:50:00+00:00"
PRIMARY = m.ZEC_BASE + f"/bitcoin-cash/dashboards/block/{HEIGHT}?limit=1"


def event():
    return {"eventId": "public-bch-fixture", "coin": "BCH", "blockHeight": HEIGHT,
            "blockHash": HASH, "packageName": "Silver M", "packageId": "public-test-only",
            "source": "NICEHASH_PUBLIC_SINGLE_REWARD", "payoutReward": 303137009}


def payload():
    return {"context": {"code": 200, "state": 969201}, "data": {str(HEIGHT): {"block": {
        "id": HEIGHT, "hash": HASH, "time": "2026-09-18 22:29:40",
        "difficulty": 413808617276.17, "coinbase_data_hex": b"abc/NiceHash/xyz".hex(),
        "reward": 312512380, "generation": 312500000, "fee_total": 12380,
        "guessed_miner": "Unknown"}, "transactions": []}}}


class Response:
    status = 200
    def __init__(self, url, data): self.url, self.data = url, data
    def geturl(self): return self.url
    def read(self, n): return self.data if isinstance(self.data, bytes) else json.dumps(self.data).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass


class Opener:
    def __init__(self, mapping): self.mapping, self.calls = mapping, []
    def open(self, req, timeout=30):
        self.calls.append(req.full_url)
        if req.get_method() != "GET": raise AssertionError("Non-GET request")
        if req.full_url not in self.mapping: raise urllib.error.URLError("synthetic offline fixture")
        data = self.mapping[req.full_url]
        if isinstance(data, Exception): raise data
        return Response(req.full_url, data)


def run(p=None, e=None, extra=None):
    op = Opener({PRIMARY: payload() if p is None else p, **(extra or {})})
    return m.verify_event(event() if e is None else e, op, NOW), op


def legacy():
    base = m.MEMPOOL_BASES["BCH"]
    return {base + f"/api/block-height/{HEIGHT}": HASH.encode(),
            base + f"/api/block/{HASH}": {"id": HASH, "height": HEIGHT, "timestamp": 1790000000, "difficulty": 1},
            base + f"/api/block/{HASH}/txs/0": [{"vin": [{"is_coinbase": True, "scriptsig": b"/NiceHash/".hex()}],
                                                "vout": [{"value": 312512380}]}]}


class BchTests(unittest.TestCase):
    def test_01_primary_one_public_get(self):
        r, op = run()
        self.assertEqual(r['status'], 'VERIFIED_ON_CHAIN_NICEHASH_TAG')
        self.assertEqual(op.calls, [PRIMARY])
        self.assertFalse(r['fallbackUsed'])

    def test_02_integer_payout_not_native_coins(self):
        r, _ = run()
        self.assertEqual(r['niceHashPayoutAtomic'], 303137009)
        self.assertEqual(r['niceHashPayoutNative'], 3.03137009)
        self.assertAlmostEqual(r['payoutToCoinbasePercent'], 97, places=6)
        self.assertFalse(r['payoutRatioIsFeeSchedule'])

    def test_03_reward_is_total_not_subsidy(self):
        r, _ = run()
        self.assertEqual(r['coinbaseRewardAtomic'], 312512380)
        self.assertEqual(r['coinbaseRewardNative'], 3.1251238)

    def test_04_missing_reward_not_replaced_by_generation(self):
        p = payload(); p['data'][str(HEIGHT)]['block'].pop('reward')
        r, _ = run(p)
        self.assertIsNone(r['coinbaseRewardNative'])
        self.assertIsNone(r['payoutToCoinbasePercent'])

    def test_05_decimal_or_unknown_payout_not_guessed(self):
        for value in ('3.03137009', 3.03137009, None, True, -1):
            e = event(); e['payoutReward'] = value
            r, _ = run(e=e)
            self.assertIsNone(r['niceHashPayoutNative'])
            self.assertIsNone(r['payoutToCoinbasePercent'])

    def test_06_source_contract_required_for_payout(self):
        e = event(); e.pop('source')
        r, _ = run(e=e)
        self.assertIsNone(r['payoutToCoinbasePercent'])

    def test_07_network_error_uses_bounded_fallback(self):
        r, op = run(urllib.error.URLError('synthetic timeout'), extra=legacy())
        self.assertEqual(r['status'], 'VERIFIED_ON_CHAIN_NICEHASH_TAG')
        self.assertTrue(r['fallbackUsed'])
        self.assertEqual(len(op.calls), 4)
        self.assertAlmostEqual(r['payoutToCoinbasePercent'], 97, places=6)

    def test_08_schema_error_uses_fallback(self):
        r, _ = run({'data': {}}, extra=legacy())
        self.assertTrue(r['fallbackUsed'])
        self.assertTrue(r['status'].startswith('VERIFIED_ON_CHAIN'))

    def test_09_hash_conflict_never_uses_fallback(self):
        p = payload(); p['data'][str(HEIGHT)]['block']['hash'] = 'a'*64
        r, op = run(p, extra=legacy())
        self.assertEqual(r['status'], 'CONFLICT_BLOCK_HASH')
        self.assertEqual(op.calls, [PRIMARY])

    def test_10_height_conflict_never_uses_fallback(self):
        p = payload(); p['data'][str(HEIGHT)]['block']['id'] += 1
        r, op = run(p, extra=legacy())
        self.assertEqual(r['status'], 'CONFLICT_BLOCK_HEIGHT')
        self.assertEqual(op.calls, [PRIMARY])

    def test_11_orphan_not_verified(self):
        p = payload(); p['data'][str(HEIGHT)]['block']['is_orphan'] = True
        r, op = run(p, extra=legacy())
        self.assertEqual(r['status'], 'CONFLICT_ORPHAN_BLOCK')
        self.assertEqual(op.calls, [PRIMARY])

    def test_12_missing_hash_no_false_success(self):
        p = payload(); p['data'][str(HEIGHT)]['block'].pop('hash')
        r, _ = run(p)
        self.assertEqual(r['status'], 'SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH')

    def test_13_guessed_miner_not_coinbase_tag(self):
        p = payload(); b = p['data'][str(HEIGHT)]['block']; b.pop('coinbase_data_hex'); b['guessed_miner'] = 'NiceHash'
        r, _ = run(p)
        self.assertEqual(r['status'], 'VERIFIED_ON_CHAIN_BLOCK_MATCH')
        self.assertIsNone(r['coinbaseTag'])

    def test_14_distinct_tags_preserved(self):
        for tag in (b'/NiceHashSolo/', b'/NiceHashMining/'):
            p = payload(); p['data'][str(HEIGHT)]['block']['coinbase_data_hex'] = tag.hex()
            r, _ = run(p)
            self.assertEqual(r['status'], 'VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG')
            self.assertEqual(r['coinbaseTag'], tag.decode())

    def test_15_invalid_source_identity_before_network(self):
        for key, value in [('blockHash', None), ('blockHash', 'z'*64), ('blockHeight', 969090.1), ('blockHeight', True), ('blockHeight', '../1')]:
            e = event(); e[key] = value
            r, op = run(e=e)
            self.assertEqual(r['status'], 'INVALID_BLOCK_IDENTITY')
            self.assertEqual(op.calls, [])

    def test_16_payout_greater_than_reward_is_visible(self):
        e = event(); e['payoutReward'] = 400000000
        r, _ = run(e=e)
        self.assertTrue(r['payoutExceedsCoinbase'])
        self.assertFalse(r['payoutRatioIsFeeSchedule'])

    def test_17_provider_failure_not_missing_block(self):
        r, op = run(urllib.error.URLError('unreachable'))
        self.assertEqual(r['status'], 'SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH')
        self.assertEqual(len(r['providerAttempts']), 2)
        self.assertEqual(len(op.calls), 2)

    def test_18_context_error_not_success(self):
        p = payload(); p['context']['code'] = 429
        r, _ = run(p)
        self.assertFalse(r['status'].startswith('VERIFIED'))

    def test_19_ambiguous_data_not_success(self):
        p = payload(); p['data']['969091'] = copy.deepcopy(p['data'][str(HEIGHT)])
        r, _ = run(p)
        self.assertFalse(r['status'].startswith('VERIFIED'))

    def test_20_native_reward_float_not_satoshis(self):
        p = payload(); p['data'][str(HEIGHT)]['block']['reward'] = 3.1251238
        r, _ = run(p)
        self.assertIsNone(r['coinbaseRewardNative'])

    def test_21_legacy_noncoinbase_rejected(self):
        extra = legacy(); extra[m.MEMPOOL_BASES['BCH']+f'/api/block/{HASH}/txs/0'][0]['vin'][0]['is_coinbase'] = False
        r, _ = run(urllib.error.URLError('fail'), extra=extra)
        self.assertFalse(r['status'].startswith('VERIFIED'))

    def test_22_legacy_metadata_conflict_rejected(self):
        extra = legacy(); extra[m.MEMPOOL_BASES['BCH']+f'/api/block/{HASH}']['height'] += 1
        r, _ = run(urllib.error.URLError('fail'), extra=extra)
        self.assertEqual(r['status'], 'CONFLICT_BLOCK_METADATA')

    def test_23_no_private_or_decision_effect(self):
        r, _ = run()
        for k in ('privateApiUsed','adminApiUsed','canRaiseBuySignal','canSupplyMissDenominator','personalOrderProven'):
            self.assertIs(r[k], False)

    def test_24_retry_preserves_old_failure(self):
        with tempfile.TemporaryDirectory() as d:
            inp,led,rep = [Path(d)/n for n in ('in','ledger','report')]
            e = event(); inp.write_text(json.dumps(e)+'\n')
            old = {**e,'verifiedAt':'2026-09-19T15:00:00Z','status':'SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH','errorType':'URLError'}
            led.write_text(json.dumps(old)+'\n')
            def verifier(e,now=None): return run(e=e)[0]
            report = m.build(inp,led,rep,1,verifier=verifier,now=NOW)
            saved = json.loads(led.read_text())
            self.assertEqual(saved['eventId'],old['eventId'])
            self.assertEqual(saved['verificationAttemptHistory'][0]['errorType'],'URLError')
            self.assertEqual(report['verifiedByCoin']['BCH'],1)
            # A successful result is not queried again on the next run.
            m.build(inp,led,rep,1,verifier=lambda *a,**k:self.fail('repeated successful block'),now=NOW)

    def test_25_output_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'src'; src.write_text('[]')
            with self.assertRaises(ValueError): m.build(src,src,Path(d)/'r',1)
            self.assertEqual(src.read_text(),'[]')

    def test_26_utc_timestamp_explicit(self):
        r, _ = run()
        self.assertEqual(m.datetime.fromtimestamp(r['onchainTimestamp'],m.timezone.utc).isoformat(), '2026-09-18T22:29:40+00:00')

    def test_27_zero_payout_not_missing(self):
        e=event(); e['payoutReward']=0
        r,_=run(e=e)
        self.assertEqual(r['niceHashPayoutAtomic'],0)
        self.assertEqual(r['payoutToCoinbasePercent'],0)

    def test_28_missing_difficulty_is_not_zero(self):
        p=payload(); p['data'][str(HEIGHT)]['block']['difficulty']=float('nan')
        r,_=run(p)
        self.assertIsNone(r['onchainDifficulty'])

    def test_29_source_inputs_unchanged(self):
        p,e=payload(),event(); before=copy.deepcopy((p,e)); run(p,e)
        self.assertEqual((p,e),before)

    def test_30_redirect_refused(self):
        with self.assertRaises(RuntimeError): m.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com')


if __name__ == '__main__': unittest.main(verbosity=2)
