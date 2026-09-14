import json
from pathlib import Path

shared_path = Path("calibration/shared-package-history.jsonl")
blocks_path = Path("calibration/realized-blocks.jsonl")

shared = [json.loads(x) for x in shared_path.read_text().splitlines() if x.strip()]
blocks = [json.loads(x) for x in blocks_path.read_text().splitlines() if x.strip()]

package_ids = sorted(set(str(x.get("package_id") or "") for x in shared if x.get("package_id")))

groups = [
    [x for x in shared if str(x.get("package_id") or "") == package_id]
    for package_id in package_ids
]

last_rows = [g[-1] for g in groups if g]

print("SHARED HISTORY ROWS", len(shared))
print("UNIQUE SHARED PACKAGES", len(package_ids))
print("REALIZED BLOCK ROWS", len(blocks))

print("SHARED SAMPLE KEYS")
print(sorted(last_rows[0].keys()) if last_rows else [])

print("REALIZED BLOCK SAMPLE KEYS")
print(sorted(blocks[0].keys()) if blocks else [])

print("SHARED SAMPLE")
print({
    "createdTs": last_rows[0].get("createdTs") if last_rows else None,
    "collected_at": last_rows[0].get("collected_at") if last_rows else None,
    "duration": last_rows[0].get("duration") if last_rows else None,
    "status": last_rows[0].get("status") if last_rows else None,
    "currencyAlgoTicket_type": type(last_rows[0].get("currencyAlgoTicket")).__name__ if last_rows else None
})

print("REALIZED BLOCK SAMPLE")
print(blocks[0] if blocks else {})
