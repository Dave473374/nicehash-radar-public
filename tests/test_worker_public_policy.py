"""Exact-byte public Worker approval does not exempt private auth or other files."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'scripts/check_private_api_policy.py'
SOURCE = ROOT / 'workers/recovery/worker.js'

class WorkerPublicPolicy(unittest.TestCase):
    def run_guard(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, content in files.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)
            return subprocess.run([sys.executable, str(GUARD)], cwd=root,
                                  text=True, capture_output=True, timeout=10)

    def test_reviewed_exact_worker_and_base_are_accepted(self):
        files = {str(p.relative_to(ROOT)): p.read_bytes() for p in
                 [SOURCE, ROOT/'workers/recovery/base-worker-v2.9.0.js']}
        r = self.run_guard(files)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)

    def test_any_changed_worker_requires_explicit_review(self):
        r = self.run_guard({'workers/recovery/worker.js': SOURCE.read_bytes()+b'\n// changed'})
        self.assertNotEqual(r.returncode, 0)

    def test_unapproved_filename_does_not_inherit_exception(self):
        r = self.run_guard({'workers/another.js': SOURCE.read_bytes()})
        self.assertNotEqual(r.returncode, 0)

    def test_added_auth_is_still_forbidden(self):
        marker = ('X-'+'Auth').encode()
        r = self.run_guard({'workers/recovery/worker.js': SOURCE.read_bytes()+b'\n'+marker})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('private API auth material', r.stdout)

    def test_private_module_write_remains_forbidden(self):
        method = ('requests.'+'post(').encode()
        r = self.run_guard({'scripts/nicehash_private_readonly.py':method})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('write-capable private HTTP method', r.stdout)

if __name__ == '__main__': unittest.main()
