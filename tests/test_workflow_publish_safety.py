"""Run the actual workflow save steps against temporary, file-only Git remotes."""
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ('recent-blocks.yml', 'update', 'recent-blocks.json', '*/5 * * * *'),
    ('match-mining-events.yml', 'match', 'calibration/mining-event-matches.jsonl', '7,22,37,52 * * * *'),
)
REAL_GIT = shutil.which('git')


def workflow(case):
    return json.loads((ROOT / '.github/workflows' / case[0]).read_text())


class LocalRemote:
    def __init__(self, root, output):
        self.root, self.output = root, output
        self.remote, self.worker, self.peer = (root / x for x in ('remote.git', 'worker', 'peer'))
        self.calls = root / 'calls'
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='file',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git(root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(root, 'clone', str(self.remote), str(self.peer))
        self.write(self.peer, output, 'original\n')
        self.write(self.peer, 'other.txt', 'original\n')
        self.write(self.peer, 'buy-feed.json', '{"production":"unchanged"}\n')
        self.git(self.peer, 'add', '.')
        self.git(self.peer, 'commit', '-m', 'fixture')
        self.git(self.peer, 'push', 'origin', 'HEAD:main')
        self.git(root, 'clone', '--depth=1', '--branch', 'main', self.remote.as_uri(), str(self.worker))
        self.base = self.git(self.remote, 'rev-parse', 'main').stdout.strip()
        self.bin = root / 'bin'
        self.bin.mkdir()
        # Only injected failures/delays are mocked; successful operations use real Git.
        shim = '''#!/usr/bin/env bash
set -eu
printf '%s\\n' "${1-}" >> "$TEST_CALLS"
if [ "${1-}" = "${TEST_FAIL_COMMAND:-never}" ]; then exit 42; fi
if [ "${1-}" = push ]; then
  count=$(grep -c '^push$' "$TEST_CALLS")
  if [ "${TEST_PUSH_MODE:-}" = reject ]; then exit 42; fi
  if [ "${TEST_PUSH_MODE:-}" = race_twice ] && [ "$count" -le 2 ]; then
    printf 'external-%s\\n' "$count" > "$TEST_PEER/other.txt"
    "$TEST_REAL_GIT" -C "$TEST_PEER" add other.txt
    "$TEST_REAL_GIT" -C "$TEST_PEER" commit -m "external-$count"
    "$TEST_REAL_GIT" -C "$TEST_PEER" push origin HEAD:main
  fi
  if [ "${TEST_PUSH_MODE:-}" = lost_response ] && [ "$count" -eq 1 ]; then
    "$TEST_REAL_GIT" "$@"
    exit 42
  fi
fi
exec "$TEST_REAL_GIT" "$@"
'''
        (self.bin / 'git').write_text(shim)
        (self.bin / 'sleep').write_text('#!/usr/bin/env bash\nexit 0\n')
        for path in self.bin.iterdir():
            path.chmod(0o755)
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                        TEST_CALLS=str(self.calls), TEST_REAL_GIT=REAL_GIT, TEST_PEER=str(self.peer))

    def git(self, cwd, *args):
        return subprocess.run([REAL_GIT, *args], cwd=cwd, env=self.env, text=True,
                              capture_output=True, check=True, timeout=15)

    @staticmethod
    def write(where, path, text):
        target = where / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def advance(self, path, text):
        self.write(self.peer, path, text)
        self.git(self.peer, 'add', '--', path)
        self.git(self.peer, 'commit', '-m', 'external update')
        self.git(self.peer, 'push', 'origin', 'HEAD:main')

    def run(self, code, **env):
        return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', code], cwd=self.worker,
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=20)

    def remote_file(self, path):
        return self.git(self.remote, 'show', 'main:' + path).stdout

    def call_count(self, name):
        return self.calls.read_text().splitlines().count(name) if self.calls.exists() else 0


@unittest.skipUnless(REAL_GIT and shutil.which('bash'), 'git and bash required')
class WorkflowPublishSafetyTests(unittest.TestCase):
    def scenarios(self, run_case):
        for case in CASES:
            with self.subTest(workflow=case[0]), tempfile.TemporaryDirectory() as tmp:
                repo = LocalRemote(Path(tmp), case[2])
                code = workflow(case)['jobs'][case[1]]['steps'][-1]['run']
                run_case(repo, code)
                self.assertEqual(repo.remote_file('buy-feed.json'), '{"production":"unchanged"}\n')

    def test_first_push_succeeds(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(r.remote_file(r.output), 'new\n')
            self.assertEqual(r.call_count('push'), 1)
        self.scenarios(check)

    def test_no_changes_does_not_commit_or_push_even_when_remote_advanced(self):
        def check(r, code):
            r.advance('other.txt', 'external\n')
            result = r.run(code)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(r.call_count('commit'), 0)
            self.assertEqual(r.call_count('push'), 0)
            self.assertEqual(r.remote_file('other.txt'), 'external\n')
        self.scenarios(check)

    def test_shallow_clone_recovers_from_non_fast_forward_and_keeps_both_outputs(self):
        def check(r, code):
            self.assertEqual(r.git(r.worker, 'rev-parse', '--is-shallow-repository').stdout.strip(), 'true')
            r.advance('other.txt', 'external\n')
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[rejected]', result.stderr)
            self.assertEqual(r.call_count('push'), 2)
            self.assertEqual(r.remote_file(r.output), 'new\n')
            self.assertEqual(r.remote_file('other.txt'), 'external\n')
        self.scenarios(check)

    def test_two_real_races_recover_on_third_attempt(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_PUSH_MODE='race_twice')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(r.call_count('push'), 3)
            self.assertEqual(r.remote_file(r.output), 'new\n')
            self.assertEqual(r.remote_file('other.txt'), 'external-2\n')
        self.scenarios(check)

    def test_output_changed_remotely_fails_closed(self):
        def check(r, code):
            r.advance(r.output, 'external output\n')
            before = r.git(r.remote, 'rev-parse', 'main').stdout
            r.write(r.worker, r.output, 'local output\n')
            result = r.run(code)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Output changed on main', result.stdout)
            self.assertEqual(r.call_count('push'), 1)
            self.assertEqual(r.remote_file(r.output), 'external output\n')
            self.assertEqual(r.git(r.remote, 'rev-parse', 'main').stdout, before)
        self.scenarios(check)

    def test_real_rebase_conflict_is_aborted_without_remote_mutation(self):
        def check(r, code):
            # An unexpected pre-existing local change must not be resolved by ours/theirs.
            r.write(r.worker, 'other.txt', 'local conflicting line\n')
            r.git(r.worker, 'add', 'other.txt')
            r.git(r.worker, 'commit', '-m', 'unexpected local commit')
            r.advance('other.txt', 'external conflicting line\n')
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Rebase conflict', result.stdout)
            self.assertEqual(r.remote_file(r.output), 'original\n')
            self.assertEqual(r.remote_file('other.txt'), 'external conflicting line\n')
            self.assertFalse((r.worker / '.git/rebase-merge').exists())
            self.assertFalse((r.worker / '.git/rebase-apply').exists())
        self.scenarios(check)

    def test_persistent_push_failure_is_bounded_and_not_green(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_PUSH_MODE='reject')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(r.call_count('push'), 3)
            self.assertEqual(r.call_count('fetch'), 2)
            self.assertIn('not published', result.stdout)
            self.assertEqual(r.remote_file(r.output), 'original\n')
        self.scenarios(check)

    def test_staging_failure_cannot_fall_through_to_commit_or_push(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_FAIL_COMMAND='add')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(r.call_count('commit'), 0)
            self.assertEqual(r.call_count('push'), 0)
        self.scenarios(check)

    def test_commit_failure_cannot_fall_through_to_push(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_FAIL_COMMAND='commit')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(r.call_count('push'), 0)
        self.scenarios(check)

    def test_fetch_failure_stops_before_retry(self):
        def check(r, code):
            r.advance('other.txt', 'external\n')
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_FAIL_COMMAND='fetch')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(r.call_count('push'), 1)
            self.assertEqual(r.remote_file(r.output), 'original\n')
        self.scenarios(check)

    def test_successful_push_with_lost_response_is_idempotent(self):
        def check(r, code):
            r.write(r.worker, r.output, 'new\n')
            result = r.run(code, TEST_PUSH_MODE='lost_response')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(r.call_count('push'), 2)
            self.assertEqual(r.remote_file(r.output), 'new\n')
            self.assertEqual(r.git(r.remote, 'rev-list', '--count', r.base + '..main').stdout.strip(), '1')
        self.scenarios(check)

    def test_workflow_contract(self):
        groups = set()
        for case in CASES:
            with self.subTest(workflow=case[0]):
                data = workflow(case)
                self.assertEqual(set(data['on']), {'workflow_dispatch', 'schedule'})
                self.assertEqual(data['on']['schedule'], [{'cron': case[3]}])
                self.assertEqual(data['permissions'], {'contents': 'write'})
                self.assertIs(data['concurrency']['cancel-in-progress'], False)
                groups.add(data['concurrency']['group'])
                job = data['jobs'][case[1]]
                self.assertEqual(job['if'], "github.ref == 'refs/heads/main'")
                self.assertEqual(job['steps'][0]['with'], {'ref': 'main', 'fetch-depth': 1})
                save = job['steps'][-1]
                self.assertEqual(save['shell'], 'bash')
                self.assertIn('set -euo pipefail', save['run'])
                self.assertIn('git add -- ' + shlex.quote(case[2]), save['run'])
                for forbidden in ('--force', 'reset --hard', '--ours', '--theirs', 'git add .', 'git add -A'):
                    self.assertNotIn(forbidden, save['run'])
        self.assertEqual(len(groups), len(CASES))


if __name__ == '__main__':
    unittest.main()
