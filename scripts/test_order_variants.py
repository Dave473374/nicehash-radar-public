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
QUERY = "page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

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

ids = [str(x.get("id") or "") for x in rows]
statuses = [str(x.get("status") or "") for x in rows]
created = [str(x.get("createdTs") or "") for x in rows]
durations = [x.get("duration") for x in rows]
participants = [x.get("numberOfParticipants") for x in rows]
probabilities = [x.get("probability") for x in rows]
merge_probabilities = [x.get("mergeProbability") for x in rows]
public_flags = [x.get("isPublic") for x in rows]

print("HTTP", r.status_code)
print("ROWS", len(rows))
print("STATUSES", sorted(set(statuses)))
print("ROWS WITH ID", sum(bool(x) for x in ids))
print("UNIQUE IDS", len(set(x for x in ids if x)))
print("ROWS WITH createdTs", sum(bool(x) for x in created))
print("ROWS WITH duration", sum(x is not None for x in durations))
print("ROWS WITH participants", sum(x is not None for x in participants))
print("ROWS WITH probability", sum(x is not None for x in probabilities))
print("ROWS WITH mergeProbability", sum(x is not None for x in merge_probabilities))
print("ROWS WITH isPublic", sum(x is not None for x in public_flags))
print("ID fingerprints", [hashlib.sha256(x.encode()).hexdigest()[:10] for x in ids if x])
print("STATUS sequence", statuses)
