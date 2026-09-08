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

path = "/hashpower/api/v2/hashpower/solo/order"
query = "page=0&limit=100"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", path, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + path + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
print("--- OWN ORDER - no filters ---")
print("HTTP", r.status_code)
print("Rows", len(rows))
print("Top-level keys", sorted(d.keys()))
print("Row keys", (rows[:1] and sorted(rows[0].keys())) or [])

path = "/hashpower/api/v2/hashpower/solo/shared/order"
query = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", path, query]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
r = requests.get(BASE + path + "?" + query, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])
print("--- SHARED ORDER ---")
print("HTTP", r.status_code)
print("Rows", len(rows))
print("Top-level keys", sorted(d.keys()))
print("Row keys", (rows[:1] and sorted(rows[0].keys())) or [])
