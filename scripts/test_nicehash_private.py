import os
import time
import uuid
import hmac
import hashlib
import requests

HOST = "https://api2.nicehash.com"
API_KEY = os.environ["NICEHASH_API_KEY"]
API_SECRET = os.environ["NICEHASH_API_SECRET"]
ORG_ID = os.environ["NICEHASH_ORG_ID"]

METHOD = "GET"
PATH = "/hashpower/api/v2/hashpower/solo/shared/order"
QUERY = "page=0&limit=10&sortDir=ASC&sortField=createdTs&onlyGold=false"

XTIMESTAMP = str(int(time.time() * 1000))
XNONCE = str(uuid.uuid4())

message = bytearray(API_KEY, "utf-8")
message += b"\x00"
message += bytearray(XTIMESTAMP, "utf-8")
message += b"\x00"
message += bytearray(XNONCE, "utf-8")
message += b"\x00"
message += b"\x00"
message += bytearray(ORG_ID, "utf-8")
message += b"\x00"
message += b"\x00"
message += bytearray(METHOD, "utf-8")
message += b"\x00"
message += bytearray(PATH, "utf-8")
message += b"\x00"
message += bytearray(QUERY, "utf-8")

digest = hmac.new(
bytearray(API_SECRET, "utf-8"),
message,
hashlib.sha256
).hexdigest()

headers = {
"X-Time": XTIMESTAMP,
"X-Nonce": XNONCE,
"X-Auth": API_KEY + ":" + digest,
"X-Organization-Id": ORG_ID,
"X-Request-Id": str(uuid.uuid4()),
"Content-Type": "application/json"
}

url = HOST + PATH + "?" + QUERY
response = requests.get(url, headers=headers, timeout=30)

print("HTTP status:", response.status_code)
data = response.json()
rows = data.get("list", [])
print("Number of rows:", len(rows))
print("ROW 1:", rows[0].get("status"), rows[0].get("createdTs"), rows[0].get("fullAmount"), rows[0].get("numberOfParticipants"))
print("ROW 2:", rows[1].get("status"), rows[1].get("createdTs"), rows[1].get("fullAmount"), rows[1].get("numberOfParticipants"))
print("ROW 3:", rows[2].get("status"), rows[2].get("createdTs"), rows[2].get("fullAmount"), rows[2].get("numberOfParticipants"))
print("ROW 4:", rows[3].get("status"), rows[3].get("createdTs"), rows[3].get("fullAmount"), rows[3].get("numberOfParticipants"))
