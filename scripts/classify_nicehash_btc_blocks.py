"""Classify public NiceHash BTC pool blocks against EasyMining source evidence.

Research-only. Exact height+hash presence in the public NiceHash singleReward
archive confirms EasyMining. Absence from that archive never proves NON-EasyMining.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

DEFAULT_POOL = Path("research/mempool-nicehash-btc-blocks.jsonl")
DEFAULT_EASY = Path("research/public-easymining-hit-history.jsonl")
DEFAULT_OUTPUT = Path("research/nicehash-btc-attribution.jsonl")
DEFAULT_REPORT = Path("research/nicehash-btc-attribution-report.json")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _height(row):
    try:
        value = int(row.get("blockHeight"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _hash(row):
    value = str(row.get("blockHash") or "").lower().strip()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        return None
    return value


def easy_index(rows):
    by_height = {}
    for row in rows:
        if str(row.get("coin") or "").upper() != "BTC":
            continue
        if row.get("source") != "NICEHASH_PUBLIC_SINGLE_REWARD":
            continue
        height = _height(row)
        block_hash = _hash(row)
        if height is None or block_hash is None:
            continue
        by_height.setdefault(height, []).append(row)
    return by_height


def classify_row(pool_row, easy_by_height, generated_at):
    height = _height(pool_row)
    block_hash = _hash(pool_row)
    if height is None or block_hash is None:
        raise ValueError("Pool record missing valid BTC height/hash")
    candidates = easy_by_height.get(height, [])
    exact = [row for row in candidates if _hash(row) == block_hash]

    if len(exact) > 1:
        # Multiple identical source rows are not harmful, but disagreeing
        # package identity at one exact block is ambiguous and must fail closed.
        package_keys = {
            (str(row.get("packageId") or ""), str(row.get("packageName") or ""))
            for row in exact
        }
        if len(package_keys) > 1:
            attribution = "CONFLICTING_EVIDENCE"
            basis = "MULTIPLE_EASYMINING_PACKAGE_IDENTITIES_FOR_EXACT_BLOCK"
            easy = None
        else:
            attribution = "EASYMINING_CONFIRMED"
            basis = "NICEHASH_PUBLIC_SINGLE_REWARD_EXACT_HEIGHT_HASH"
            easy = exact[0]
    elif len(exact) == 1:
        attribution = "EASYMINING_CONFIRMED"
        basis = "NICEHASH_PUBLIC_SINGLE_REWARD_EXACT_HEIGHT_HASH"
        easy = exact[0]
    elif candidates:
        attribution = "CONFLICTING_EVIDENCE"
        basis = "EASYMINING_HEIGHT_PRESENT_BUT_BLOCK_HASH_DIFFERS"
        easy = None
    else:
        attribution = "NICEHASH_UNKNOWN"
        basis = "MEMPOOL_NICEHASH_POOL_WITHOUT_EASYMINING_SOURCE_MATCH"
        easy = None

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "eventId": pool_row.get("eventId"),
        "coin": "BTC",
        "blockHeight": height,
        "blockHash": block_hash,
        "timestamp": pool_row.get("timestamp"),
        "source": "MEMPOOL_SPACE_NICEHASH_POOL",
        "niceHashPoolAttribution": "CONFIRMED_BY_MEMPOOL_SPACE",
        "easyMiningAttribution": attribution,
        "easyMiningAttributionBasis": basis,
        "easyMiningEventId": easy.get("eventId") if easy else None,
        "packageId": easy.get("packageId") if easy else None,
        "packageName": easy.get("packageName") if easy else None,
        "coinbaseFetchStatus": pool_row.get("coinbaseFetchStatus"),
        "coinbaseOutputAddresses": pool_row.get("coinbaseOutputAddresses") or [],
        "coinbaseOutputs": pool_row.get("coinbaseOutputs") or [],
        "absenceFromEasyMiningArchiveDoesNotProveNonEasy": True,
        "needsExplicitNonEasySourceEvidence": attribution == "NICEHASH_UNKNOWN",
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
    }


def _address_stats(rows):
    stats = {}
    for row in rows:
        attribution = str(row.get("easyMiningAttribution") or "UNKNOWN")
        addresses = row.get("coinbaseOutputAddresses") or []
        if not isinstance(addresses, list):
            continue
        for address in dict.fromkeys(str(a) for a in addresses if isinstance(a, str) and a):
            item = stats.setdefault(address, Counter())
            item["totalBlocks"] += 1
            item[attribution] += 1
    result = []
    for address, counts in stats.items():
        result.append({
            "address": address,
            "totalBlocks": counts["totalBlocks"],
            "easyMiningConfirmedBlocks": counts["EASYMINING_CONFIRMED"],
            "niceHashUnknownBlocks": counts["NICEHASH_UNKNOWN"],
            "conflictingEvidenceBlocks": counts["CONFLICTING_EVIDENCE"],
        })
    return sorted(
        result,
        key=lambda item: (-item["totalBlocks"], item["address"]),
    )


def build(pool_input: Path, easy_input: Path, output: Path, report_path: Path, now=None):
    now = now or utc_now()
    pool_rows = load_jsonl(pool_input)
    easy_rows = load_jsonl(easy_input)
    index = easy_index(easy_rows)

    rows = []
    seen = set()
    for pool_row in pool_rows:
        if str(pool_row.get("coin") or "").upper() != "BTC":
            continue
        height = _height(pool_row)
        block_hash = _hash(pool_row)
        if height is None or block_hash is None:
            continue
        key = (height, block_hash)
        if key in seen:
            continue
        seen.add(key)
        rows.append(classify_row(pool_row, index, now))

    rows.sort(key=lambda row: row["blockHeight"], reverse=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    counts = Counter(row["easyMiningAttribution"] for row in rows)
    addresses = _address_stats(rows)
    report = {
        "schemaVersion": 1,
        "generatedAt": now,
        "role": "NICEHASH_BTC_PRODUCT_ATTRIBUTION_RESEARCH_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
        "automaticPurchase": False,
        "niceHashPoolBlockCount": len(rows),
        "easyMiningConfirmedCount": counts["EASYMINING_CONFIRMED"],
        "niceHashUnknownCount": counts["NICEHASH_UNKNOWN"],
        "conflictingEvidenceCount": counts["CONFLICTING_EVIDENCE"],
        "nonEasyConfirmedCount": 0,
        "attributionCounts": dict(counts),
        "coinbaseAddressStats": addresses[:50],
        "definitions": {
            "EASYMINING_CONFIRMED": "Exact BTC block height+hash appears in NiceHash public singleReward with package identity.",
            "NICEHASH_UNKNOWN": "mempool.space attributes the BTC block to NiceHash, but no exact EasyMining source match is present.",
            "CONFLICTING_EVIDENCE": "Source evidence conflicts at the same BTC block height or exact block identity.",
            "NON_EASY_CONFIRMED": "Reserved for explicit product-level NON-EasyMining evidence; absence from singleReward is never enough.",
        },
        "policy": (
            "NiceHash pool attribution is not EasyMining attribution. Coinbase payout addresses are recorded as public "
            "on-chain context only and are not hard-coded as product classifiers. A NiceHash-only block remains "
            "NICEHASH_UNKNOWN until explicit product-level evidence exists."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-input", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--easy-input", type=Path, default=DEFAULT_EASY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = build(args.pool_input, args.easy_input, args.output, args.report)
    print("NICEHASH BTC ATTRIBUTION COMPLETE; research-only; absence is not NON-Easy proof")
    print(json.dumps({
        k: report[k] for k in (
            "niceHashPoolBlockCount",
            "easyMiningConfirmedCount",
            "niceHashUnknownCount",
            "conflictingEvidenceCount",
            "nonEasyConfirmedCount",
        )
    }, sort_keys=True))


if __name__ == "__main__":
    main()
