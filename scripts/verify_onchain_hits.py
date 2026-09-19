"""Verify public EasyMining hit events against independent on-chain explorers.

BTC uses mempool.space and BCH uses bchexplorer.cash. Both expose mempool-style
read-only REST endpoints. Verification is research/audit only and cannot alter
CURRENT, BATCH, alerts, orders, or hit-rate denominators.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import urllib.request
from pathlib import Path

DEFAULT_INPUT = Path("research/public-easymining-hit-history.jsonl")
DEFAULT_LEDGER = Path("research/onchain-hit-verification.jsonl")
DEFAULT_REPORT = Path("research/onchain-hit-report.json")
MAX_RESPONSE_BYTES = 4_000_000

EXPLORERS = {
    "BTC": "https://mempool.space",
    "BCH": "https://bchexplorer.cash",
}
KNOWN_TAGS = (
    (b"/NiceHashMining/", "NICEHASH_MINING_TAG"),
    (b"/NiceHashSolo/", "NICEHASH_SOLO_TAG"),
    (b"/NiceHash/", "NICEHASH_TAG"),
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("On-chain explorer redirect refused")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def get(opener, base: str, path: str, json_expected: bool):
    if base not in EXPLORERS.values() or not path.startswith("/api/"):
        raise ValueError("Explorer request not allowlisted")
    url = base + path
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "nicehash-radar-onchain-audit/1.0", "Accept": "application/json,text/plain"})
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected explorer response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Explorer response too large")
    text = raw.decode("utf-8").strip()
    return json.loads(text) if json_expected else text


def classify_tag(script_hex: str):
    try:
        raw = bytes.fromhex(str(script_hex or ""))
    except ValueError:
        return "INVALID_COINBASE_SCRIPTSIG", None
    for needle, label in KNOWN_TAGS:
        if needle.lower() in raw.lower():
            return label, needle.decode("ascii")
    return "UNKNOWN_TAG", None


def source_event_id(event: dict) -> str:
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
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def verify_event(event: dict, opener=None, now=None):
    now = now or utc_now()
    coin = str(event.get("coin") or "").upper()
    event_id = source_event_id(event)
    common = {
        "schemaVersion": 1,
        "eventId": event_id,
        "verifiedAt": now,
        "coin": coin or None,
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
    if coin not in EXPLORERS:
        return {**common, "status": "UNSUPPORTED_COIN", "explorer": None}
    try:
        height = int(event.get("blockHeight"))
        if height <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return {**common, "status": "INVALID_BLOCK_HEIGHT", "explorer": EXPLORERS[coin]}

    base = EXPLORERS[coin]
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        block_hash = get(opener, base, f"/api/block-height/{height}", False).strip()
        if len(block_hash) != 64:
            raise ValueError("Explorer returned invalid block hash")
        expected_hash = str(event.get("blockHash") or "").lower().strip()
        if len(expected_hash) == 64 and expected_hash != block_hash.lower():
            return {
                **common,
                "status": "CONFLICT_BLOCK_HASH",
                "explorer": base,
                "onchainBlockHash": block_hash,
            }

        block = get(opener, base, f"/api/block/{block_hash}", True)
        txs = get(opener, base, f"/api/block/{block_hash}/txs/0", True)
        if not isinstance(block, dict) or not isinstance(txs, list) or not txs or not isinstance(txs[0], dict):
            raise ValueError("Unexpected explorer block/transaction schema")
        tx0 = txs[0]
        vin = tx0.get("vin")
        vout = tx0.get("vout")
        if not isinstance(vin, list) or not vin or not isinstance(vin[0], dict):
            raise ValueError("Coinbase input missing")
        tag_status, tag = classify_tag(vin[0].get("scriptsig"))

        reward_sats = None
        if isinstance(vout, list):
            values = [x.get("value") for x in vout if isinstance(x, dict)]
            if values and all(finite(x) for x in values):
                reward_sats = sum(float(x) for x in values)
        reward_native = reward_sats / 100_000_000 if reward_sats is not None else None
        payout_ratio = None
        try:
            payout = float(event.get("payoutReward"))
            if math.isfinite(payout) and payout >= 0 and reward_native and reward_native > 0:
                payout_ratio = payout / reward_native * 100
        except (TypeError, ValueError):
            pass

        source_time = parse_source_time(event.get("time") or event.get("createdTs"))
        block_ts = block.get("timestamp")
        delta_seconds = None
        if source_time is not None and finite(block_ts):
            onchain_dt = datetime.fromtimestamp(float(block_ts), tz=timezone.utc)
            delta_seconds = abs((source_time - onchain_dt).total_seconds())

        if tag_status == "NICEHASH_TAG":
            status = "VERIFIED_ON_CHAIN_NICEHASH_TAG"
        elif tag_status in {"NICEHASH_MINING_TAG", "NICEHASH_SOLO_TAG"}:
            status = "VERIFIED_ON_CHAIN_OTHER_NICEHASH_TAG"
        elif tag_status == "INVALID_COINBASE_SCRIPTSIG":
            status = "BLOCK_VERIFIED_INVALID_TAG_DATA"
        else:
            status = "BLOCK_VERIFIED_TAG_UNKNOWN"

        return {
            **common,
            "status": status,
            "explorer": base,
            "onchainBlockHash": block_hash,
            "onchainTimestamp": block_ts,
            "onchainDifficulty": block.get("difficulty"),
            "coinbaseTagClass": tag_status,
            "coinbaseTag": tag,
            "coinbaseRewardNative": round(reward_native, 12) if reward_native is not None else None,
            "payoutToCoinbasePercent": round(payout_ratio, 6) if payout_ratio is not None else None,
            "sourceTimeVsOnchainSeconds": round(delta_seconds, 3) if delta_seconds is not None else None,
        }
    except Exception as exc:
        return {
            **common,
            "status": "SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH",
            "explorer": base,
            "errorType": type(exc).__name__,
        }


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def build(input_path: Path, ledger_path: Path, report_path: Path, max_new: int, verifier=verify_event, now=None):
    if max_new < 1 or max_new > 100:
        raise ValueError("max_new must be 1..100")
    now = now or utc_now()
    events = load_jsonl(input_path)
    old = load_jsonl(ledger_path)
    latest = {}
    for row in old:
        eid = source_event_id(row)
        if eid:
            latest[eid] = row

    processed = 0
    for event in events:
        eid = source_event_id(event)
        coin = str(event.get("coin") or "").upper()
        previous = latest.get(eid)
        if previous and previous.get("status") not in {"SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH"}:
            continue
        if coin not in EXPLORERS:
            if previous is None:
                latest[eid] = verifier(event, now=now)
            continue
        if processed >= max_new:
            continue
        latest[eid] = verifier(event, now=now)
        processed += 1

    rows = sorted(latest.values(), key=lambda r: (str(r.get("coin") or ""), int(r.get("blockHeight") or 0), str(r.get("eventId") or "")), reverse=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in rows), encoding="utf-8")

    counts = Counter(str(r.get("status") or "UNKNOWN") for r in rows)
    tag_counts = Counter(str(r.get("coinbaseTagClass") or "NONE") for r in rows)
    verified = sum(v for k, v in counts.items() if k.startswith("VERIFIED_ON_CHAIN"))
    report = {
        "schemaVersion": 1,
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
        "verifiedNiceHashTagCount": counts.get("VERIFIED_ON_CHAIN_NICEHASH_TAG", 0),
        "verifiedOnchainCount": verified,
        "statusCounts": dict(counts),
        "tagCounts": dict(tag_counts),
        "supportedCoins": sorted(EXPLORERS),
        "policy": "Independent chain audit only. A verified HIT is numerator/context evidence and never supplies missing tickets or a MISS denominator.",
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
    print(json.dumps({k: report[k] for k in ("inputEventCount", "ledgerEventCount", "processedThisRun", "verifiedOnchainCount", "verifiedNiceHashTagCount", "statusCounts")}, sort_keys=True))


if __name__ == "__main__":
    main()
