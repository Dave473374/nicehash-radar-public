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

SHARED_PATH = "/hashpower/api/v2/hashpower/solo/shared/order"
SHARED_QUERY = "status=COMPLETED&page=0&limit=100&sortDir=ASC&sortField=createdTs&onlyGold=false"

ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", SHARED_PATH, SHARED_QUERY]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
shared_r = requests.get(BASE + SHARED_PATH + "?" + SHARED_QUERY, headers=headers, timeout=30)
shared_rows = shared_r.json().get("list", [])

OWN_PATH = "/hashpower/api/v2/hashpower/solo/order"
OWN_QUERY = "active=false&page=0&limit=100"

ts = str(int(time.time() * 1000))
nonce = str(uuid.uuid4())
reqid = str(uuid.uuid4())
msg = "\x00".join([KEY, ts, nonce, "", ORG, "", "GET", OWN_PATH, OWN_QUERY]).encode()
sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
headers = {"X-Time": ts, "X-Nonce": nonce, "X-Auth": KEY + ":" + sig, "X-Organization-Id": ORG, "X-Request-Id": reqid}
own_r = requests.get(BASE + OWN_PATH + "?" + OWN_QUERY, headers=headers, timeout=30)
own_rows = own_r.json().get("list", [])

shared_ticket_ids = [str((x.get("orderDetails") or {}).get("soloTicketId") or "") for x in shared_rows]
own_ticket_ids = [str(x.get("soloTicketId") or "") for x in own_rows]

valid_shared_ids = [x for x in shared_ticket_ids if x]
valid_own_ids = [x for x in own_ticket_ids if x]

matches = [x for x in valid_shared_ids if x in valid_own_ids]

shared_coins = [str((x.get("orderDetails") or {}).get("soloMiningCoin") or "") for x in shared_rows]
own_coins = [str(x.get("soloMiningCoin") or "") for x in own_rows]

shared_starts = [str((x.get("orderDetails") or {}).get("startTs") or "") for x in shared_rows]
own_starts = [str(x.get("startTs") or "") for x in own_rows]

start_matches = [x for x in shared_starts if x and x in own_starts]

print("SHARED HTTP", shared_r.status_code)
print("OWN HTTP", own_r.status_code)
print("SHARED completed rows", len(shared_rows))
print("OWN completed rows", len(own_rows))
print("SHARED rows with soloTicketId", len(valid_shared_ids))
print("OWN rows with soloTicketId", len(valid_own_ids))
print("EXACT soloTicketId matches", len(matches))
print("START timestamp matches", len(start_matches))
print("SHARED coins", sorted(set(shared_coins)))
print("OWN coins", sorted(set(own_coins)))
