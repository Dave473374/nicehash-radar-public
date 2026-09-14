import os
import time
import uuid
import hmac
import hashlib
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api2.nicehash.com"
KEY = os.environ["NICEHASH_API_KEY"]
SECRET = os.environ["NICEHASH_API_SECRET"].encode()
ORG = os.environ["NICEHASH_ORG_ID"]

PATH = "/hashpower/api/v2/hashpower/solo/shared/order"
QUERY = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())

msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, QUERY]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()

headers = {
    "X-Time": ts,
    "X-Nonce": nonce,
    "X-Auth": KEY + ":" + sig,
    "X-Organization-Id": ORG,
    "X-Request-Id": reqid
}

r = requests.get(BASE + PATH + "?" + QUERY, headers=headers, timeout=30)
r.raise_for_status()

rows = r.json().get("list", [])
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

print("HTTP", r.status_code)
print("ACTIVE SHARED PACKAGES", len(rows))
print("PREVIOUS ACTIVE PACKAGES", len(previous_ids))
print("NEW STATES SAVED", len(new_records))
print("NEW DISAPPEARED", len(disappeared_records))
print("TOTAL HISTORY ROWS", len(existing) + len(new_records))
print("STATUSES", sorted(set(str(row.get("status")) for row in rows)))
print("SNAPSHOT INITIALIZED", first_snapshot)
