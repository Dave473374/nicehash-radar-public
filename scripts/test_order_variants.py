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

members = first.get("members") or []
member = members[0] if members else {}

reward_amount = member.get("rewardAmount")
rewards = member.get("rewards")

print("HTTP", r.status_code)
print("COMPLETED rows", len(rows))

print("rewardAmount type:", type(reward_amount).__name__)

print("rewards type:", type(rewards).__name__)
print("rewards count:", len(rewards) if isinstance(rewards, list) else 0)
print("rewards item type:", type(rewards[0]).__name__ if isinstance(rewards, list) and rewards else "none")
print("rewards item keys:", sorted(rewards[0].keys()) if isinstance(rewards, list) and rewards and isinstance(rewards[0], dict) else [])

order_details = first.get("orderDetails") or {}

print("packagePrice type:", type(order_details.get("packagePrice")).__name__)
print("payedAmount type:", type(order_details.get("payedAmount")).__name__)
print("soloMiningCoin type:", type(order_details.get("soloMiningCoin")).__name__)
print("soloMiningSharesMaxPercent type:", type(order_details.get("soloMiningSharesMaxPercent")).__name__)
