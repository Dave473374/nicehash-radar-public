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

history_path = Path("calibration/shared-package-history.jsonl")
history = [json.loads(x) for x in history_path.read_text().splitlines() if x.strip()]

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

current_rows = r.json().get("list", [])
active_ids = sorted(set(str(x.get("id") or "") for x in current_rows if x.get("id")))

disappeared_ids = sorted(set(seen_ids) - set(active_ids))
still_active_seen_ids = sorted(set(seen_ids) & set(active_ids))

fingerprints = [
hashlib.sha256(x.encode()).hexdigest()[:10]
for x in disappeared_ids
]

last_statuses = [
[
x.get("status")
for x in reversed(history)
if str(x.get("package_id") or "") == package_id
][0]
for package_id in disappeared_ids
]

print("HTTP", r.status_code)
print("TOTAL UNIQUE PACKAGES EVER SEEN", len(seen_ids))
print("CURRENT ACTIVE PACKAGES", len(active_ids))
print("SEEN PACKAGES STILL ACTIVE", len(still_active_seen_ids))
print("DISAPPEARED CANDIDATES", len(disappeared_ids))
print("DISAPPEARED fingerprints", fingerprints)
print("LAST KNOWN statuses", last_statuses)
