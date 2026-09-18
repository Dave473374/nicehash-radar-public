import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

import requests

HOST = "https://adminapi.nicehash.com"
PATH = "/main/api/v2/admin/orders"

API_KEY = os.environ["NICEHASH_API_KEY"]
API_SECRET = os.environ["NICEHASH_API_SECRET"]
ORG_ID = os.environ["NICEHASH_ORG_ID"]

PAGE_SIZE = int(os.getenv("GLOBAL_ORDER_PAGE_SIZE", "100"))
MAX_PAGES = int(os.getenv("GLOBAL_ORDER_MAX_PAGES", "20"))
OUTPUT = Path(
    os.getenv(
        "GLOBAL_ORDERS_PATH",
        "/tmp/nicehash-global-completed-orders.json",
    )
)


def sign_request(method, path, query):
    x_time = str(int(time.time() * 1000))
    x_nonce = str(uuid.uuid4())

    message = bytearray(API_KEY, "utf-8")
    message += b"\x00"
    message += bytearray(x_time, "utf-8")
    message += b"\x00"
    message += bytearray(x_nonce, "utf-8")
    message += b"\x00"
    message += b"\x00"
    message += bytearray(ORG_ID, "utf-8")
    message += b"\x00"
    message += b"\x00"
    message += bytearray(method, "utf-8")
    message += b"\x00"
    message += bytearray(path, "utf-8")
    message += b"\x00"
    message += bytearray(query, "utf-8")

    digest = hmac.new(
        bytearray(API_SECRET, "utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    return {
        "X-Time": x_time,
        "X-Nonce": x_nonce,
        "X-Auth": API_KEY + ":" + digest,
        "X-Organization-Id": ORG_ID,
        "X-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def extract_rows(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("list", "orders", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    data = payload.get("data")
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("list", "orders", "content"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    return []


def first_value(row, *keys):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def nested_value(row, parent, child):
    value = row.get(parent)
    if isinstance(value, dict):
        return value.get(child)
    return None


def sanitize_order(row):
    is_reward = row.get("isReward")
    if not isinstance(is_reward, bool):
        is_reward = row.get("hadReward")
        if not isinstance(is_reward, bool):
            is_reward = None

    reward_count = row.get("rewardCount")
    if not isinstance(reward_count, int):
        reward_count = None

    package_name = first_value(
        row,
        "packageName",
        "package_name",
        "soloMiningPackageName",
    )
    if not package_name:
        package_name = nested_value(row, "package", "name")

    currency_market = first_value(
        row,
        "currencyMarket",
        "currency_market",
    )
    if not currency_market:
        currency_market = nested_value(row, "package", "currencyMarket")

    return {
        "startTs": first_value(
            row,
            "startTs",
            "orderStartTs",
            "createdTs",
        ),
        "endTs": first_value(
            row,
            "endTs",
            "orderEndTs",
            "completedTs",
        ),
        "packageName": package_name,
        "currencyMarket": currency_market,
        "payedAmount": first_value(
            row,
            "payedAmount",
            "amountSpent",
        ),
        "packagePrice": first_value(
            row,
            "packagePrice",
            "price",
        ),
        "soloMiningCoin": first_value(
            row,
            "soloMiningCoin",
            "coin",
            "primaryCoin",
        ),
        "soloMiningMergeCoin": first_value(
            row,
            "soloMiningMergeCoin",
            "mergeCoin",
        ),
        "isReward": is_reward,
        "rewardCount": reward_count,
    }


orders = []
timestamp_limit = str(int(time.time() * 1000))

for page in range(MAX_PAGES):
    query = (
        f"timestamp={timestamp_limit}"
        "&op=LE"
        f"&size={PAGE_SIZE}"
        f"&page={page}"
        "&status=COMPLETED"
        "&type=FIXED"
    )

    headers = sign_request("GET", PATH, query)
    response = requests.get(
        HOST + PATH + "?" + query,
        headers=headers,
        timeout=30,
    )

    if response.status_code != 200:
        print("GLOBAL ORDERS FETCH FAILED")
        print("HTTP status:", response.status_code)
        print("Page:", page)
        raise SystemExit(1)

    try:
        payload = response.json()
    except ValueError:
        print("GLOBAL ORDERS FETCH FAILED")
        print("Response was not valid JSON")
        print("Page:", page)
        raise SystemExit(1)

    rows = extract_rows(payload)
    print(f"Page {page}: {len(rows)} completed orders")

    if not rows:
        break

    orders.extend(
        sanitize_order(row)
        for row in rows
        if isinstance(row, dict)
    )

    if len(rows) < PAGE_SIZE:
        break

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    json.dumps(
        {
            "source": "SANITIZED_GLOBAL_COMPLETED_EASYMINING_ORDERS",
            "rawAdminRowsStored": False,
            "fieldsPolicy": (
                "Only minimal order timing/package/market/HIT-MISS fields are "
                "retained in the temporary file. Customer/account identifiers "
                "and raw admin responses are never written."
            ),
            "list": orders,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

print("GLOBAL COMPLETED ORDERS COLLECTOR")
print("Pages requested:", min(MAX_PAGES, page + 1))
print("Sanitized orders written:", len(orders))
print("Output:", OUTPUT)
print("Raw admin response persisted: NO")
