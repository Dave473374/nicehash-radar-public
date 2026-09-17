import os
import time
import uuid
import hmac
import hashlib
import requests

HOST = "https://adminapi.nicehash.com"

API_KEY = os.environ["NICEHASH_API_KEY"]
API_SECRET = os.environ["NICEHASH_API_SECRET"]
ORG_ID = os.environ["NICEHASH_ORG_ID"]

METHOD = "GET"
PATH = "/main/api/v2/admin/orders"

# Testiramo samo prvo stran in zahtevamo samo 1 zapis.
# Ne izpisujemo nobenih podatkov o orderjih.
timestamp = str(int(time.time() * 1000))
QUERY = (
    f"timestamp={timestamp}"
    "&op=LE"
    "&size=1"
    "&page=0"
    "&status=COMPLETED"
    "&type=FIXED"
)

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
    hashlib.sha256,
).hexdigest()

headers = {
    "X-Time": XTIMESTAMP,
    "X-Nonce": XNONCE,
    "X-Auth": API_KEY + ":" + digest,
    "X-Organization-Id": ORG_ID,
    "X-Request-Id": str(uuid.uuid4()),
    "Content-Type": "application/json",
}

url = HOST + PATH + "?" + QUERY

try:
    response = requests.get(url, headers=headers, timeout=30)

    print("ADMIN ORDERS ACCESS TEST")
    print("HTTP status:", response.status_code)

    if response.status_code == 200:
        print("RESULT: ACCESS OK")
        print("Read-only API credentials reached the admin orders endpoint.")
    elif response.status_code in (401, 403):
        print("RESULT: ACCESS DENIED")
        print("The existing read-only API credentials cannot access this admin endpoint.")
    else:
        print("RESULT: OTHER RESPONSE")
        print("No response body was printed for security.")

except requests.RequestException as exc:
    print("RESULT: REQUEST FAILED")
    print("Error type:", type(exc).__name__)
