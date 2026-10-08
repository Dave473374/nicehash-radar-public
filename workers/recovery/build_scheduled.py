"""Deterministic single-file scheduled Worker build; no network, credentials or deployment."""
from pathlib import Path
import hashlib
import json
ROOT = Path(__file__).resolve().parent
BASE_SHA256 = 'd151375be73e45b51294dca1706a5b88dcffb70952762fe295cbe32f87d5c1f2'

def build():
    raw = (ROOT/'worker.js').read_bytes()
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError('Unexpected previously tested recovery.1 source; reconcile before building')
    modules = [(ROOT/name).read_text() for name in ['scheduled-math.js','scheduler-runtime.js']]
    digest = hashlib.sha256(raw + b'\0' + Path(__file__).read_bytes() + b'\0' + '\n'.join(modules).encode()).hexdigest()
    revision = 'scheduler-recovery.2-sha256:' + digest
    source = raw.decode()
    assert source.count('export default {') == 1
    source = source.replace('export default {','const radarCore = {',1)
    source = source.replace('"2.9.0-history-recovery.1"','"2.9.0-history-recovery.2"',1)
    source = source.replace('source_revision: "6a4fc86fca0f53ec8b90ce3188dc8d728f06e5a9"','source_revision: SCHEDULED_SOURCE_REVISION')
    source = 'const SCHEDULED_SOURCE_REVISION = ' + json.dumps(revision) + ';\n' + source + '\n' + '\n'.join(modules)
    (ROOT/'worker-scheduled.js').write_text(source)
    (ROOT/'worker-scheduled.mjs').write_text(source + '\nexport {radarCore, collectScheduled, readScheduledFeed, scheduledHealth, scheduledChainCheck, annotateScheduledMath};\n')
    manifest = {'base_sha256':BASE_SHA256,'worker_sha256':hashlib.sha256(source.encode()).hexdigest(),
      'source_revision':revision,'base_git_ref':'78b8989d30d2dad63f3a087a21c03c5cb3982598',
      'relay_version':'2.9.0-history-recovery.2','cron':'*/5 * * * *','same_existing_worker':True,
      'cloudflare_deployed':False,'cron_configured_by_builder':False,'signal_thresholds_changed':False,
      'public_http_writes_history':False,'readme':'docs/buy-radar-scheduler-recovery.md'}
    (ROOT/'scheduled-build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
    return manifest
if __name__ == '__main__': build()
