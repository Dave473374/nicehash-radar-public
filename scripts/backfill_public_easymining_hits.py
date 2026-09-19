"""Backfill public NiceHash EasyMining success events without credentials.

This dataset is success-event context only. It cannot provide a MISS denominator and
must never be used by itself to estimate ticket hit-rate or raise a BUY signal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api2.nicehash.com"
PATH = "/hashpower/api/v2/public/solo/singleReward"
DEFAULT_OUTPUT = Path("research/public-easymining-hit-history.jsonl")
MAX_RESPONSE_BYTES = 4_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Public NiceHash redirect refused")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_page(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("list", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("Unexpected singleReward response structure")


def fetch_page(page: int, limit: int, opener=None):
    if page < 0 or limit < 1 or limit > 100:
        raise ValueError("Invalid page or limit")
    query = urllib.parse.urlencode({"limit": limit, "page": page})
    url = BASE + PATH + "?" + query
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "nicehash-radar-public-hit-history/1.0", "Accept": "application/json"},
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected public NiceHash response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Public NiceHash response too large")
    return normalise_page(json.loads(raw.decode("utf-8")))


def event_id(record: dict) -> str:
    coin = str(record.get("coin") or "").upper()
    height = str(record.get("blockHeight") or "")
    package_id = str(record.get("packageId") or "")
    block_hash = str(record.get("blockHash") or "")
    basis = "|".join((coin, height, package_id, block_hash))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def source_key(record: dict):
    return (
        str(record.get("coin") or "").upper(),
        str(record.get("blockHeight") or ""),
        str(record.get("packageId") or ""),
        str(record.get("blockHash") or ""),
    )


def canonical_source(item: dict, first_seen: str) -> dict:
    record = {
        "coin": item.get("coin"),
        "blockHeight": item.get("blockHeight"),
        "blockHash": item.get("blockHash"),
        "payoutReward": item.get("payoutReward"),
        "payoutRewardBtc": item.get("payoutRewardBtc"),
        "time": item.get("time"),
        "createdTs": item.get("createdTs"),
        "packageId": item.get("packageId"),
        "packageName": item.get("packageName"),
        "shared": item.get("shared"),
    }
    record.update(
        schemaVersion=1,
        eventId=event_id(record),
        firstSeenAt=first_seen,
        source="NICEHASH_PUBLIC_SINGLE_REWARD",
        sourceRole="SUCCESS_EVENTS_ONLY_NO_MISS_DENOMINATOR",
        credentialsUsed=False,
        privateApiUsed=False,
        adminApiUsed=False,
        canRaiseBuySignal=False,
        canSupplyMissDenominator=False,
    )
    return record


def load_existing(path: Path):
    by_key = {}
    if not path.exists():
        return by_key
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        key = source_key(row)
        if key in by_key and by_key[key] != row:
            raise ValueError(f"Conflicting existing event for key {key}")
        by_key[key] = row
    return by_key


def collect(max_pages: int, limit: int, output: Path, fetcher=fetch_page, now=None):
    if max_pages < 1 or max_pages > 100:
        raise ValueError("max_pages must be 1..100")
    now = now or utc_now()
    existing = load_existing(output)
    page_fingerprints = set()
    added = 0
    pages_checked = 0
    stop_reason = "MAX_PAGES"

    for page in range(max_pages):
        rows = fetcher(page, limit)
        pages_checked += 1
        if not rows:
            stop_reason = "EMPTY_PAGE"
            break
        fingerprint = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if fingerprint in page_fingerprints:
            stop_reason = "REPEATED_PAGE"
            break
        page_fingerprints.add(fingerprint)

        for item in rows:
            if not isinstance(item, dict) or item.get("coin") is None or item.get("blockHeight") is None:
                continue
            key = source_key(item)
            candidate = canonical_source(item, now)
            if key in existing:
                old = existing[key]
                candidate["firstSeenAt"] = old.get("firstSeenAt") or now
                immutable = ("coin", "blockHeight", "blockHash", "packageId", "packageName")
                if any(old.get(k) != candidate.get(k) for k in immutable):
                    raise ValueError(f"Conflicting public event revision for key {key}")
                existing[key] = candidate
            else:
                existing[key] = candidate
                added += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        existing.values(),
        key=lambda r: (str(r.get("time") or ""), str(r.get("coin") or ""), str(r.get("blockHeight") or "")),
        reverse=True,
    )
    output.write_text(
        "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "pagesChecked": pages_checked,
        "recordsStored": len(rows),
        "recordsAdded": added,
        "stopReason": stop_reason,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = collect(args.max_pages, args.limit, args.output)
    print("PUBLIC EASYMINING HIT BACKFILL OK; success-events only; no credentials")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
