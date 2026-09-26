"""Additional source/timing contracts for the forward-only shadow experiment."""
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_palladium_shadow_trial import snapshot,Clock,T,ROOT,m


class ShadowContractTests(unittest.TestCase):
    def setUp(self):self.p=m.load_protocol(ROOT/'research/palladium-shadow-protocol-v1.json')

    def test_public_cadence_includes_prior_history_and_only_observed_batch_prefix(self):
        c=Clock();old=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as out:
            try:
                os.chdir(tmp);m.PUBLIC_HISTORY.parent.mkdir()
                m.PUBLIC_HISTORY.write_bytes(m.encoded(snapshot(T-timedelta(seconds=120)))+b'\n')
                m.collect(self.p,None,'code',3,out,True,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
                rows=[json.loads(line) for line in Path(out,'public-samples.jsonl').read_text().splitlines()]
                self.assertEqual([r['scryptEconomics']['cadence']['samplesLast24h'] for r in rows],[2,3,4])
                self.assertEqual(rows[-1]['scryptEconomics']['cadence']['maximumGapSeconds'],120)
                latest=json.loads(m.LATEST_ECONOMICS.read_bytes())
                self.assertEqual(latest['cadence']['samplesLast24h'],4)
            finally:os.chdir(old)

    def test_cached_registry_timestamp_not_advanced_for_each_new_sample(self):
        c=Clock()
        def sample(*args):
            s=snapshot(c.now());s['algorithms']['SCRYPT']['unitContract']={'observedAt':c.now().isoformat()}
            return s
        with tempfile.TemporaryDirectory() as out,patch.object(m,'get_json',return_value={}) as get,patch.object(m,'take_sample',side_effect=sample):
            m.collect(self.p,None,'code',3,out,False,clock=c.now,mono=c.mono,sleep=c.sleep)
            rows=[json.loads(line) for line in Path(out,'public-samples.jsonl').read_text().splitlines()]
            self.assertEqual(get.call_count,2)
            self.assertEqual({r['registryMetadataFetchedAt'] for r in rows},{T.isoformat()})
            self.assertEqual({r['algorithms']['SCRYPT']['unitContract']['observedAt'] for r in rows},{T.isoformat()})

    def test_expired_trial_keeps_old_model_hash_but_allows_normal_collector_handoff(self):
        c=Clock();old=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as out:
            try:
                os.chdir(tmp)
                m.collect(self.p,None,'old-code',1,out,True,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
                with patch.object(m,'utcnow',return_value=T+timedelta(hours=1)):
                    with self.assertRaises(ValueError):m.load_live_state(self.p,'new-code')
                with patch.object(m,'utcnow',return_value=T+timedelta(hours=73)):
                    s=m.load_live_state(self.p,'new-code')
                    self.assertEqual(s['modelCodeHash'],'old-code')
                    self.assertEqual(m.mode(s,m.utcnow()),'legacy')
            finally:os.chdir(old)

    def test_boolean_protocol_number_is_not_accepted_as_integer_one(self):
        p=json.loads(json.dumps(self.p));p['schemaVersion']=True
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.json';path.write_text(json.dumps(p))
            with self.assertRaises(ValueError):m.load_protocol(path)

    def test_malformed_chain_object_fails_closed_not_an_unhandled_exception(self):
        s=snapshot();q=next(q for q in s['scryptEconomics']['packages'] if q['package']=='Palladium M')
        q['chains'][0]=None
        self.assertEqual(m.observation(s,'Palladium M')['status'],'UNAVAILABLE')

    def test_public_source_hash_matches_exact_saved_sample(self):
        c=Clock()
        with tempfile.TemporaryDirectory() as out:
            m.collect(self.p,None,'code',2,out,False,sampler=lambda:snapshot(c.now()),clock=c.now,mono=c.mono,sleep=c.sleep)
            public=[json.loads(x) for x in Path(out,'public-samples.jsonl').read_text().splitlines()]
            shadow=[json.loads(x) for x in Path(out,'new-shadow-samples.jsonl').read_text().splitlines()]
            self.assertEqual([m.digest(x) for x in public],[x['publicSnapshotHash'] for x in shadow])


if __name__=='__main__':unittest.main()
