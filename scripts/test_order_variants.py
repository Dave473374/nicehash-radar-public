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

history = [
json.loads(x)
for x in Path("calibration/shared-package-history.jsonl").read_text().splitlines()
if x.strip()
]

seen_ids = sorted(set(str(x.get("package_id") or "") for x in history if x.get("package_id")))

ACTIVE_QUERY = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, ACTIVE_QUERY]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}

active_r = requests.get(BASE + PATH + "?" + ACTIVE_QUERY, headers=headers, timeout=30)
active_rows = active_r.json().get("list", [])
active_ids = sorted(set(str(x.get("id") or "") for x in active_rows if x.get("id")))

disappeared_ids = sorted(set(seen_ids) - set(active_ids))

COMPLETED_QUERY = "status=COMPLETED&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

ts2 = str(int(time.time() * 1000))
nonce2 = str(uuid.uuid4())
reqid2 = str(uuid.uuid4())
msg2 = "\x00".join([KEY, ts2, nonce2, "", ORG, "", "GET", PATH, COMPLETED_QUERY]).encode()
sig2 = hmac.new(SECRET, msg2, hashlib.sha256).hexdigest()
headers2 = {"X-Time": ts2, "X-Nonce": nonce2, "X-Auth": KEY + ":" + sig2, "X-Organization-Id": ORG, "X-Request-Id": reqid2}

completed_r = requests.get(BASE + PATH + "?" + COMPLETED_QUERY, headers=headers2, timeout=30)
completed_rows = completed_r.json().get("list", [])

matched_rows = [
x
for x in completed_rows
if str(x.get("id") or "") in disappeared_ids
]

matched_flags = [
bool((x.get("orderDetails") or {}).get("isReward"))
for x in matched_rows
]

matched_reward_amounts = [
sum(
float(m.get("rewardAmount") or 0)
for m in (x.get("members") or [])
if isinstance(m, dict)
)
for x in matched_rows
]

matched_fingerprints = [
hashlib.sha256(str(x.get("id") or "").encode()).hexdigest()[:10]
for x in matched_rows
]

print("ACTIVE HTTP", active_r.status_code)
print("COMPLETED HTTP", completed_r.status_code)
print("TOTAL EVER SEEN", len(seen_ids))
print("CURRENT ACTIVE", len(active_ids))
print("DISAPPEARED", len(disappeared_ids))
print("COMPLETED ROWS RETURNED", len(completed_rows))
print("DISAPPEARED MATCHED IN COMPLETED", len(matched_rows))
print("MATCHED fingerprints", matched_fingerprints)
print("MATCHED isReward", matched_flags)
print("MATCHED rewardAmount totals", matched_reward_amounts)
