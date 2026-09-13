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

headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}

r = requests.get(BASE + PATH + "?" + QUERY, headers=headers, timeout=30)
d = r.json()
rows = d.get("list", [])

details = [row.get("orderDetails") or {} for row in rows]
members = [m for row in rows for m in (row.get("members") or []) if isinstance(m, dict)]

detail_reward_flags = [bool(x.get("isReward")) for x in details]
member_reward_amounts = [float(m.get("rewardAmount") or 0) for m in members]
member_reward_records = [reward for m in members for reward in (m.get("rewards") or [])]
member_claimed_flags = [bool(m.get("claimed")) for m in members]

meta_keys = sorted(set(k for m in members for k in ((m.get("meta") or {}).keys() if isinstance(m.get("meta"), dict) else [])))

print("HTTP", r.status_code)
print("COMPLETED packages", len(rows))
print("orderDetails isReward TRUE", sum(detail_reward_flags))
print("orderDetails isReward FALSE", len(detail_reward_flags) - sum(detail_reward_flags))
print("members", len(members))
print("members rewardAmount > 0", sum(x > 0 for x in member_reward_amounts))
print("reward records", len(member_reward_records))
print("members claimed TRUE", sum(member_claimed_flags))
print("members claimed FALSE", len(member_claimed_flags) - sum(member_claimed_flags))
print("member meta keys", meta_keys)
print("package isReward flags", detail_reward_flags)
