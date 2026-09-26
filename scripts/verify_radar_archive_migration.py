"""Test lossless migration on the real checked-out public archive, without HTTP.

Runs existing public research consumers before and after at a fixed cutoff.
Source feed/market bytes, logical history bytes and generated outputs must agree.
The source checkout is intentionally migrated locally; no Git write is performed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from radar_snapshot_archive import DEFAULT_PATH, history_digest, manifest_path, migrate_history, verify_history


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report', type=Path, default=Path('/tmp/radar-archive-migration-verification.json'))
    args=ap.parse_args()
    now=datetime.now(timezone.utc).isoformat()
    before=verify_history(DEFAULT_PATH)
    sources={p:digest(p) for p in ('buy-feed.json','calibration/public-market-history.jsonl')}
    with tempfile.TemporaryDirectory(prefix='radar-migration-') as tmp:
        root=Path(tmp)
        def consumers(label):
            out=root/label;out.mkdir()
            commands=[
                ['build_market_edge_research.py','--now',now,'--output',str(out/'market.json'),'--pairs',str(out/'pairs.jsonl')],
                ['diagnose_market_inputs.py','--now',now,'--output',str(out/'inputs.json')],
                ['build_alert_latency_report.py','--now',now,'--output',str(out/'latency.json')],
                ['build_palladium_m_signal_exposure.py','--output',str(out/'exposure.json')],
            ]
            for command in commands:
                subprocess.run([sys.executable,'scripts/'+command[0],*command[1:]],check=True,capture_output=True,text=True)
            return {p.name:digest(p) for p in sorted(out.iterdir())}
        old_outputs=consumers('before')
        migrate_history(DEFAULT_PATH)
        after=verify_history(DEFAULT_PATH)
        assert before['logicalSha256']==after['logicalSha256']
        assert before['logicalBytes']==after['logicalBytes']
        assert before['records']==after['records']
        new_outputs=consumers('after')
        assert old_outputs==new_outputs, 'Consumer output changed during storage migration'
        manifest_before=manifest_path(DEFAULT_PATH).read_bytes()
        migrate_history(DEFAULT_PATH)
        assert manifest_path(DEFAULT_PATH).read_bytes()==manifest_before, 'Migration not idempotent'
        assert sources=={p:digest(p) for p in sources}, 'Public feed/market inputs were modified'
    report={'role':'LOSSLESS_SNAPSHOT_STORAGE_MIGRATION_VERIFICATION',
        'before':before,'after':after,'logicalBytesIdentical':True,'recordsPreserved':True,
        'consumerOutputsIdentical':True,'consumerOutputSha256':old_outputs,
        'feedAndMarketSourcesUnchanged':True,'repeatMigrationIsNoop':True,
        'cutoff':now,'sourceSha256':sources,'networkRequestsMade':0,
        'adminApiUsed':False,'privateApiUsed':False,'canRaiseSignal':False,
        'currentProductionModelChanged':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,sort_keys=True))


if __name__=='__main__':main()
