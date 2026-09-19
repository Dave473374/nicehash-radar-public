import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from nicehash_private_readonly import get_shared_easymining_orders

rows_raw = get_shared_easymining_orders(
    page=0,
    limit=100,
    sort_dir="ASC",
    sort_field="createdTs",
    only_gold=False,
)

rows = [
    row
    for row in rows_raw
    if isinstance(row, dict) and row.get("isPublic") is True
]

collected_at = datetime.now(timezone.utc).isoformat()

history_path = Path("calibration/shared-package-history.jsonl")
snapshot_path = Path("calibration/shared-active-snapshot.json")

history_path.parent.mkdir(parents=True, exist_ok=True)

existing_lines = history_path.read_text().splitlines() if history_path.exists() else []
existing = [json.loads(line) for line in existing_lines if line.strip()]

first_snapshot = not snapshot_path.exists()

if first_snapshot:
    existing = [x for x in existing if x.get("status") != "DISAPPEARED"]
    history_path.write_text(
        "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in existing)
    )

previous_ids = set()

if snapshot_path.exists():
    previous_snapshot = json.loads(snapshot_path.read_text())
    previous_ids = set(str(x) for x in previous_snapshot.get("active_ids", []))

active_ids = set(
    str(row.get("id"))
    for row in rows
    if row.get("id")
)

latest_by_id = {}

for x in existing:
    package_id = str(x.get("package_id") or "")
    if package_id:
        latest_by_id[package_id] = x

existing_keys = set(
    x.get("state_key")
    for x in existing
    if x.get("state_key")
)

records = [
    {
        "state_key": hashlib.sha256(
            (
                str(row.get("id") or "")
                + "|"
                + str(row.get("status") or "")
                + "|"
                + str(row.get("numberOfParticipants") or "")
                + "|"
                + str(row.get("probability") or "")
                + "|"
                + str(row.get("mergeProbability") or "")
            ).encode()
        ).hexdigest(),
        "package_id": str(row.get("id") or ""),
        "collected_at": collected_at,
        "createdTs": row.get("createdTs"),
        "status": row.get("status"),
        "duration": row.get("duration"),
        "countdownDuration": row.get("countdownDuration"),
        "numberOfParticipants": row.get("numberOfParticipants"),
        "probability": row.get("probability"),
        "probabilityPrecision": row.get("probabilityPrecision"),
        "mergeProbability": row.get("mergeProbability"),
        "mergeProbabilityPrecision": row.get("mergeProbabilityPrecision"),
        "projectedSpeed": row.get("projectedSpeed"),
        "currencyAlgoTicket": row.get("currencyAlgoTicket"),
        "addedAmount": row.get("addedAmount"),
        "fallAmount": row.get("fallAmount"),
        "isPublic": row.get("isPublic"),
        "spawningMode": row.get("spawningMode")
    }
    for row in rows
]

disappeared_ids = previous_ids - active_ids

disappeared_records = [
    {
        **latest_by_id[package_id],
        "state_key": hashlib.sha256(
            (package_id + "|DISAPPEARED|" + collected_at).encode()
        ).hexdigest(),
        "collected_at": collected_at,
        "status": "DISAPPEARED"
    }
    for package_id in disappeared_ids
    if package_id in latest_by_id
]

new_records = [
    record
    for record in records + disappeared_records
    if record.get("package_id")
    and record.get("state_key") not in existing_keys
]

with history_path.open("a") as f:
    for record in new_records:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")

snapshot_path.write_text(
    json.dumps(
        {
            "collected_at": collected_at,
            "active_ids": sorted(active_ids)
        },
        separators=(",", ":")
    )
)

print("PUBLIC ACTIVE SHARED PACKAGES", len(rows))
print("NONPUBLIC SHARED ROWS EXCLUDED", len(rows_raw) - len(rows))
print("PREVIOUS ACTIVE PACKAGES", len(previous_ids))
print("NEW STATES SAVED", len(new_records))
print("NEW DISAPPEARED", len(disappeared_records))
print("TOTAL HISTORY ROWS", len(existing) + len(new_records))
print("STATUSES", sorted(set(str(row.get("status")) for row in rows)))
print("SNAPSHOT INITIALIZED", first_snapshot)
