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

query = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", []) if isinstance(d, dict) else []
print("BASELINE | HTTP", r.status_code, "| rows", len(rows), "| statuses", sorted(set(str(x.get("status")) for x in rows)))

query = "active=false&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", []) if isinstance(d, dict) else []
print("ACTIVE FALSE | HTTP", r.status_code, "| rows", len(rows), "| statuses", sorted(set(str(x.get("status")) for x in rows)))

query = "status=COMPLETED&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", []) if isinstance(d, dict) else []
print("STATUS COMPLETED | HTTP", r.status_code, "| rows", len(rows), "| statuses", sorted(set(str(x.get("status")) for x in rows)))

query = "page=1&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", []) if isinstance(d, dict) else []
print("PAGE 1 | HTTP", r.status_code, "| rows", len(rows), "| statuses", sorted(set(str(x.get("status")) for x in rows)))

query = "page=0&limit=100&sortDir=DESC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", PATH, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + PATH + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", []) if isinstance(d, dict) else []
print("DESC | HTTP", r.status_code, "| rows", len(rows), "| statuses", sorted(set(str(x.get("status")) for x in rows)))
