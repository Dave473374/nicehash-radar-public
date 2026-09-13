import os
import json
import time
import uuid
import hmac
import hashlib
import requests
from pathlib import Path

BASE = "https://api2.nicehash.com"
KEY = os.environ["NICEHASH_API_KEY"]
SECRET = os.environ["NICEHASH_API_SECRET"].encode()
ORG = os.environ["NICEHASH_ORG_ID"]
PATH = "/hashpower/api/v2/hashpower/solo/shared/order"
QUERY = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

history = [
json.loads(x)
for x in Path("calibration/shared-package-history.jsonl").read_text().splitlines()
if x.strip()
]

seen_ids = sorted(set(str(x.get("package_id") or "") for x in history if x.get("package_id")))

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

active_rows = r.json().get("list", [])
active_ids = sorted(set(str(x.get("id") or "") for x in active_rows if x.get("id")))
disappeared_ids = sorted(set(seen_ids) - set(active_ids))

groups = [
[x for x in history if str(x.get("package_id") or "") == package_id]
for package_id in disappeared_ids
]

summary = [
{
"fingerprint": hashlib.sha256(str(g[0].get("package_id")).encode()).hexdigest()[:10],
"observations": len(g),
"first_seen": g[0].get("collected_at"),
"last_seen": g[-1].get("collected_at"),
"createdTs": g[0].get("createdTs"),
"last_status": g[-1].get("status"),
"duration": g[-1].get("duration"),
"currencyAlgoTicket": g[-1].get("currencyAlgoTicket"),
"participants_first": g[0].get("numberOfParticipants"),
"participants_last": g[-1].get("numberOfParticipants"),
"probability_first": g[0].get("probability"),
"probability_last": g[-1].get("probability"),
"mergeProbability_last": g[-1].get("mergeProbability"),
"projectedSpeed_last": g[-1].get("projectedSpeed"),
"has_createdTs": g[0].get("createdTs") is not None,
"has_duration": g[-1].get("duration") is not None,
"has_currency": g[-1].get("currencyAlgoTicket") is not None
}
for g in groups
if g
]

print("HTTP", r.status_code)
print("DISAPPEARED", len(disappeared_ids))
print("DIAGNOSTIC")
print(json.dumps(summary, indent=2))
