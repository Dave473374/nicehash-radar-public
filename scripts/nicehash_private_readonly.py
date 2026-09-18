import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import urlencode

import requests

HOST = "https://api2.nicehash.com"
ALLOWED_METHOD = "GET"
ALLOWED_PATHS = {
    "/hashpower/api/v2/hashpower/solo/order": {
        "allowed_query_keys": {"active", "page", "limit"},
        "kind": "private_completed_easymining",
    },
    "/hashpower/api/v2/hashpower/solo/shared/order": {
        "allowed_query_keys": {
            "page",
            "limit",
            "sortDir",
            "sortField",
            "onlyGold",
        },
        "kind": "shared_easymining",
    },
}


class PrivateApiPolicyError(RuntimeError):
    pass


def _credentials():
    return (
        os.environ["NICEHASH_API_KEY"],
        os.environ["NICEHASH_API_SECRET"],
        os.environ["NICEHASH_ORG_ID"],
    )


def _validate_request(method, path, query_params):
    if method != ALLOWED_METHOD:
        raise PrivateApiPolicyError(
            f"Private API method is forbidden: {method}"
        )

    policy = ALLOWED_PATHS.get(path)
    if policy is None:
        raise PrivateApiPolicyError(
            f"Private API path is not allowlisted: {path}"
        )

    keys = set(query_params)
    allowed = policy["allowed_query_keys"]
    extra = keys - allowed
    if extra:
        raise PrivateApiPolicyError(
            f"Private API query keys are not allowlisted: {sorted(extra)}"
        )

    if policy.get("kind") == "private_completed_easymining":
        if str(query_params.get("active", "")).lower() != "false":
            raise PrivateApiPolicyError(
                "Only completed EasyMining history (active=false) is allowed"
            )

    if policy.get("kind") == "shared_easymining":
        if str(query_params.get("sortField", "")) != "createdTs":
            raise PrivateApiPolicyError(
                "Shared EasyMining sortField must be createdTs"
            )
        if str(query_params.get("sortDir", "")).upper() not in {"ASC", "DESC"}:
            raise PrivateApiPolicyError(
                "Shared EasyMining sortDir must be ASC or DESC"
            )
        if str(query_params.get("onlyGold", "")).lower() not in {"true", "false"}:
            raise PrivateApiPolicyError(
                "Shared EasyMining onlyGold must be boolean text"
            )

    try:
        page = int(query_params.get("page", 0))
        limit = int(query_params.get("limit", 100))
    except (TypeError, ValueError) as exc:
        raise PrivateApiPolicyError(
            "page and limit must be integers"
        ) from exc

    if page < 0:
        raise PrivateApiPolicyError("page must be >= 0")
    if limit < 1 or limit > 100:
        raise PrivateApiPolicyError("limit must be between 1 and 100")


def _signed_headers(method, path, query):
    api_key, api_secret, org_id = _credentials()

    x_time = str(int(time.time() * 1000))
    x_nonce = str(uuid.uuid4())

    message = bytearray(api_key, "utf-8")
    message += b"\x00"
    message += bytearray(x_time, "utf-8")
    message += b"\x00"
    message += bytearray(x_nonce, "utf-8")
    message += b"\x00"
    message += b"\x00"
    message += bytearray(org_id, "utf-8")
    message += b"\x00"
    message += b"\x00"
    message += bytearray(method, "utf-8")
    message += b"\x00"
    message += bytearray(path, "utf-8")
    message += b"\x00"
    message += bytearray(query, "utf-8")

    digest = hmac.new(
        bytearray(api_secret, "utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    return {
        "X-Time": x_time,
        "X-Nonce": x_nonce,
        "X-Auth": api_key + ":" + digest,
        "X-Organization-Id": org_id,
        "X-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "NiceHash-Radar-Private-Readonly/1.0",
    }


def get_completed_easymining_orders(page=0, limit=100):
    path = "/hashpower/api/v2/hashpower/solo/order"
    params = {
        "active": "false",
        "page": int(page),
        "limit": int(limit),
    }

    _validate_request("GET", path, params)

    query = urlencode(params)
    headers = _signed_headers("GET", path, query)

    response = requests.get(
        HOST + path + "?" + query,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected private EasyMining response type")

    rows = payload.get("list")
    if not isinstance(rows, list):
        rows = []

    return rows


def get_shared_easymining_orders(
    page=0,
    limit=100,
    sort_dir="ASC",
    sort_field="createdTs",
    only_gold=False,
):
    path = "/hashpower/api/v2/hashpower/solo/shared/order"
    params = {
        "page": int(page),
        "limit": int(limit),
        "sortDir": str(sort_dir).upper(),
        "sortField": str(sort_field),
        "onlyGold": "true" if only_gold else "false",
    }

    _validate_request("GET", path, params)

    query = urlencode(params)
    headers = _signed_headers("GET", path, query)

    response = requests.get(
        HOST + path + "?" + query,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected shared EasyMining response type")

    rows = payload.get("list")
    return rows if isinstance(rows, list) else []


def policy_summary():
    return {
        "host": HOST,
        "method": ALLOWED_METHOD,
        "paths": sorted(ALLOWED_PATHS),
        "writesAllowed": False,
        "adminAllowed": False,
    }
