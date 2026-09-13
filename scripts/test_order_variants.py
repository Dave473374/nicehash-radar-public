import os
import time
import uuid
import hmac
import hashlib
import requests

BASE = "https://api2.nicehash.com"
KEY = os.environ["NICEHASH_API_KEY"]
SECRET = os.environ["NICEHASH_API_SECRET"].encode()
ORG = os.environ["NICEHASH_ORG_ID"]
PATH = "/hashpower/api/v2/hashpower/solo/shared/order"

status = "COMPLETED"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))

status = "FINISHED"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))

status = "SUCCESS"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))

status = "REWARDED"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))

status = "EXPIRED"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))

status = "CANCELLED"
query = "status=" + status + "&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
reward_values = [float(m.get("rewardAmount") or 0) for row in rows for m in (row.get("members") or [])]
print(status, "| HTTP", r.status_code, "| rows", len(rows), "| returned statuses", sorted(set(str(x.get("status")) for x in rows)), "| reward hits", sum(x > 0 for x in reward_values))
