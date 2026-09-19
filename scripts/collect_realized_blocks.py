"""Collect public NiceHash EasyMining realized blocks (success events only).

GET-only public endpoint. No credentials. This file is numerator/context evidence
and never supplies a MISS denominator.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api2.nicehash.com"
PATH = "/hashpower/api/v2/public/solo/singleReward"
DEFAULT_OUTPUT = Path("calibration/realized-blocks.jsonl")
MAX_RESPONSE_BYTES = 4_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Public NiceHash redirect refused")


def utc_now():
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
        headers={"User-Agent": "nicehash-radar-public/2.0", "Accept": "application/json"},
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected public NiceHash response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Public NiceHash response too large")
    return normalise_page(json.loads(raw.decode("utf-8")))


def identity(row):
    return (
        str(row.get("coin") or "").upper(),
        str(row.get("blockHeight") or ""),
        str(row.get("packageId") or ""),
        str(row.get("blockHash") or ""),
    )


def to_record(block, collected_at):
    return {
        "collected_at": collected_at,
        "coin": block.get("coin"),
        "blockHeight": block.get("blockHeight"),
        "blockHash": block.get("blockHash"),
        "payoutReward": block.get("payoutReward"),
        "payoutRewardBtc": block.get("payoutRewardBtc"),
        "time": block.get("time"),
        "createdTs": block.get("createdTs"),
        "packageId": block.get("packageId"),
        "packageName": block.get("packageName"),
        "shared": block.get("shared"),
    }


def load_existing(path):
    rows = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        key = identity(row)
        if key in rows and rows[key] != row:
            raise ValueError(f"Conflicting realized-block record: {key}")
        rows[key] = row
    return rows


def collect(output: Path, pages: int, limit: int, fetcher=fetch_page, now=None):
    if pages < 1 or pages > 100:
        raise ValueError("pages must be 1..100")
    now = now or utc_now()
    existing = load_existing(output)
    fingerprints = set()
    added = 0
    pages_checked = 0
    stop_reason = "MAX_PAGES"

    for page in range(pages):
        blocks = fetcher(page, limit)
        pages_checked += 1
        if not blocks:
            stop_reason = "EMPTY_PAGE"
            break
        fp = hashlib.sha256(
            json.dumps(blocks, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        if fp in fingerprints:
            stop_reason = "REPEATED_PAGE"
            break
        fingerprints.add(fp)

        for block in blocks:
            if not isinstance(block, dict) or block.get("coin") is None or block.get("blockHeight") is None:
                continue
            record = to_record(block, now)
            key = identity(record)
            if key in existing:
                old = existing[key]
                for field in ("coin", "blockHeight", "blockHash", "packageId", "packageName"):
                    if old.get(field) != record.get(field):
                        raise ValueError(f"Conflicting public success event: {key}")
                # Preserve first collection time; refresh mutable payout/context fields.
                record["collected_at"] = old.get("collected_at") or now
            else:
                added += 1
            existing[key] = record

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        existing.values(),
        key=lambda r: (str(r.get("time") or ""), str(r.get("coin") or ""), str(r.get("blockHeight") or "")),
        reverse=True,
    )
    output.write_text(
        "".join(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n" for r in rows),
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
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = collect(args.output, args.pages, args.limit)
    print("PUBLIC REALIZED BLOCKS OK; success-events only; no credentials")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
