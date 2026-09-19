"""Verify public EasyMining success events against independent chain data.

Research/audit only. It never changes CURRENT, BATCH, alerts, orders, or
hit-rate denominators.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_INPUT = Path("research/public-easymining-hit-history.jsonl")
DEFAULT_LEDGER = Path("research/onchain-hit-verification.jsonl")
DEFAULT_REPORT = Path("research/onchain-hit-report.json")
MAX_RESPONSE_BYTES = 4_000_000

MEMPOOL_BASES = {"BTC": "https://mempool.space", "BCH": "https://bchexplorer.cash"}
ZEC_BASE = "https://api.blockchair.com"
KAS_BASE = "https://api.kaspa.org"
SUPPORTED = {"BTC", "BCH", "ZEC", "KAS"}
KNOWN_TAGS = (
    (b"/NiceHashMining/", "NICEHASH_MINING_TAG"),
    (b"/NiceHashSolo/", "NICEHASH_SOLO_TAG"),
    (b"/NiceHash/", "NICEHASH_TAG"),
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("On-chain explorer redirect refused")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def get(opener, base, path, json_expected=True):
    if base not in set(MEMPOOL_BASES.values()) | {ZEC_BASE, KAS_BASE}:
        raise ValueError("Explorer host not allowlisted")
    if not path.startswith("/"):
        raise ValueError("Explorer path invalid")
    url = base + path
    request = urllib.request.Request(
        url, method="GET",
        headers={"User-Agent": "nicehash-radar-onchain-audit/2.0", "Accept": "application/json,text/plain"},
    )
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected explorer response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Explorer response too large")
    text = raw.decode("utf-8").strip()
    return json.loads(text) if json_expected else text


def classify_tag(script_hex):
    try:
        raw = bytes.fromhex(str(script_hex or ""))
    except ValueError:
        return "INVALID_COINBASE_SCRIPTSIG", None
    lower = raw.lower()
    for needle, label in KNOWN_TAGS:
        if needle.lower() in lower:
            return label, needle.decode("ascii")
    return "UNKNOWN_TAG", None


def source_event_id(event):
    return str(event.get("eventId") or "").strip() or "|".join(
        str(event.get(k) or "") for k in ("coin", "blockHeight", "packageId", "blockHash")
    )


def parse_source_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            return None
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def common(event, now):
    return {
        "schemaVersion": 2,
        "eventId": source_event_id(event),
        "verifiedAt": now,
        "coin": str(event.get("coin") or "").upper() or None,
        "blockHeight": event.get("blockHeight"),
        "packageName": event.get("packageName"),
        "packageId": event.get("packageId"),
        "niceHashBlockHash": event.get("blockHash"),
        "niceHashPayoutReward": event.get("payoutReward"),
        "source": "PUBLIC_ONCHAIN_EXPLORER",
        "credentialsUsed": False,
        "privateApiUsed": False,
        "adminApiUsed": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
    }


def verify_mempool(event, opener, now):
    coin = str(event.get("coin") or "").upper()
    base = MEMPOOL_BASES[coin]
    result = common(event, now)
    height = strict_uint(event.get("blockHeight"))
    if height is None or hash256(event.get("blockHash")) is None:
        return {**result, "status": "INVALID_BLOCK_IDENTITY", "explorer": None}
    block_hash = get(opener, base, f"/api/block-height/{height}", False).strip()
    if hash256(block_hash) is None:
        raise ValueError("Invalid block hash")
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) == 64 and expected != block_hash.lower():
        return {**result, "status": "CONFLICT_BLOCK_HASH", "explorer": base, "onchainBlockHash": block_hash}

    block = get(opener, base, f"/api/block/{block_hash}")
    txs = get(opener, base, f"/api/block/{block_hash}/txs/0")
    if not isinstance(block, dict) or not isinstance(txs, list) or not txs:
        raise ValueError("Unexpected mempool-style schema")
    if strict_uint(block.get("height")) != height or hash256(block.get("id")) != block_hash.lower():
        return {**result, "status": "CONFLICT_BLOCK_METADATA", "explorer": base}
    tx0 = txs[0]
    if not isinstance(tx0, dict):
        raise ValueError("Coinbase transaction missing")
    vin, vout = tx0.get("vin"), tx0.get("vout")
    if not isinstance(vin, list) or not vin or not isinstance(vin[0], dict):
        raise ValueError("Coinbase input missing")
    if len(vin) != 1 or vin[0].get("is_coinbase") is not True:
        raise ValueError("First transaction is not an explicit coinbase")
    tag_class, tag = classify_tag(vin[0].get("scriptsig"))
    reward_sats = None
    if isinstance(vout, list) and vout and all(isinstance(x, dict) for x in vout):
        vals = [strict_uint(x.get("value")) for x in vout]
        if all(x is not None for x in vals):
            reward_sats = sum(vals)

    if tag_class == "NICEHASH_TAG":
        status = "VERIFIED_ON_CHAIN_NICEHASH_TAG"
        strength = "BLOCK_HASH_AND_NICEHASH_TAG"
    elif tag_class in {"NICEHASH_MINING_TAG", "NICEHASH_SOLO_TAG"}:
        status = "VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG"
        strength = "BLOCK_HASH_AND_OTHER_NICEHASH_TAG"
    else:
        status = "VERIFIED_ON_CHAIN_BLOCK_MATCH"
        strength = "BLOCK_HASH_ONLY_TAG_UNCONFIRMED"

    return {
        **result,
        "status": status,
        "verificationStrength": strength,
        "explorer": base,
        "onchainBlockHash": block_hash,
        "onchainTimestamp": block.get("timestamp"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseTagClass": tag_class,
        "coinbaseTag": tag,
        **reward_fields(event, reward_sats),
        "onchainBlockHeight": height,
        "personalOrderProven": False,
    }



def strict_uint(value):
    """Atomic amounts/heights: never truncate floats or infer units by size."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 2**63-1 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        number = int(value)
        return number if number <= 2**63-1 else None
    return None


def hash256(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) else None


def native_payout(event):
    # This contract is scoped to the normalized public singleReward BTC/BCH
    # stream. A decimal native-coin string or unknown provenance stays unknown.
    if (event.get("source") != "NICEHASH_PUBLIC_SINGLE_REWARD"
            or event.get("coin") not in {"BTC", "BCH"}):
        return None
    return strict_uint(event.get("payoutReward"))


def reward_fields(event, reward_sats):
    payout_sats = native_payout(event)
    valid_reward = strict_uint(reward_sats)
    ratio = (100.0 * payout_sats / valid_reward
             if payout_sats is not None and valid_reward not in (None, 0) else None)
    return {
        "coinbaseRewardAtomic": valid_reward,
        "coinbaseRewardNative": valid_reward / 100_000_000 if valid_reward is not None else None,
        "coinbaseRewardUnit": "SATOSHI_1E8_PER_COIN" if valid_reward is not None else "UNKNOWN",
        "niceHashPayoutAtomic": payout_sats,
        "niceHashPayoutNative": payout_sats / 100_000_000 if payout_sats is not None else None,
        "niceHashPayoutUnit": "PUBLIC_SINGLE_REWARD_ATOMIC_1E8" if payout_sats is not None else "UNKNOWN",
        "payoutToCoinbasePercent": round(ratio, 8) if ratio is not None else None,
        "payoutExceedsCoinbase": payout_sats > valid_reward if payout_sats is not None and valid_reward is not None else None,
        "payoutRatioIsFeeSchedule": False,
    }


def verify_bch_blockchair(event, opener, now):
    """One public documented block dashboard request; never use guessed_miner as tag proof."""
    result = common(event, now)
    height, expected = strict_uint(event.get("blockHeight")), hash256(event.get("blockHash"))
    if height is None or expected is None:
        return {**result, "status": "INVALID_BLOCK_IDENTITY", "explorer": None}
    path = f"/bitcoin-cash/dashboards/block/{height}?limit=1"
    payload = get(opener, ZEC_BASE, path)
    if not isinstance(payload, dict) or not isinstance(payload.get("context"), dict):
        raise ValueError("Blockchair context missing")
    if strict_uint(payload["context"].get("code")) != 200:
        raise ValueError("Blockchair context did not confirm success")
    data = payload.get("data")
    if not isinstance(data, dict) or set(data) != {str(height)}:
        raise ValueError("BCH requested height missing or ambiguous")
    entry = data[str(height)]
    block = entry.get("block") if isinstance(entry, dict) else None
    if not isinstance(block, dict):
        raise ValueError("BCH block object missing")
    on_hash, on_height = hash256(block.get("hash")), strict_uint(block.get("id"))
    if on_hash is None or on_height is None:
        raise ValueError("BCH explorer hash/height missing")
    if on_height != height or on_hash != expected:
        return {**result, "status": "CONFLICT_BLOCK_HASH" if on_hash != expected else "CONFLICT_BLOCK_HEIGHT",
                "explorer": ZEC_BASE, "onchainBlockHash": on_hash, "onchainBlockHeight": on_height}
    if block.get("is_orphan") is True or block.get("main_chain") is False:
        return {**result, "status": "CONFLICT_ORPHAN_BLOCK", "explorer": ZEC_BASE,
                "onchainBlockHash": on_hash, "onchainBlockHeight": on_height}
    # Blockchair specifies UTC for this time format. No browser/display-time inference.
    try:
        at = datetime.strptime(block["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        raise ValueError("BCH timestamp missing or invalid") from None
    difficulty = block.get("difficulty")
    difficulty = difficulty if finite(difficulty) and difficulty > 0 else None
    script = block.get("coinbase_data_hex")
    tag_class, tag = classify_tag(script)
    if tag_class == "NICEHASH_TAG":
        status, strength = "VERIFIED_ON_CHAIN_NICEHASH_TAG", "BLOCK_HEIGHT_HASH_AND_NICEHASH_TAG"
    elif tag_class in {"NICEHASH_SOLO_TAG", "NICEHASH_MINING_TAG"}:
        status, strength = "VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG", "BLOCK_HEIGHT_HASH_AND_OTHER_NICEHASH_TAG"
    else:
        status, strength = "VERIFIED_ON_CHAIN_BLOCK_MATCH", "BLOCK_HEIGHT_HASH_TAG_UNCONFIRMED"
    return {
        **result, "status": status, "verificationStrength": strength,
        "explorer": ZEC_BASE, "explorerPath": path, "onchainNetwork": "BCH_MAINNET",
        "onchainBlockHash": on_hash, "onchainBlockHeight": on_height,
        "onchainTimestamp": int(at.timestamp()), "onchainDifficulty": difficulty,
        "coinbaseTagClass": tag_class, "coinbaseTag": tag,
        "coinbaseTagEvidenceField": "coinbase_data_hex" if tag else None,
        # Preserve only a digest of the public coinbase script, not arbitrary text.
        "coinbaseScriptSha256": hashlib.sha256(bytes.fromhex(script)).hexdigest()
            if isinstance(script, str) and len(script) <= 2000 and re.fullmatch(r"(?:[0-9a-fA-F]{2})+", script) else None,
        **reward_fields(event, block.get("reward")),
        "verificationScope": "INDEPENDENT_EXPLORER_MATCH_NOT_FULL_NODE_CONSENSUS_VALIDATION",
        "packageAttributionSource": "NICEHASH_PUBLIC_SINGLE_REWARD",
        "personalOrderProven": False,
    }


def verify_bch(event, opener, now):
    """Bounded fallback for availability/schema errors, NEVER for an identity conflict."""
    if strict_uint(event.get("blockHeight")) is None or hash256(event.get("blockHash")) is None:
        return {**common(event, now), "status": "INVALID_BLOCK_IDENTITY", "explorer": None}
    attempts = []
    for base, verifier in ((ZEC_BASE, verify_bch_blockchair), (MEMPOOL_BASES["BCH"], verify_mempool)):
        try:
            result = verifier(event, opener, now)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, RuntimeError) as exc:
            attempts.append({"explorer": base, "status": "UNAVAILABLE_OR_INVALID_SCHEMA", "errorType": type(exc).__name__})
            continue
        attempts.append({"explorer": base, "status": result["status"]})
        result.update(bchVerifierVersion=1, providerAttempts=attempts, fallbackUsed=len(attempts) > 1)
        return result
    return {**common(event, now), "status": "SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH", "explorer": None,
            "bchVerifierVersion": 1, "providerAttempts": attempts, "fallbackUsed": True,
            "errorType": "AllBchProvidersUnavailable"}



def first_blockchair_block(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or not payload["data"]:
        raise ValueError("Unexpected Blockchair response")
    entry = next(iter(payload["data"].values()))
    if not isinstance(entry, dict):
        raise ValueError("Unexpected Blockchair block entry")
    block = entry.get("block")
    if not isinstance(block, dict):
        raise ValueError("Blockchair block missing")
    return block


def verify_zec(event, opener, now):
    result = common(event, now)
    height = int(event.get("blockHeight"))
    payload = get(opener, ZEC_BASE, f"/zcash/dashboards/block/{height}")
    block = first_blockchair_block(payload)
    block_hash = str(block.get("hash") or "")
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) == 64 and block_hash and expected != block_hash.lower():
        return {**result, "status": "CONFLICT_BLOCK_HASH", "explorer": ZEC_BASE, "onchainBlockHash": block_hash}
    if int(block.get("id") or block.get("height") or height) != height:
        raise ValueError("ZEC block height mismatch")
    return {
        **result,
        "status": "VERIFIED_ON_CHAIN_BLOCK_MATCH",
        "verificationStrength": "BLOCK_HEIGHT_HASH_INDEPENDENT_EXPLORER",
        "explorer": ZEC_BASE,
        "onchainBlockHash": block_hash or None,
        "onchainTimestamp": block.get("time") or block.get("date"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseRewardNative": block.get("reward"),
        "explorerMinerLabel": block.get("guessed_miner"),
        "coinbaseTagClass": "NOT_CHECKED_FOR_ZEC_PHASE1",
    }


def verify_kas(event, opener, now):
    result = common(event, now)
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) != 64:
        return {**result, "status": "INVALID_BLOCK_HASH", "explorer": KAS_BASE}
    path = f"/blocks/{expected}?includeTransactions=true&includeColor=false"
    block = get(opener, KAS_BASE, path)
    if not isinstance(block, dict):
        raise ValueError("Unexpected Kaspa block schema")
    verbose = block.get("verboseData") or {}
    header = block.get("header") or {}
    extra = block.get("extra") or {}
    onchain_hash = str(verbose.get("hash") or "").lower()
    if onchain_hash != expected:
        return {**result, "status": "CONFLICT_BLOCK_HASH", "explorer": KAS_BASE, "onchainBlockHash": onchain_hash or None}
    miner_info = extra.get("minerInfo")
    nicehash_label = isinstance(miner_info, str) and "nicehash" in miner_info.lower()
    status = "VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO" if nicehash_label else "VERIFIED_ON_CHAIN_BLOCK_MATCH"
    strength = "BLOCK_HASH_AND_NICEHASH_MINER_INFO" if nicehash_label else "BLOCK_HASH_INDEPENDENT_OFFICIAL_API"
    return {
        **result,
        "status": status,
        "verificationStrength": strength,
        "explorer": KAS_BASE,
        "onchainBlockHash": onchain_hash,
        "onchainTimestamp": header.get("timestamp"),
        "onchainDifficulty": verbose.get("difficulty"),
        "kaspaBlueScore": verbose.get("blueScore") or header.get("blueScore"),
        "kaspaDaaScore": header.get("daaScore"),
        "explorerMinerInfo": miner_info,
        "explorerMinerAddress": extra.get("minerAddress"),
        "coinbaseTagClass": "KASPA_MINER_INFO" if nicehash_label else "KASPA_MINER_INFO_UNCONFIRMED",
    }


def verify_event(event, opener=None, now=None):
    now = now or utc_now()
    coin = str(event.get("coin") or "").upper()
    result = common(event, now)
    if coin not in SUPPORTED:
        return {**result, "status": "UNSUPPORTED_COIN", "explorer": None}
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        if coin == "BCH":
            return verify_bch(event, opener, now)
        if coin in MEMPOOL_BASES:
            return verify_mempool(event, opener, now)
        if coin == "ZEC":
            return verify_zec(event, opener, now)
        if coin == "KAS":
            return verify_kas(event, opener, now)
    except Exception as exc:
        return {**result, "status": "SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH", "explorer": None, "errorType": type(exc).__name__}


def load_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def build(input_path, ledger_path, report_path, max_new, verifier=verify_event, now=None):
    if len({Path(x).resolve() for x in (input_path, ledger_path, report_path)}) != 3:
        raise ValueError("Source, ledger and report must be distinct files")
    if max_new < 1 or max_new > 100:
        raise ValueError("max_new must be 1..100")
    now = now or utc_now()
    events = load_jsonl(input_path)
    latest = {source_event_id(r): r for r in load_jsonl(ledger_path) if source_event_id(r)}
    processed = 0

    # Record unsupported event types without spending explorer budget.
    for event in events:
        eid = source_event_id(event)
        coin = str(event.get("coin") or "").upper()
        if coin not in SUPPORTED and eid not in latest:
            latest[eid] = verifier(event, now=now)

    # High-frequency KAS hits can dominate chronological history. Verify
    # supported chains round-robin so Bronze/Silver/Gold are not starved.
    queues = {coin: [] for coin in sorted(SUPPORTED)}
    for event in events:
        eid = source_event_id(event)
        coin = str(event.get("coin") or "").upper()
        if coin not in SUPPORTED:
            continue
        previous = latest.get(eid)
        if previous and previous.get("status") != "SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH":
            continue
        queues[coin].append(event)

    positions = {coin: 0 for coin in queues}
    while processed < max_new:
        progressed = False
        for coin in sorted(queues):
            pos = positions[coin]
            if pos >= len(queues[coin]) or processed >= max_new:
                continue
            event = queues[coin][pos]
            positions[coin] += 1
            eid = source_event_id(event)
            previous = latest.get(eid)
            current = verifier(event, now=now)
            if previous is not None:
                history = list(previous.get("verificationAttemptHistory") or [])
                summary = {key: previous.get(key) for key in ("verifiedAt", "status", "explorer", "errorType")}
                if summary not in history:
                    history.append(summary)
                current["verificationAttemptHistory"] = history
            latest[eid] = current
            processed += 1
            progressed = True
        if not progressed:
            break

    rows = sorted(
        latest.values(),
        key=lambda r: (str(r.get("coin") or ""), int(r.get("blockHeight") or 0), str(r.get("eventId") or "")),
        reverse=True,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        "".join(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    counts = Counter(str(r.get("status") or "UNKNOWN") for r in rows)
    by_coin = Counter(str(r.get("coin") or "UNKNOWN") for r in rows if str(r.get("status") or "").startswith("VERIFIED_ON_CHAIN"))
    verified = sum(v for k, v in counts.items() if k.startswith("VERIFIED_ON_CHAIN"))
    report = {
        "schemaVersion": 2,
        "generatedAt": now,
        "role": "ONCHAIN_HIT_VERIFICATION_RESEARCH_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
        "automaticPurchase": False,
        "successEventsOnly": True,
        "inputEventCount": len(events),
        "ledgerEventCount": len(rows),
        "processedThisRun": processed,
        "verifiedOnchainCount": verified,
        "verifiedByCoin": dict(by_coin),
        "statusCounts": dict(counts),
        "verificationHealth": "DEGRADED" if counts.get("SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH") else "COMPLETED_NO_SOURCE_ERRORS",
        "bchProviderOrder": [ZEC_BASE, MEMPOOL_BASES["BCH"]],
        "bchPayoutRatioIsFeeSchedule": False,
        "supportedCoins": sorted(SUPPORTED),
        "packageCoverageIntent": {
            "Gold": "BTC",
            "Silver": "BCH",
            "Bronze": "ZEC",
            "Titanium": "KAS",
        },
        "policy": "Independent chain audit only. Verified HITs are numerator/context evidence; no chain explorer supplies missing EasyMining tickets or a MISS denominator.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-new", type=int, default=20)
    args = parser.parse_args()
    report = build(args.input, args.ledger, args.report, args.max_new)
    print("ONCHAIN HIT VERIFICATION COMPLETE; research-only; no account access")
    print(json.dumps({k: report[k] for k in ("inputEventCount","ledgerEventCount","processedThisRun","verifiedOnchainCount","verifiedByCoin","statusCounts")}, sort_keys=True))


if __name__ == "__main__":
    main()
