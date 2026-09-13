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

base_query = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, base_query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + base_query, headers=headers, timeout=30)
active_rows = r.json().get("list", [])
active_ids = sorted(set(str(x.get("id") or "") for x in active_rows if x.get("id")))

disappeared = sorted(set(seen_ids) - set(active_ids))

target1 = disappeared[0] if len(disappeared) > 0 else ""
target2 = disappeared[1] if len(disappeared) > 1 else ""

query1 = "id=" + target1
ts1 = str(int(time.time() * 1000))
nonce1 = str(uuid.uuid4())
reqid1 = str(uuid.uuid4())
msg1 = "\x00".join([KEY, ts1, nonce1, "", ORG, "", "GET", PATH, query1]).encode()
sig1 = hmac.new(SECRET, msg1, hashlib.sha256).hexdigest()
headers1 = {"X-Time": ts1, "X-Nonce": nonce1, "X-Auth": KEY + ":" + sig1, "X-Organization-Id": ORG, "X-Request-Id": reqid1}
r1 = requests.get(BASE + PATH + "?" + query1, headers=headers1, timeout=30)
d1 = r1.json()
rows1 = d1.get("list", []) if isinstance(d1, dict) else []
matches1 = [x for x in rows1 if str(x.get("id") or "") == target1]

query2 = "id=" + target2
ts2 = str(int(time.time() * 1000))
nonce2 = str(uuid.uuid4())
reqid2 = str(uuid.uuid4())
msg2 = "\x00".join([KEY, ts2, nonce2, "", ORG, "", "GET", PATH, query2]).encode()
sig2 = hmac.new(SECRET, msg2, hashlib.sha256).hexdigest()
headers2 = {"X-Time": ts2, "X-Nonce": nonce2, "X-Auth": KEY + ":" + sig2, "X-Organization-Id": ORG, "X-Request-Id": reqid2}
r2 = requests.get(BASE + PATH + "?" + query2, headers=headers2, timeout=30)
d2 = r2.json()
rows2 = d2.get("list", []) if isinstance(d2, dict) else []
matches2 = [x for x in rows2 if str(x.get("id") or "") == target2]

status1 = [x.get("status") for x in matches1]
status2 = [x.get("status") for x in matches2]

details1 = [x.get("orderDetails") or {} for x in matches1]
details2 = [x.get("orderDetails") or {} for x in matches2]

reward1 = [bool(x.get("isReward")) for x in details1]
reward2 = [bool(x.get("isReward")) for x in details2]

print("DISAPPEARED", len(disappeared))
print("LOOKUP 1 HTTP", r1.status_code, "| returned rows", len(rows1), "| exact matches", len(matches1))
print("LOOKUP 1 status", status1, "| isReward", reward1)
print("LOOKUP 2 HTTP", r2.status_code, "| returned rows", len(rows2), "| exact matches", len(matches2))
print("LOOKUP 2 status", status2, "| isReward", reward2)
