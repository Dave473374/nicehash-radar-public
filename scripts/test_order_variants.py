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
QUERY = "status=COMPLETED&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

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
d = r.json()
rows = d.get("list", [])
first = rows[0] if rows else {}

details = first.get("orderDetails")
members = first.get("members")

print("HTTP", r.status_code)
print("COMPLETED rows", len(rows))

print("orderDetails type:", type(details).__name__)
print("orderDetails keys:", sorted(details.keys()) if isinstance(details, dict) else [])
print("orderDetails count:", len(details) if isinstance(details, list) else 0)
print("orderDetails item type:", type(details[0]).__name__ if isinstance(details, list) and details else "none")
print("orderDetails item keys:", sorted(details[0].keys()) if isinstance(details, list) and details and isinstance(details[0], dict) else [])

print("members type:", type(members).__name__)
print("members keys:", sorted(members.keys()) if isinstance(members, dict) else [])
print("members count:", len(members) if isinstance(members, list) else 0)
print("members item type:", type(members[0]).__name__ if isinstance(members, list) and members else "none")
print("members item keys:", sorted(members[0].keys()) if isinstance(members, list) and members and isinstance(members[0], dict) else [])
