import json
import hashlib
from pathlib import Path

shared = [json.loads(x) for x in Path("calibration/shared-package-history.jsonl").read_text().splitlines() if x.strip()]
blocks = [json.loads(x) for x in Path("calibration/realized-blocks.jsonl").read_text().splitlines() if x.strip()]

shared_ids = sorted(set(str(x.get("package_id")) for x in shared if x.get("package_id")))
block_ids = set(str(x.get("packageId")) for x in blocks if x.get("packageId"))

matched_ids = sorted(set(shared_ids) & block_ids)

def fp(x):
    return hashlib.sha256(x.encode()).hexdigest()[:10]

print("SHARED HISTORY ROWS", len(shared))
print("UNIQUE TRACKED PACKAGES", len(shared_ids))
print("REALIZED BLOCK ROWS", len(blocks))
print("EXACT PACKAGE ID MATCHES", len(matched_ids))

print("MATCHED PACKAGE FINGERPRINTS")
print([fp(x) for x in matched_ids])

matched_blocks = [x for x in blocks if str(x.get("packageId")) in matched_ids]

print("MATCHED BLOCK SUMMARY")
print([
    {
        "package_fp": fp(str(x.get("packageId"))),
        "coin": x.get("coin"),
        "packageName": x.get("packageName"),
        "shared": x.get("shared"),
        "createdTs": x.get("createdTs")
    }
    for x in matched_blocks
])
