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
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_INPUT = Path("research/public-easymining-hit-history.jsonl")
DEFAULT_LEDGER = Path("research/onchain-hit-verification.jsonl")
DEFAULT_REPORT = Path("research/onchain-hit-report.json")
MAX_RESPONSE_BYTES = 4_000_000

MEMPOOL_BASES = {"BTC": "https://mempool.space", "BCH": "https://bchexplorer.cash"}
BLOCKCHAIR_BASE = "https://api.blockchair.com"
BCH_NINJA_BASE = "https://explorer.bch.ninja"
ORDNET_BASE = "https://api.ordnet.io/v1"
ZEC_BASE = BLOCKCHAIR_BASE
KAS_BASE = "https://api.kaspa.org"
BLOCKCHAIR_CHAIN_SLUGS = {"LTC": "litecoin", "DOGE": "dogecoin"}
ORDNET_CHAIN_SLUGS = {"LTC": "ltc", "DOGE": "doge"}
SUPPORTED = {"BTC", "BCH", "ZEC", "KAS", "LTC", "DOGE"}
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
    if base not in set(MEMPOOL_BASES.values()) | {BLOCKCHAIR_BASE, BCH_NINJA_BASE, KAS_BASE, ORDNET_BASE}:
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
        "schemaVersion": 3,
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
    height = int(event.get("blockHeight"))
    block_hash = get(opener, base, f"/api/block-height/{height}", False).strip()
    if len(block_hash) != 64:
        raise ValueError("Invalid block hash")
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) == 64 and expected != block_hash.lower():
        return {**result, "status": "CONFLICT_BLOCK_HASH", "explorer": base, "onchainBlockHash": block_hash}

    block = get(opener, base, f"/api/block/{block_hash}")
    txs = get(opener, base, f"/api/block/{block_hash}/txs/0")
    if not isinstance(block, dict) or not isinstance(txs, list) or not txs:
        raise ValueError("Unexpected mempool-style schema")
    tx0 = txs[0]
    vin, vout = tx0.get("vin"), tx0.get("vout")
    if not isinstance(vin, list) or not vin or not isinstance(vin[0], dict):
        raise ValueError("Coinbase input missing")
    tag_class, tag = classify_tag(vin[0].get("scriptsig"))
    reward_sats = None
    if isinstance(vout, list):
        vals = [x.get("value") for x in vout if isinstance(x, dict)]
        if vals and all(finite(x) for x in vals):
            reward_sats = sum(float(x) for x in vals)
    reward_native = reward_sats / 100_000_000 if reward_sats is not None else None
    payout_native = payout_native_from_public_reward(event)
    payout_ratio = None
    if payout_native is not None and reward_native and reward_native > 0:
        payout_ratio = payout_native / reward_native * 100

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
        "coinbaseRewardNative": round(reward_native, 12) if reward_native is not None else None,
        "niceHashPayoutRewardNative": round(payout_native, 12) if payout_native is not None else None,
        "payoutToCoinbasePercent": round(payout_ratio, 6) if payout_ratio is not None else None,
    }


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


def payout_native_from_public_reward(event):
    """Normalize public singleReward UTXO-chain payouts from 1e-8 native units."""
    raw = event.get("payoutReward")
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    coin = str(event.get("coin") or "").upper()
    if coin in {"BTC", "BCH", "ZEC", "LTC", "DOGE"}:
        return value / 100_000_000
    return value


def _easymining_source_evidence(event):
    """Strong product-level evidence from the public EasyMining success endpoint."""
    block_hash = str(event.get("blockHash") or "").strip()
    return bool(
        event.get("source") == "NICEHASH_PUBLIC_SINGLE_REWARD"
        and event.get("packageName")
        and event.get("packageId")
        and event.get("blockHeight") is not None
        and len(block_hash) == 64
    )


def _non_easy_source_evidence(event):
    """Explicit external evidence that NiceHash classified the reward as non-EasyMining."""
    return bool(
        event.get("sourceClassification") == "NON_EASY_ORDER"
        or event.get("source") == "NICEHASH_NON_EASY_ORDER"
        or event.get("nonEasyOrder") is True
    )


def _nicehash_chain_evidence(record):
    tag = str(record.get("coinbaseTagClass") or "")
    status = str(record.get("status") or "")
    return bool(
        tag in {
            "NICEHASH_TAG",
            "NICEHASH_MINING_TAG",
            "NICEHASH_SOLO_TAG",
            "BCH_NINJA_NICEHASH_MINER_LABEL",
            "BLOCKCHAIR_NICEHASH_MINER_LABEL",
            "KASPA_MINER_INFO",
        }
        or status in {
            "VERIFIED_ON_CHAIN_NICEHASH_TAG",
            "VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG",
            "VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO",
        }
    )


def apply_easymining_attribution(record, event):
    """Keep pool evidence separate from EasyMining product attribution."""
    out = dict(record)
    easy = _easymining_source_evidence(event)
    non_easy = _non_easy_source_evidence(event)
    chain_nicehash = _nicehash_chain_evidence(out)

    if easy and non_easy:
        attribution = "CONFLICTING_EVIDENCE"
        basis = "EASYMINING_SOURCE_AND_EXPLICIT_NON_EASY_SOURCE_CONFLICT"
    elif non_easy:
        attribution = "NON_EASY_CONFIRMED"
        basis = "EXPLICIT_NON_EASY_ORDER_SOURCE"
    elif easy:
        attribution = "EASYMINING_CONFIRMED"
        basis = "NICEHASH_PUBLIC_SINGLE_REWARD_PACKAGE_AND_BLOCK_IDENTITY"
    elif chain_nicehash:
        attribution = "NICEHASH_UNKNOWN"
        basis = "NICEHASH_POOL_EVIDENCE_WITHOUT_PRODUCT_SOURCE"
    else:
        attribution = "UNKNOWN"
        basis = "NO_PRODUCT_LEVEL_ATTRIBUTION_EVIDENCE"

    out["easyMiningAttribution"] = attribution
    out["easyMiningAttributionBasis"] = basis
    out["niceHashPoolAttribution"] = "CONFIRMED" if chain_nicehash else "NOT_CONFIRMED"
    out["niceHashTagDoesNotProveEasyMining"] = True
    return out


def refresh_payout_metrics(record, event):
    """Normalize public reward units and repair legacy payout/reward ratios locally."""
    out = dict(record)
    payout_native = payout_native_from_public_reward(event)
    reward_native = out.get("coinbaseRewardNative")
    try:
        reward_native = float(reward_native)
    except (TypeError, ValueError, OverflowError):
        reward_native = None
    if reward_native is not None and not math.isfinite(reward_native):
        reward_native = None

    if payout_native is not None:
        out["niceHashPayoutRewardNative"] = round(payout_native, 12)
    if payout_native is not None and reward_native and reward_native > 0:
        out["payoutToCoinbasePercent"] = round(payout_native / reward_native * 100, 6)
    return out


def verify_bch_blockchair(event, opener, now, primary_error=None):
    """Fallback when the BCH mempool-style explorer is unavailable.

    Blockchair confirms height/hash/timestamp/difficulty/reward but this path
    deliberately does not claim a NiceHash coinbase tag.
    """
    result = common(event, now)
    height = int(event.get("blockHeight"))
    payload = get(opener, BLOCKCHAIR_BASE, f"/bitcoin-cash/dashboards/block/{height}")
    block = first_blockchair_block(payload)
    block_hash = str(block.get("hash") or "").lower().strip()
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) == 64 and block_hash and expected != block_hash:
        return {
            **result,
            "status": "CONFLICT_BLOCK_HASH",
            "explorer": BLOCKCHAIR_BASE,
            "fallbackFrom": MEMPOOL_BASES["BCH"],
            "primaryErrorType": primary_error,
            "onchainBlockHash": block_hash,
        }
    on_height = int(block.get("id") or block.get("height") or height)
    if on_height != height:
        raise ValueError("BCH fallback block height mismatch")

    reward_raw = block.get("reward")
    reward_native = None
    if finite(reward_raw):
        reward_native = float(reward_raw) / 100_000_000

    payout_native = payout_native_from_public_reward(event)
    payout_ratio = None
    if payout_native is not None and reward_native and reward_native > 0:
        payout_ratio = payout_native / reward_native * 100

    return {
        **result,
        "schemaVersion": 3,
        "status": "VERIFIED_ON_CHAIN_BLOCK_MATCH",
        "verificationStrength": "BLOCK_HEIGHT_HASH_INDEPENDENT_EXPLORER_FALLBACK",
        "explorer": BLOCKCHAIR_BASE,
        "fallbackFrom": MEMPOOL_BASES["BCH"],
        "primaryErrorType": primary_error,
        "onchainBlockHash": block_hash or None,
        "onchainTimestamp": block.get("time") or block.get("date"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseRewardRaw": reward_raw,
        "coinbaseRewardNative": round(reward_native, 12) if reward_native is not None else None,
        "niceHashPayoutRewardNative": round(payout_native, 12) if payout_native is not None else None,
        "payoutToCoinbasePercent": round(payout_ratio, 6) if payout_ratio is not None else None,
        "explorerMinerLabel": block.get("guessed_miner"),
        "coinbaseTagClass": "NOT_CHECKED_BCH_BLOCKCHAIR_FALLBACK",
    }


def _unique_height_hash_record(payload, height, source_name):
    """Find exactly one recursive height/hash block object in public JSON."""
    matches = []

    def walk(value, depth=0):
        if depth > 5:
            return
        if isinstance(value, dict):
            raw_height = value.get("height")
            if raw_height is None:
                raw_height = value.get("blockHeight")
            raw_hash = value.get("hash")
            if raw_hash is None:
                raw_hash = value.get("blockHash")
            try:
                parsed_height = int(raw_height)
            except (TypeError, ValueError):
                parsed_height = None
            block_hash = str(raw_hash or "").lower().strip()
            if parsed_height == height and len(block_hash) == 64:
                matches.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)

    walk(payload)
    unique = {}
    for item in matches:
        block_hash = str(item.get("hash") or item.get("blockHash") or "").lower().strip()
        unique.setdefault(block_hash, item)
    if len(unique) != 1:
        raise ValueError(f"{source_name} response did not contain exactly one matching height/hash")
    block_hash, item = next(iter(unique.items()))
    return block_hash, item


def _bch_ninja_block(payload, height):
    return _unique_height_hash_record(payload, height, "BCH Ninja")


def verify_bch_ninja_hash(event, opener, now, primary_error=None, blockchair_error=None):
    """Last-resort BCH verification using BCH Ninja's public JSON block route."""
    result = common(event, now)
    height = int(event.get("blockHeight"))
    payload = get(
        opener,
        BCH_NINJA_BASE,
        f"/api/blocks-by-height/{height}",
    )
    block_hash, block = _bch_ninja_block(payload, height)
    expected = str(event.get("blockHash") or "").lower().strip()
    if len(expected) == 64 and expected != block_hash:
        return {
            **result,
            "schemaVersion": 3,
            "status": "CONFLICT_BLOCK_HASH",
            "explorer": BCH_NINJA_BASE,
            "fallbackFrom": [MEMPOOL_BASES["BCH"], BLOCKCHAIR_BASE],
            "primaryErrorType": primary_error,
            "blockchairErrorType": blockchair_error,
            "onchainBlockHash": block_hash,
        }

    pool_info = block.get("poolInfo") if isinstance(block.get("poolInfo"), dict) else {}
    miner = (
        pool_info.get("poolName")
        or block.get("miner")
        or block.get("minerName")
    )
    nicehash_label = isinstance(miner, str) and "nicehash" in miner.lower()

    return {
        **result,
        "schemaVersion": 3,
        "status": (
            "VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO"
            if nicehash_label
            else "VERIFIED_ON_CHAIN_BLOCK_MATCH"
        ),
        "verificationStrength": (
            "BLOCK_HEIGHT_HASH_AND_NICEHASH_MINER_LABEL_SECOND_FALLBACK"
            if nicehash_label
            else "BLOCK_HEIGHT_HASH_PUBLIC_EXPLORER_SECOND_FALLBACK"
        ),
        "explorer": BCH_NINJA_BASE,
        "fallbackFrom": [MEMPOOL_BASES["BCH"], BLOCKCHAIR_BASE],
        "primaryErrorType": primary_error,
        "blockchairErrorType": blockchair_error,
        "onchainBlockHash": block_hash,
        "onchainTimestamp": block.get("time") or block.get("timestamp"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseRewardNative": None,
        "niceHashPayoutRewardNative": payout_native_from_public_reward(event),
        "payoutToCoinbasePercent": None,
        "explorerMinerLabel": miner,
        "coinbaseTagClass": (
            "BCH_NINJA_NICEHASH_MINER_LABEL"
            if nicehash_label
            else "NOT_CHECKED_BCH_NINJA_HASH_ONLY"
        ),
    }



def verify_palladium_chain(event, opener, now):
    """Verify the reported Palladium chain event without over-claiming its pair.

    Palladium uses Scrypt merged mining. A public LTC or DOGE success event is
    verified only against the chain named by that event. This function does not
    infer that the paired chain also produced a reward.
    """
    coin = str(event.get("coin") or "").upper()
    if coin not in BLOCKCHAIR_CHAIN_SLUGS:
        raise ValueError("Unsupported Palladium chain")
    result = common(event, now)
    height = int(event.get("blockHeight"))
    slug = BLOCKCHAIR_CHAIN_SLUGS[coin]
    payload = get(opener, BLOCKCHAIR_BASE, f"/{slug}/dashboards/block/{height}")
    block = first_blockchair_block(payload)
    block_hash = str(block.get("hash") or "").lower().strip()
    expected = str(event.get("blockHash") or "").lower().strip()
    chain_role = "AUXPOW_CHILD_CHAIN" if coin == "DOGE" else "PARENT_SCRYPT_CHAIN"

    if len(expected) == 64 and block_hash and expected != block_hash:
        return {
            **result,
            "schemaVersion": 3,
            "status": "CONFLICT_BLOCK_HASH",
            "explorer": BLOCKCHAIR_BASE,
            "onchainBlockHash": block_hash,
            "mergedMiningFamily": "Palladium",
            "mergedMiningChain": coin,
            "mergedMiningChainRole": chain_role,
            "mergedMiningEvidenceScope": "THIS_CHAIN_EVENT_ONLY",
            "pairedChainEvidenceClaimed": False,
        }

    on_height = int(block.get("id") or block.get("height") or height)
    if on_height != height:
        raise ValueError("Palladium chain block height mismatch")

    reward_raw = block.get("reward")
    reward_native = float(reward_raw) / 100_000_000 if finite(reward_raw) else None
    payout_native = payout_native_from_public_reward(event)
    payout_ratio = None
    if payout_native is not None and reward_native and reward_native > 0:
        payout_ratio = payout_native / reward_native * 100

    miner = block.get("guessed_miner")
    nicehash_label = isinstance(miner, str) and "nicehash" in miner.lower()
    return {
        **result,
        "schemaVersion": 3,
        "status": (
            "VERIFIED_ON_CHAIN_NICEHASH_MINER_INFO"
            if nicehash_label
            else "VERIFIED_ON_CHAIN_BLOCK_MATCH"
        ),
        "verificationStrength": (
            "BLOCK_HEIGHT_HASH_AND_NICEHASH_MINER_LABEL_PALLADIUM_CHAIN"
            if nicehash_label
            else "BLOCK_HEIGHT_HASH_PALLADIUM_CHAIN_EVENT"
        ),
        "explorer": BLOCKCHAIR_BASE,
        "onchainBlockHash": block_hash or None,
        "onchainTimestamp": block.get("time") or block.get("date"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseRewardRaw": reward_raw,
        "coinbaseRewardNative": round(reward_native, 12) if reward_native is not None else None,
        "niceHashPayoutRewardNative": round(payout_native, 12) if payout_native is not None else None,
        "payoutToCoinbasePercent": round(payout_ratio, 6) if payout_ratio is not None else None,
        "explorerMinerLabel": miner,
        "coinbaseTagClass": (
            "BLOCKCHAIR_NICEHASH_MINER_LABEL"
            if nicehash_label
            else "NOT_CHECKED_PALLADIUM_BLOCKCHAIR"
        ),
        "mergedMiningFamily": "Palladium",
        "mergedMiningChain": coin,
        "mergedMiningChainRole": chain_role,
        "mergedMiningEvidenceScope": "THIS_CHAIN_EVENT_ONLY",
        "pairedChainEvidenceClaimed": False,
    }


def verify_palladium_ordnet_hash(event, opener, now, primary_error=None):
    """Keyless hash-only fallback for a reported LTC/DOGE Palladium event."""
    coin = str(event.get("coin") or "").upper()
    if coin not in ORDNET_CHAIN_SLUGS:
        raise ValueError("Unsupported Palladium ORDnet chain")
    result = common(event, now)
    height = int(event.get("blockHeight"))
    slug = ORDNET_CHAIN_SLUGS[coin]
    payload = get(opener, ORDNET_BASE, f"/{slug}/block/height/{height}")
    block_hash, block = _unique_height_hash_record(payload, height, "ORDnet")
    expected = str(event.get("blockHash") or "").lower().strip()
    chain_role = "AUXPOW_CHILD_CHAIN" if coin == "DOGE" else "PARENT_SCRYPT_CHAIN"
    if len(expected) == 64 and expected != block_hash:
        return {
            **result,
            "schemaVersion": 3,
            "status": "CONFLICT_BLOCK_HASH",
            "explorer": ORDNET_BASE,
            "fallbackFrom": BLOCKCHAIR_BASE,
            "primaryErrorType": primary_error,
            "onchainBlockHash": block_hash,
            "mergedMiningFamily": "Palladium",
            "mergedMiningChain": coin,
            "mergedMiningChainRole": chain_role,
            "mergedMiningEvidenceScope": "THIS_CHAIN_EVENT_ONLY",
            "pairedChainEvidenceClaimed": False,
        }
    return {
        **result,
        "schemaVersion": 3,
        "status": "VERIFIED_ON_CHAIN_BLOCK_MATCH",
        "verificationStrength": "BLOCK_HEIGHT_HASH_KEYLESS_NODE_FALLBACK",
        "explorer": ORDNET_BASE,
        "fallbackFrom": BLOCKCHAIR_BASE,
        "primaryErrorType": primary_error,
        "onchainBlockHash": block_hash,
        "onchainTimestamp": block.get("time") or block.get("timestamp"),
        "onchainDifficulty": block.get("difficulty"),
        "coinbaseRewardNative": None,
        "niceHashPayoutRewardNative": payout_native_from_public_reward(event),
        "payoutToCoinbasePercent": None,
        "coinbaseTagClass": "NOT_CHECKED_PALLADIUM_ORDNET_HASH_ONLY",
        "mergedMiningFamily": "Palladium",
        "mergedMiningChain": coin,
        "mergedMiningChainRole": chain_role,
        "mergedMiningEvidenceScope": "THIS_CHAIN_EVENT_ONLY",
        "pairedChainEvidenceClaimed": False,
    }


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
    base_result = common(event, now)
    if coin not in SUPPORTED:
        return apply_easymining_attribution(
            {**base_result, "status": "UNSUPPORTED_COIN", "explorer": None},
            event,
        )
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        if coin == "BCH":
            try:
                verified = verify_mempool(event, opener, now)
            except Exception as primary_exc:
                try:
                    verified = verify_bch_blockchair(
                        event,
                        opener,
                        now,
                        primary_error=type(primary_exc).__name__,
                    )
                except Exception as blockchair_exc:
                    verified = verify_bch_ninja_hash(
                        event,
                        opener,
                        now,
                        primary_error=type(primary_exc).__name__,
                        blockchair_error=type(blockchair_exc).__name__,
                    )
            # A real primary hash conflict is evidence, not an availability
            # failure, so never hide it behind a fallback.
        elif coin == "BTC":
            verified = verify_mempool(event, opener, now)
        elif coin in {"LTC", "DOGE"}:
            try:
                verified = verify_palladium_chain(event, opener, now)
            except Exception as primary_exc:
                verified = verify_palladium_ordnet_hash(
                    event,
                    opener,
                    now,
                    primary_error=type(primary_exc).__name__,
                )
        elif coin == "ZEC":
            verified = verify_zec(event, opener, now)
        elif coin == "KAS":
            verified = verify_kas(event, opener, now)
        else:
            verified = {**base_result, "status": "UNSUPPORTED_COIN", "explorer": None}
    except Exception as exc:
        verified = {
            **base_result,
            "status": "SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH",
            "explorer": None,
            "errorType": type(exc).__name__,
        }
    verified = refresh_payout_metrics(verified, event)
    return apply_easymining_attribution(verified, event)

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
    if max_new < 1 or max_new > 100:
        raise ValueError("max_new must be 1..100")
    now = now or utc_now()
    events = load_jsonl(input_path)
    event_by_id = {source_event_id(event): event for event in events if source_event_id(event)}
    latest = {source_event_id(r): r for r in load_jsonl(ledger_path) if source_event_id(r)}
    # Schema-only migration: no network. Existing verified rows gain explicit
    # EasyMining attribution and corrected payout units from their original source event.
    for eid, row in list(latest.items()):
        event = event_by_id.get(eid)
        if event is not None:
            latest[eid] = apply_easymining_attribution(
                refresh_payout_metrics(row, event),
                event,
            )
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
        retryable = {"SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH", "UNSUPPORTED_COIN"}
        if previous and previous.get("status") not in retryable:
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
            latest[source_event_id(event)] = verifier(event, now=now)
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
    attribution_counts = Counter(
        str(r.get("easyMiningAttribution") or "UNKNOWN") for r in rows
    )
    report = {
        "schemaVersion": 3,
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
        "easyMiningAttributionCounts": dict(attribution_counts),
        "supportedCoins": sorted(SUPPORTED),
        "packageCoverageIntent": {
            "Gold": "BTC",
            "Silver": "BCH",
            "Bronze": "ZEC",
            "Titanium": "KAS",
            "Palladium": "LTC + DOGE merged mining; each public event verified on its named chain only",
        },
        "policy": "Product attribution and pool attribution are separate. /NiceHash/ or a NiceHash miner label proves pool evidence only; EasyMining requires the public singleReward package/block identity or another explicit product-level source. Verified HITs are numerator/context evidence; no chain explorer supplies missing EasyMining tickets or a MISS denominator.",
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
    print(json.dumps({k: report[k] for k in ("inputEventCount","ledgerEventCount","processedThisRun","verifiedOnchainCount","verifiedByCoin","statusCounts","easyMiningAttributionCounts")}, sort_keys=True))


if __name__ == "__main__":
    main()
