"""Offline lossless-storage and compatibility tests; no network/account operations."""
from __future__ import annotations
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import radar_snapshot_archive as a


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'radar-snapshots.jsonl'
        self.raw = b'\r\n {"z":2,"collected_at":"2026-09-22T17:40:34Z"}\r\n' + '{"name":"ž Škrabel"}\n'.encode() + b'{"repeat":true}\n{"repeat":true}\n'
        self.path.write_bytes(self.raw)

    def read_bytes(self):
        with a.open_history(self.path, 'rb') as h:
            return h.read()

    def manifest(self):
        return json.loads(a.manifest_path(self.path).read_bytes())

    def write_manifest(self, m):
        a.manifest_path(self.path).write_text(json.dumps(m))

    def test_lossless_migration_includes_whitespace_unicode_duplicates(self):
        m = a.migrate_history(self.path, chunk_bytes=40)
        self.assertFalse(self.path.exists())
        self.assertTrue(a.history_exists(self.path))
        self.assertEqual(self.read_bytes(), self.raw)
        self.assertEqual(m['logicalSha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(m['records'], 4)
        self.assertGreater(len(m['chunks']),1)
        self.assertEqual(a.history_digest(self.path), m['logicalSha256'])
        self.assertEqual(a.verify_history(self.path)['storage'], 'VERIFIED_SHARDS')

    def test_second_migration_is_noop(self):
        a.migrate_history(self.path)
        before = a.manifest_path(self.path).read_bytes()
        a.migrate_history(self.path)
        self.assertEqual(before, a.manifest_path(self.path).read_bytes())

    def test_append_preserves_old_bytes_and_immutable_chunks(self):
        a.migrate_history(self.path, chunk_bytes=40)
        m = self.manifest()
        before = {c['file']: (a.archive_dir(self.path)/c['file']).read_bytes() for c in m['chunks']}
        new = [{'collected_at':'2026-09-23T01:02:03Z','feed':{'ok':True}}]
        a.append_history(self.path,new,expected_sha256=m['logicalSha256'])
        appended = (json.dumps(new[0],separators=(',',':'))+'\n').encode()
        self.assertEqual(self.read_bytes(),self.raw+appended)
        for name,raw in before.items():self.assertEqual((a.archive_dir(self.path)/name).read_bytes(),raw)
        self.assertEqual(a.verify_history(self.path)['records'],5)
        self.assertEqual(self.manifest()['migrationAnchor'],m['migrationAnchor'])

    def test_duplicate_rows_preserved_not_new_dedup_policy(self):
        a.migrate_history(self.path)
        a.append_history(self.path,[{'repeat':True},{'repeat':True}])
        self.assertEqual(a.verify_history(self.path)['records'],6)

    def test_empty_archive_and_append(self):
        self.path.write_bytes(b'')
        a.migrate_history(self.path)
        self.assertEqual(self.read_bytes(),b'')
        a.append_history(self.path,[{'ok':True}])
        self.assertEqual(self.read_bytes(),b'{"ok":true}\n')

    def test_legacy_small_append_remains_backward_compatible(self):
        a.append_history(self.path,[{'new':1}])
        self.assertTrue(self.path.exists())
        self.assertFalse(a.manifest_path(self.path).exists())
        self.assertEqual(self.path.read_bytes(),self.raw+b'{"new":1}\n')

    def test_new_archive_with_no_legacy(self):
        self.path.unlink()
        a.append_history(self.path,[{'new':1}])
        self.assertEqual(self.read_bytes(),b'{"new":1}\n')

    def test_missing_newline_append_keeps_exact_prefix(self):
        self.raw=b'{"old":1}'
        self.path.write_bytes(self.raw)
        a.migrate_history(self.path)
        self.assertEqual(self.read_bytes(),self.raw)
        a.append_history(self.path,[{'new':1}])
        self.assertEqual(self.read_bytes(),self.raw+b'\n{"new":1}\n')
        self.assertEqual(a.verify_history(self.path)['records'],2)

    def test_corrupt_chunk_fails_before_consumer_receives_bytes(self):
        a.migrate_history(self.path,chunk_bytes=30)
        m=self.manifest()
        cp=a.archive_dir(self.path)/m['chunks'][-1]['file']
        raw=cp.read_bytes();cp.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)

    def test_missing_chunk_does_not_mean_missing_history(self):
        a.migrate_history(self.path)
        (a.archive_dir(self.path)/self.manifest()['chunks'][0]['file']).unlink()
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)

    def test_bad_manifest_does_not_fall_back_to_legacy(self):
        a.migrate_history(self.path)
        self.path.write_bytes(self.raw)
        a.manifest_path(self.path).write_text('broken')
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)
        with self.assertRaises(a.ArchiveError):a.history_exists(self.path)

    def test_manifest_count_size_hash_and_path_contracts(self):
        a.migrate_history(self.path)
        original=self.manifest()
        bads=[]
        for key,val in (('records',0),('logicalBytes',0),('logicalSha256','0'*64),('endsWithNewline',False),('schemaVersion',9)):
            x=copy.deepcopy(original);x[key]=val;bads.append(x)
        for key,val in (('file','../outside.gz'),('rawBytes',a.CHUNK_BYTES+1),('compressedBytes',a.MAX_COMPRESSED_BYTES+1),('rawSha256','0'*64),('records',99)):
            x=copy.deepcopy(original);x['chunks'][0][key]=val;bads.append(x)
        for x in bads:
            self.write_manifest(x)
            with self.assertRaises(a.ArchiveError):a.open_history(self.path)
        self.write_manifest(original)

    def test_legacy_conflict_not_silently_ignored(self):
        a.migrate_history(self.path)
        self.path.write_bytes(self.raw+b'{"other":1}\n')
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)
        with self.assertRaises(a.ArchiveError):a.migrate_history(self.path)

    def test_crash_before_publish_preserves_original_and_is_retryable(self):
        with patch.object(a,'_publish',side_effect=RuntimeError('simulated')):
            with self.assertRaises(RuntimeError):a.migrate_history(self.path,chunk_bytes=40)
        self.assertEqual(self.path.read_bytes(),self.raw)
        self.assertFalse(a.manifest_path(self.path).exists())
        a.migrate_history(self.path,chunk_bytes=40)
        self.assertEqual(self.read_bytes(),self.raw)

    def test_crash_after_publish_before_unlink_recoverable(self):
        publish=a._publish
        def stop(path,m):
            publish(path,m)
            raise RuntimeError('simulated')
        with patch.object(a,'_publish',side_effect=stop):
            with self.assertRaises(RuntimeError):a.migrate_history(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.read_bytes(),self.raw)
        a.migrate_history(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.read_bytes(),self.raw)

    def test_append_crash_orphans_not_included_until_retry(self):
        a.migrate_history(self.path)
        before=a.manifest_path(self.path).read_bytes()
        with patch.object(a,'_publish',side_effect=RuntimeError('simulated')):
            with self.assertRaises(RuntimeError):a.append_history(self.path,[{'new':1}])
        self.assertEqual(a.manifest_path(self.path).read_bytes(),before)
        self.assertEqual(self.read_bytes(),self.raw)
        a.append_history(self.path,[{'new':1}])
        self.assertEqual(self.read_bytes(),self.raw+b'{"new":1}\n')

    def test_expected_base_rejects_stale_writer(self):
        a.migrate_history(self.path)
        before=a.manifest_path(self.path).read_bytes()
        with self.assertRaises(a.ArchiveError):a.append_history(self.path,[{'new':1}],expected_sha256='0'*64)
        self.assertEqual(a.manifest_path(self.path).read_bytes(),before)

    def test_old_manifest_reader_valid_while_new_manifest_published(self):
        m=a.migrate_history(self.path)
        a.append_history(self.path,[{'new':1}])
        with a._verified_binary(self.path,m) as h:self.assertEqual(h.read(),self.raw)

    def test_concurrent_local_writer_refused(self):
        a.migrate_history(self.path)
        with a._writer_lock(self.path):
            with self.assertRaises(a.ArchiveError):a.append_history(self.path,[{'new':1}])

    def test_invalid_source_never_removed(self):
        for raw in (b'{"x":NaN}\n',b'[1]\n',b'{\xff}\n',b'{invalid}\n'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(a.ArchiveError):a.migrate_history(self.path)
                self.assertEqual(self.path.read_bytes(),raw)

    def test_invalid_appended_rows_never_modify_manifest(self):
        a.migrate_history(self.path)
        before=a.manifest_path(self.path).read_bytes()
        for rows in ([None],[[]],[{'x':float('nan')}],[{'x':float('inf')}]):
            with self.assertRaises((a.ArchiveError,ValueError)):a.append_history(self.path,rows)
            self.assertEqual(a.manifest_path(self.path).read_bytes(),before)

    def test_missing_manifest_with_orphans_is_not_empty(self):
        a.migrate_history(self.path)
        a.manifest_path(self.path).unlink()
        with self.assertRaises(a.ArchiveError):a.history_exists(self.path)
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)

    def test_symlinks_refused(self):
        a.migrate_history(self.path)
        cp=a.archive_dir(self.path)/self.manifest()['chunks'][0]['file']
        outside=self.root/'outside';outside.write_bytes(cp.read_bytes());cp.unlink();cp.symlink_to(outside)
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)

    def test_gzip_bomb_bounded(self):
        a.migrate_history(self.path)
        m=self.manifest();c=m['chunks'][0]
        cp=a.archive_dir(self.path)/c['file']
        data=gzip.compress(b' '*1_000_000,mtime=0);cp.write_bytes(data)
        c['compressedBytes']=len(data);c['compressedSha256']=hashlib.sha256(data).hexdigest()
        self.write_manifest(m)
        with self.assertRaises(a.ArchiveError):a.open_history(self.path)

    def test_chunk_target_bound_and_no_oversize_line_loss(self):
        with self.assertRaises(ValueError):a.migrate_history(self.path,chunk_bytes=a.CHUNK_BYTES+1)
        self.path.write_bytes(b'{"x":"'+b'a'*a.CHUNK_BYTES+b'"}\n')
        before=self.path.stat().st_size
        with self.assertRaises(a.ArchiveError):a.migrate_history(self.path)
        self.assertEqual(self.path.stat().st_size,before)

    def test_readonly_api_and_plain_generic_file(self):
        with self.assertRaises(ValueError):a.open_history(self.path,'a')
        other=self.root/'other.json';other.write_text('{"x":1}')
        self.assertEqual(a.read_history_text(other),'{"x":1}')
        missing=self.root/'missing.jsonl'
        self.assertFalse(a.history_exists(missing))
        with self.assertRaises(FileNotFoundError):a.open_history(missing)

    def test_staging_only_referenced_chunks_and_deleted_legacy(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.name','test'],check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.email','test@localhost'],check=True)
        subprocess.run(['git','-C',str(self.root),'add','radar-snapshots.jsonl'],check=True)
        subprocess.run(['git','-C',str(self.root),'commit','-qm','legacy'],check=True)
        a.migrate_history(self.path)
        orphan=a.archive_dir(self.path)/'chunks'/'orphan.gz';orphan.write_bytes(b'orphan')
        previous=Path.cwd()
        try:
            os.chdir(self.root)
            a.stage_history(Path('radar-snapshots.jsonl'))
            staged=subprocess.check_output(['git','diff','--cached','--name-status']).decode()
            self.assertIn('D\tradar-snapshots.jsonl',staged)
            self.assertIn('manifest.json',staged)
            self.assertNotIn('orphan.gz',staged)
            self.assertNotIn('.writer.lock',staged)
            with patch.object(a,'STAGED_LIMIT',1):
                with self.assertRaises(a.ArchiveError):a.check_staged_files()
        finally:os.chdir(previous)


class ConsumerCompatibilityTests(unittest.TestCase):
    def test_every_direct_snapshot_consumer_has_archive_adapter(self):
        exempt={'radar_snapshot_archive.py'}
        for p in (ROOT/'scripts').glob('*.py'):
            text=p.read_text()
            if 'radar-snapshots.jsonl' not in text or p.name in exempt:continue
            if p.name=='diagnose_market_inputs.py':
                self.assertIn('from build_market_edge_research import',text)
                self.assertIn('json_lines',text)
                continue
            self.assertIn('from radar_snapshot_archive import',text,p.name)

    def test_existing_public_readers_and_logical_digest_equal(self):
        import build_market_edge_research as research
        import build_alert_latency_report as latency
        import build_palladium_m_signal_exposure as exposure
        from tests.test_reward_context_integrity import snapshot, AT
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'radar-snapshots.jsonl'
            rows=[snapshot(-2,-1)]
            rows[0]['feed']['packages'][0]['name']='Palladium M'
            rows[0]['feed']['packages'][0]['final_signal']='WAIT'
            raw=''.join(json.dumps(r)+'\n' for r in rows).encode();p.write_bytes(raw)
            from collections import Counter
            before=(list(research.json_lines(p,Counter())),research.digest(p),
                    latency.load_snapshots(p,AT),exposure.load_points(p))
            a.migrate_history(p)
            after=(list(research.json_lines(p,Counter())),research.digest(p),
                   latency.load_snapshots(p,AT),exposure.load_points(p))
            self.assertEqual(before,after)


if __name__=='__main__':unittest.main()
