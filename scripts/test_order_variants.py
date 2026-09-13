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

members = [
m
for row in rows
for m in (row.get("members") or [])
if isinstance(m, dict)
]

reward_amounts = [
float(m.get("rewardAmount") or 0)
for m in members
]

reward_members = [
m
for m in members
if float(m.get("rewardAmount") or 0) > 0
]

reward_records = [
reward
for m in members
for reward in (m.get("rewards") or [])
]

packages_with_reward = [
row
for row in rows
if any(
float(m.get("rewardAmount") or 0) > 0
for m in (row.get("members") or [])
if isinstance(m, dict)
)
]

rewarding_members_per_package = [
sum(
1
for m in (row.get("members") or [])
if isinstance(m, dict)
and float(m.get("rewardAmount") or 0) > 0
)
for row in rows
]

print("HTTP", r.status_code)
print("COMPLETED packages", len(rows))
print("TOTAL members", len(members))
print("PACKAGES with rewardAmount > 0", len(packages_with_reward))
print("PACKAGES without reward", len(rows) - len(packages_with_reward))
print("MEMBERS with rewardAmount > 0", len(reward_members))
print("TOTAL rewards records", len(reward_records))
print("MIN rewardAmount", min(reward_amounts) if reward_amounts else 0)
print("MAX rewardAmount", max(reward_amounts) if reward_amounts else 0)
print("REWARDING members per package", rewarding_members_per_package)
