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
PATH = "/hashpower/api/v2/hashpower/solo/shared/order"
QUERY = "status=COMPLETED&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

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
PATH,
QUERY
]).encode()

sig = hmac.new(
SECRET,
msg,
hashlib.sha256
).hexdigest()

headers = {
"X-Time": ts,
"X-Nonce": nonce,
"X-Auth": KEY + ":" + sig,
"X-Organization-Id": ORG,
"X-Request-Id": reqid
}

r = requests.get(
BASE + PATH + "?" + QUERY,
headers=headers,
timeout=30
)

d = r.json()
rows = d.get("list", [])

print("HTTP", r.status_code)
print("COMPLETED rows", len(rows))
print("ROW KEYS", sorted(rows[0].keys()) if rows else [])

safe_rows = [
{
"createdTs": x.get("createdTs"),
"duration": x.get("duration"),
"status": x.get("status"),
"numberOfParticipants": x.get("numberOfParticipants"),
"probability": x.get("probability"),
"mergeProbability": x.get("mergeProbability"),
"isPublic": x.get("isPublic"),
"filledAmount": x.get("filledAmount"),
"addedAmount": x.get("addedAmount"),
"countdownDuration": x.get("countdownDuration")
}
for x in rows
]

print("SAFE COMPLETED DATA")
print(json.dumps(safe_rows, separators=(",", ":")))
