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


def signed_get(path, query):
ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())

message = "\x00".join([
KEY,
ts,
nonce,
"",
ORG,
"",
"GET",
path,
query
]).encode()

signature = hmac.new(
SECRET,
message,
hashlib.sha256
).hexdigest()

headers = {
"X-Time": ts,
"X-Nonce": nonce,
"X-Auth": KEY + ":" + signature,
"X-Organization-Id": ORG,
"X-Request-Id": reqid
}

return requests.get(
BASE + path + ("?" + query if query else ""),
headers=headers,
timeout=30
)


def report(label, response):
print()
print("---", label, "---")
print("HTTP", response.status_code)

data = response.json()
rows = data.get("list", []) if isinstance(data, dict) else []

print("Rows", len(rows))
print(
"Top-level keys",
sorted(data.keys()) if isinstance(data, dict) else []
)
print(
"Row keys",
sorted(rows[0].keys()) if rows else []
)


own = signed_get(
"/hashpower/api/v2/hashpower/solo/order",
"page=0&limit=100"
)

shared = signed_get(
"/hashpower/api/v2/hashpower/solo/shared/order",
"page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"
)

report("OWN ORDER - no filters", own)
report("SHARED ORDER", shared)
