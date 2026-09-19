"""Research-only on-chain verification for public NiceHash EasyMining HITs.

Reads public NiceHash singleReward history, confirms that the referenced block
exists on-chain through a public explorer, and writes verification evidence.
It never creates a MISS denominator, never changes BUY logic and never uses
NiceHash credentials/Admin access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import urllib.error
import urllib.request

INPUT = Path("calibration/realized-blocks.jsonl")
OUTPUT = Path("calibration/onchain-block-verifications.jsonl")
REPORT = Path("research/onchain-verification-report.json")
BASE = "https://api.blockchair.com"
SATOSHI = 100_000_000
MAX_NEW = 50
SUPPORTED = {
    "BTC": "bitcoin",
    "BCH": "bitcoin-cash",
    "LTC": "litecoin",
    "DOGE": "dogecoin",
    "ZEC": "zcash",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Explorer redirect refused")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def positive_float(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def as_int(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def load_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def record_id(row):
    coin = str(row.get("coin") or "").upper()
    height = as_int(row.get("blockHeight"))
    package = str(row.get("packageName") or "UNKNOWN")
    package_id = str(row.get("packageId") or "UNKNOWN")
    if not coin or height is None:
        return None
    return f"{coin}:{height}:{package}:{package_id}"


def project_source(row):
    return {
        "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
        "coin": str(row.get("coin") or "").upper(),
        "packageName": row.get("packageName"),
        "packageId": row.get("packageId"),
        "blockHeight": as_int(row.get("blockHeight")),
        "blockHash": row.get("blockHash"),
        "payoutReward": row.get("payoutReward"),
        "payoutRewardBtc": row.get("payoutRewardBtc"),
        "time": row.get("time"),
        "createdTs": row.get("createdTs"),
        "shared": row.get("shared"),
    }


def unique_sources(rows):
    unique = {}
    conflicts = set()
    for row in rows:
        identity = record_id(row)
        if identity is None or identity in conflicts:
            continue
        projected = project_source(row)
        if identity in unique and canonical(unique[identity]) != canonical(projected):
            unique.pop(identity, None)
            conflicts.add(identity)
        else:
            unique[identity] = projected
    return unique, conflicts


def fetch_blockchair(chain: str, height: int):
    if chain not in SUPPORTED.values():
        raise ValueError("Unsupported explorer chain")
    url = f"{BASE}/{chain}/dashboards/block/{height}?limit=1"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "NiceHash-Radar-Onchain/1.0", "Accept": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected explorer response")
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Explorer response too large")
    payload = json.loads(raw.decode("utf-8"))
    item = (payload.get("data") or {}).get(str(height)) if isinstance(payload, dict) else None
    block = item.get("block") if isinstance(item, dict) else None
    if not isinstance(block, dict):
        return {"found": False, "provider": "BLOCKCHAIR", "chain": chain}
    txs = item.get("transactions") if isinstance(item.get("transactions"), list) else []
    return {"found": True, "provider": "BLOCKCHAIR", "chain": chain, "block": block, "firstTransactionHash": txs[0] if txs else None}


def explorer_urls(coin, height, block_hash):
    chain = SUPPORTED.get(coin)
    urls = {"blockchair": f"https://blockchair.com/{chain}/block/{height}"} if chain else {}
    if coin == "BTC":
        urls["mempool"] = f"https://mempool.space/block/{block_hash or height}"
    if coin == "BCH":
        urls["blockchainCom"] = f"https://www.blockchain.com/explorer/blocks/bch/{height}"
    return urls


def reward_native(block):
    for key in ("reward", "generation", "subsidy"):
        raw = positive_float(block.get(key))
        if raw is not None:
            return raw / SATOSHI, raw, key
    return None, None, None


def nicehash_tag(block):
    evidence = []
    for key, value in block.items():
        name = str(key).lower()
        if "coinbase" not in name and "pool" not in name and "miner" not in name:
            continue
        text = str(value)
        if "nicehash" in text.lower():
            evidence.append({"field": key, "value": text[:200]})
    return ("OBSERVED", evidence) if evidence else ("NOT_AVAILABLE_IN_BLOCK_DASHBOARD", [])


def build_verification(source, fetched, collected_at):
    coin = str(source.get("coin") or "").upper()
    height = as_int(source.get("blockHeight"))
    chain = SUPPORTED.get(coin)
    identity = record_id(source)
    if identity is None or height is None or chain is None:
        return None

    base = {
        "schemaVersion": 1,
        "id": identity,
        "collected_at": collected_at,
        "role": "ONCHAIN_HIT_VERIFICATION_ONLY",
        "canSupplyMissDenominator": False,
        "canRaiseBuySignal": False,
        "automaticPurchase": False,
        "privateApiUsed": False,
        "adminApiUsed": False,
        "niceHashSource": source,
        "explorerUrls": explorer_urls(coin, height, source.get("blockHash")),
    }
    if not fetched.get("found"):
        return {**base, "provider": fetched.get("provider"), "chain": chain, "status": "NOT_FOUND_ONCHAIN"}

    block = fetched.get("block") or {}
    on_height = as_int(block.get("id") or block.get("height"))
    on_hash = str(block.get("hash") or "") or None
    nh_hash = str(source.get("blockHash") or "") or None
    height_matches = on_height == height
    hash_matches = None if not (nh_hash and on_hash) else nh_hash.lower() == on_hash.lower()
    status = "VERIFIED_ONCHAIN" if height_matches and hash_matches is not False else "HASH_OR_HEIGHT_MISMATCH"
    if status == "VERIFIED_ONCHAIN" and hash_matches is None:
        status = "VERIFIED_HEIGHT_ONLY"

    reward, reward_raw, reward_field = reward_native(block)
    payout = positive_float(source.get("payoutReward"))
    payout_ratio = round(100 * payout / reward, 6) if payout is not None and reward is not None else None
    tag_status, tag_evidence = nicehash_tag(block)

    return {
        **base,
        "status": status,
        "provider": fetched.get("provider"),
        "chain": chain,
        "confidence": "HEIGHT_AND_HASH" if hash_matches is True else "HEIGHT_ONLY" if height_matches else "MISMATCH",
        "onchain": {
            "height": on_height,
            "hash": on_hash,
            "time": block.get("time"),
            "difficulty": block.get("difficulty"),
            "transactionCount": block.get("transaction_count") or block.get("transactionCount"),
            "firstTransactionHash": fetched.get("firstTransactionHash"),
            "rewardNative": reward,
            "rewardRaw": reward_raw,
            "rewardField": reward_field,
            "nicehashTagStatus": tag_status,
            "nicehashTagEvidence": tag_evidence,
        },
        "checks": {
            "blockExists": True,
            "heightMatches": height_matches,
            "hashMatches": hash_matches,
            "payoutVsBlockRewardPercent": payout_ratio,
        },
        "limitations": [
            "This verifies a public NiceHash HIT block, not a personal user HIT unless matched to a completed order.",
            "Public HITs do not provide a MISS denominator.",
            "Coinbase tag evidence may be unavailable from this provider.",
        ],
    }


def conflict_id(identity, collected_at):
    return identity + ":CONFLICT:" + str(collected_at).replace(":", "").replace("+", "")[-24:]


def sort_key(row):
    src = row.get("niceHashSource") or {}
    return (str(src.get("coin")), int(src.get("blockHeight") or 0), str(row.get("id")))


def merge(existing, new_records):
    out = {row.get("id"): row for row in existing if isinstance(row.get("id"), str)}
    for row in new_records:
        identity = row.get("id")
        if not isinstance(identity, str):
            continue
        if identity in out and canonical(out[identity]) != canonical(row):
            conflict = dict(row)
            conflict["id"] = conflict_id(identity, row.get("collected_at"))
            conflict["status"] = "CONFLICT_WITH_EXISTING_VERIFICATION"
            out[conflict["id"]] = conflict
        else:
            out[identity] = row
    return sorted(out.values(), key=sort_key)


def summarize(records, skipped, pending, generated_at):
    status_counts = Counter(row.get("status") for row in records)
    coin_counts = Counter((row.get("niceHashSource") or {}).get("coin") for row in records)
    package_counts = Counter((row.get("niceHashSource") or {}).get("packageName") for row in records)
    ratios = [row.get("checks", {}).get("payoutVsBlockRewardPercent") for row in records]
    ratios = [x for x in ratios if isinstance(x, (int, float))]
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "role": "ONCHAIN_HIT_VERIFICATION_REPORT_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseBuySignal": False,
        "automaticPurchase": False,
        "privateApiUsed": False,
        "adminApiUsed": False,
        "verifiedRecordCount": len(records),
        "pendingCandidateCount": pending,
        "skipped": dict(skipped),
        "statusCounts": dict(status_counts),
        "coinCounts": dict(coin_counts),
        "packageCounts": dict(package_counts),
        "payoutVsBlockRewardPercent": {
            "samples": len(ratios),
            "min": min(ratios) if ratios else None,
            "max": max(ratios) if ratios else None,
            "average": round(sum(ratios) / len(ratios), 6) if ratios else None,
        },
        "policy": {
            "publicHitsAreNotMissDenominator": True,
            "personalHitRequiresCompletedOrderMatch": True,
            "usesNiceHashCredentials": False,
            "usesAdmin": False,
        },
    }


def run(input_path: Path, existing_path: Path, fetcher, limit: int, generated_at: str):
    sources, conflicts = unique_sources(load_jsonl(input_path))
    existing = load_jsonl(existing_path)
    existing_ids = {row.get("id") for row in existing if isinstance(row.get("id"), str)}
    skipped = Counter({"inputConflicts": len(conflicts)})
    new_records = []
    pending = 0
    for identity, source in sorted(sources.items()):
        if identity in existing_ids:
            continue
        coin = str(source.get("coin") or "").upper()
        height = as_int(source.get("blockHeight"))
        chain = SUPPORTED.get(coin)
        if chain is None or height is None:
            skipped["unsupportedOrInvalid"] += 1
            continue
        if len(new_records) >= limit:
            pending += 1
            continue
        try:
            fetched = fetcher(chain, height)
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError):
            skipped["temporaryFetchError"] += 1
            continue
        verification = build_verification(source, fetched, generated_at)
        if verification:
            new_records.append(verification)
    merged = merge(existing, new_records)
    return merged, summarize(merged, skipped, pending, generated_at)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--limit", type=int, default=MAX_NEW)
    args = parser.parse_args()
    generated_at = utc_now()
    records, report = run(args.input, args.output, fetch_blockchair, args.limit, generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(canonical(row) + "\n" for row in records), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("ONCHAIN HIT VERIFICATION OK")
    print("Verified records:", report["verifiedRecordCount"])
    print("Statuses:", report["statusCounts"])
    print("Skipped:", report["skipped"])


if __name__ == "__main__":
    main()
