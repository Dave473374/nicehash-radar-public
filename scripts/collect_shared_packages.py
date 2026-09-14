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

output = Path("calibration/shared-package-history.jsonl")
output.parent.mkdir(parents=True, exist_ok=True)

existing_lines = output.read_text().splitlines() if output.exists() else []
existing = [json.loads(line) for line in existing_lines if line.strip()]

existing_keys = set(
    x.get("state_key")
    for x in existing
)

active_ids = set(
    str(row.get("id") or "")
    for row in rows
    if row.get("id")
)

latest_by_id = {}

for x in existing:
    package_id = str(x.get("package_id") or "")
    if package_id:
        latest_by_id[package_id] = x

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

disappeared_records = [
    {
        **last,
        "state_key": hashlib.sha256(
            (
                package_id
                + "|DISAPPEARED|"
                + collected_at
            ).encode()
        ).hexdigest(),
        "collected_at": collected_at,
        "status": "DISAPPEARED"
    }
    for package_id, last in latest_by_id.items()
    if package_id not in active_ids
    and last.get("status") != "DISAPPEARED"
]

new_records = [
    record
    for record in records + disappeared_records
    if record["package_id"]
    and record["state_key"] not in existing_keys
]

new_text = "".join(
    json.dumps(record, separators=(",", ":")) + "\n"
    for record in new_records
)

existing_text = output.read_text() if output.exists() else ""
output.write_text(existing_text + new_text)

print("HTTP", r.status_code)
print("ACTIVE SHARED PACKAGES", len(rows))
print("NEW STATES SAVED", len(new_records))
print("NEW DISAPPEARED", len(disappeared_records))
print("TOTAL HISTORY ROWS", len(existing_lines) + len(new_records))
print("STATUSES", sorted(set(str(row.get("status")) for row in rows)))
