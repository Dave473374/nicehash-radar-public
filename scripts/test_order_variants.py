import os
import time
import uuid
import hmac
import hashlib
import requests
import json

BASE = "https://api2.nicehash.com"
KEY = os.environ["NICEHASH_API_KEY"]
SECRET = os.environ["NICEHASH_API_SECRET"].encode()
ORG = os.environ["NICEHASH_ORG_ID"]

path = "/hashpower/api/v2/hashpower/solo/shared/order"
query = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())

msg = "\x00".join([
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

sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()

headers = {
"X-Time": ts,
"X-Nonce": nonce,
"X-Auth": KEY + ":" + sig,
"X-Organization-Id": ORG,
"X-Request-Id": reqid
}

r = requests.get(
BASE + path + "?" + query,
headers=headers,
timeout=30
)

d = r.json()
rows = d.get("list", [])

safe_rows = [
{
"createdTs": x.get("createdTs"),
"duration": x.get("duration"),
"status": x.get("status"),
"isPublic": x.get("isPublic"),
"numberOfParticipants": x.get("numberOfParticipants"),
"probability": x.get("probability")
}
for x in rows
]

print("HTTP", r.status_code)
print("SHARED rows", len(rows))
print("SAFE SHARED DATA")
print(json.dumps(safe_rows, indent=None))
