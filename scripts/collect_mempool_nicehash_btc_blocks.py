"""Collect public BTC blocks attributed to the NiceHash mining pool by mempool.space.

Research-only, public GET requests, no credentials. Pool attribution is kept
separate from EasyMining product attribution. Coinbase payout addresses are
collected from the public block transaction when available.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.request

BASE = "https://mempool.space"
POOL_PATH = "/api/v1/mining/pool/nicehash/blocks"
DEFAULT_OUTPUT = Path("research/mempool-nicehash-btc-blocks.jsonl")
MAX_RESPONSE_BYTES = 4_000_000
PAGE_SIZE = 10


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("mempool.space redirect refused")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _get_json(path: str, opener=None):
    if not path.startswith("/"):
        raise ValueError("Path must be absolute")
    url = BASE + path
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "nicehash-radar-public/nicehash-pool-research-1.0",
            "Accept": "application/json",
        },
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected mempool.space response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("mempool.space response too large")
    return json.loads(raw.decode("utf-8"))


def normalize_block_page(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("blocks", "data", "list", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("Unexpected mining-pool block response")


def fetch_block_page(cursor_height=None, opener=None):
    path = POOL_PATH
    if cursor_height is not None:
        cursor_height = int(cursor_height)
        if cursor_height < 1:
            raise ValueError("cursor_height must be positive")
        path += f"/{cursor_height}"
    return normalize_block_page(_get_json(path, opener=opener))


def _finite_nonnegative(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def parse_coinbase_outputs(payload):
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("Coinbase transaction response missing")
    tx = payload[0]
    vin = tx.get("vin")
    if not isinstance(vin, list) or not vin or not isinstance(vin[0], dict):
        raise ValueError("Coinbase input missing")
    # mempool.space marks the first transaction as coinbase. Accept either the
    # explicit flag or the coinbase scriptsig shape, but fail on ordinary txs.
    first_in = vin[0]
    if first_in.get("is_coinbase") is not True and first_in.get("prevout") is not None:
        raise ValueError("First transaction is not coinbase")

    outputs = []
    addresses = []
    for item in tx.get("vout") or []:
        if not isinstance(item, dict):
            continue
        address = item.get("scriptpubkey_address")
        value = item.get("value")
        row = {
            "address": str(address) if isinstance(address, str) and address else None,
            "valueSats": int(value) if _finite_nonnegative(value) else None,
            "scriptType": item.get("scriptpubkey_type"),
        }
        outputs.append(row)
        if row["address"] and row["address"] not in addresses:
            addresses.append(row["address"])
    return {
        "coinbaseTxid": tx.get("txid"),
        "coinbaseOutputs": outputs,
        "coinbaseOutputAddresses": addresses,
    }


def fetch_coinbase_outputs(block_hash: str, opener=None):
    block_hash = str(block_hash or "").lower().strip()
    if len(block_hash) != 64 or any(c not in "0123456789abcdef" for c in block_hash):
        raise ValueError("Invalid BTC block hash")
    payload = _get_json(f"/api/block/{block_hash}/txs/0", opener=opener)
    return parse_coinbase_outputs(payload)


def _block_hash(row):
    return str(row.get("id") or row.get("hash") or row.get("blockHash") or "").lower().strip()


def _block_height(row):
    raw = row.get("height")
    if raw is None:
        raw = row.get("blockHeight")
    try:
        height = int(raw)
    except (TypeError, ValueError):
        return None
    return height if height > 0 else None


def event_id(height, block_hash):
    basis = f"MEMPOOL_SPACE_NICEHASH_POOL|BTC|{height}|{block_hash}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def to_record(block, collected_at):
    height = _block_height(block)
    block_hash = _block_hash(block)
    if height is None:
        raise ValueError("NiceHash pool block missing height")
    if len(block_hash) != 64 or any(c not in "0123456789abcdef" for c in block_hash):
        raise ValueError("NiceHash pool block missing valid hash")
    extras = block.get("extras") if isinstance(block.get("extras"), dict) else {}
    return {
        "schemaVersion": 1,
        "eventId": event_id(height, block_hash),
        "firstSeenAt": collected_at,
        "coin": "BTC",
        "blockHeight": height,
        "blockHash": block_hash,
        "timestamp": block.get("timestamp"),
        "rewardSats": extras.get("reward"),
        "feesSats": extras.get("totalFees"),
        "source": "MEMPOOL_SPACE_NICEHASH_POOL",
        "sourceRole": "NICEHASH_POOL_BLOCKS_NOT_PRODUCT_ATTRIBUTION",
        "poolSlug": "nicehash",
        "credentialsUsed": False,
        "privateApiUsed": False,
        "adminApiUsed": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
        "coinbaseFetchStatus": "PENDING",
        "coinbaseOutputAddresses": [],
        "coinbaseOutputs": [],
    }


def load_existing(path: Path):
    rows = {}
    by_height = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        try:
            height = int(row.get("blockHeight"))
        except (TypeError, ValueError):
            continue
        block_hash = str(row.get("blockHash") or "").lower().strip()
        if height in by_height and by_height[height] != block_hash:
            raise ValueError(f"Conflicting BTC block hash at height {height}")
        by_height[height] = block_hash
        rows[(height, block_hash)] = row
    return rows


def collect(
    output: Path,
    max_pages: int,
    coinbase_limit: int,
    page_fetcher=fetch_block_page,
    coinbase_fetcher=fetch_coinbase_outputs,
    now=None,
):
    if max_pages < 1 or max_pages > 50:
        raise ValueError("max_pages must be 1..50")
    if coinbase_limit < 0 or coinbase_limit > 500:
        raise ValueError("coinbase_limit must be 0..500")
    now = now or utc_now()
    existing = load_existing(output)
    by_height = {height: block_hash for height, block_hash in existing}
    fingerprints = set()
    cursor = None
    pages_checked = 0
    added = 0
    stop_reason = "MAX_PAGES"

    for _ in range(max_pages):
        blocks = list(page_fetcher(cursor))
        pages_checked += 1
        if not blocks:
            stop_reason = "EMPTY_PAGE"
            break
        page_ids = []
        page_heights = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            height = _block_height(block)
            block_hash = _block_hash(block)
            if height is None or len(block_hash) != 64:
                continue
            page_ids.append((height, block_hash))
            page_heights.append(height)
        fingerprint = tuple(page_ids)
        if not fingerprint:
            raise ValueError("Mining-pool page contained no valid BTC blocks")
        if fingerprint in fingerprints:
            stop_reason = "REPEATED_PAGE"
            break
        fingerprints.add(fingerprint)

        for block in blocks:
            if not isinstance(block, dict):
                continue
            try:
                record = to_record(block, now)
            except ValueError:
                continue
            height = record["blockHeight"]
            block_hash = record["blockHash"]
            previous_hash = by_height.get(height)
            if previous_hash and previous_hash != block_hash:
                raise ValueError(f"Conflicting BTC block hash at height {height}")
            key = (height, block_hash)
            if key in existing:
                old = existing[key]
                record["firstSeenAt"] = old.get("firstSeenAt") or now
                for field in (
                    "coinbaseFetchStatus",
                    "coinbaseFetchedAt",
                    "coinbaseFetchErrorType",
                    "coinbaseTxid",
                    "coinbaseOutputs",
                    "coinbaseOutputAddresses",
                ):
                    if field in old:
                        record[field] = old.get(field)
            else:
                added += 1
            existing[key] = record
            by_height[height] = block_hash

        if len(page_ids) < PAGE_SIZE:
            stop_reason = "PARTIAL_PAGE"
            break
        cursor = min(page_heights) - 1
        if cursor < 1:
            stop_reason = "CHAIN_START"
            break

    enriched = 0
    # Newest-first so fresh blocks get their payout outputs first. Older
    # pending rows are gradually backfilled on later scheduled runs.
    for key in sorted(existing, reverse=True):
        if enriched >= coinbase_limit:
            break
        row = existing[key]
        if row.get("coinbaseFetchStatus") == "OK" and isinstance(row.get("coinbaseOutputAddresses"), list):
            continue
        try:
            details = coinbase_fetcher(row["blockHash"])
            row.update(details)
            row["coinbaseFetchStatus"] = "OK"
            row["coinbaseFetchedAt"] = now
            row.pop("coinbaseFetchErrorType", None)
        except Exception as exc:
            row["coinbaseFetchStatus"] = "UNAVAILABLE"
            row["coinbaseFetchErrorType"] = type(exc).__name__
        enriched += 1

    rows = sorted(
        existing.values(),
        key=lambda r: (int(r.get("blockHeight") or 0), str(r.get("blockHash") or "")),
        reverse=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "pagesChecked": pages_checked,
        "recordsStored": len(rows),
        "recordsAdded": added,
        "coinbaseRowsAttempted": enriched,
        "coinbaseRowsOk": sum(1 for row in rows if row.get("coinbaseFetchStatus") == "OK"),
        "stopReason": stop_reason,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--coinbase-limit", type=int, default=30)
    args = parser.parse_args()
    result = collect(args.output, args.max_pages, args.coinbase_limit)
    print("MEMPOOL NICEHASH BTC COLLECTION COMPLETE; public GET only; product attribution not inferred")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
