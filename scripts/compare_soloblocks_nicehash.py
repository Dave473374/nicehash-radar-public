"""Compare SoloBlocks NiceHash BTC HIT history with NiceHash singleReward history.

A = present in both sources
B = NiceHash singleReward only
C = SoloBlocks only

Research-only source coverage report. Presence in either source is success-event
evidence only and never supplies a MISS denominator or BUY signal.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

DEFAULT_SOLO = Path("research/soloblocks-btc-hits.jsonl")
DEFAULT_NICEHASH = Path("recent-blocks.json")
DEFAULT_LEDGER = Path("research/soloblocks-btc-onchain-verification.jsonl")
DEFAULT_OUTPUT = Path("research/soloblocks-btc-abc-report.json")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def load_json_list(path: Path):
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Expected JSON list")
    return [row for row in payload if isinstance(row, dict)]


def btc_heights(rows):
    out = set()
    for row in rows:
        if str(row.get("coin") or "").upper() != "BTC":
            continue
        try:
            height = int(row.get("blockHeight"))
        except (TypeError, ValueError):
            continue
        if height > 0:
            out.add(height)
    return out


def build(solo_input: Path, nicehash_input: Path, ledger: Path, output: Path, now=None):
    now = now or utc_now()
    solo_rows = load_jsonl(solo_input)
    nicehash_rows = load_json_list(nicehash_input)
    ledger_rows = load_jsonl(ledger)

    solo = btc_heights(solo_rows)
    nicehash = btc_heights(nicehash_rows)
    both = sorted(solo & nicehash, reverse=True)
    nicehash_only = sorted(nicehash - solo, reverse=True)
    solo_only = sorted(solo - nicehash, reverse=True)

    status_counts = Counter(str(r.get("status") or "UNKNOWN") for r in ledger_rows)
    verified = btc_heights(
        r for r in ledger_rows
        if str(r.get("status") or "").startswith("VERIFIED_ON_CHAIN")
    )

    report = {
        "schemaVersion": 1,
        "generatedAt": now,
        "role": "SOLOBLOCKS_NICEHASH_BTC_SOURCE_COMPARISON_RESEARCH_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
        "automaticPurchase": False,
        "successEventsOnly": True,
        "packageVariantKnownFromSoloBlocks": False,
        "soloBlocksBtcCount": len(solo),
        "niceHashSingleRewardBtcCount": len(nicehash),
        "A_bothCount": len(both),
        "B_niceHashOnlyCount": len(nicehash_only),
        "C_soloBlocksOnlyCount": len(solo_only),
        "A_bothHeights": both,
        "B_niceHashOnlyHeights": nicehash_only,
        "C_soloBlocksOnlyHeights": solo_only,
        "soloBlocksOnchainVerifiedCount": len(solo & verified),
        "soloBlocksNotYetOnchainVerifiedCount": len(solo - verified),
        "onchainStatusCounts": dict(status_counts),
        "definitions": {
            "A": "BTC block height present in both SoloBlocks NiceHash history and NiceHash public singleReward archive.",
            "B": "BTC block height present only in NiceHash public singleReward archive.",
            "C": "BTC block height present only in SoloBlocks NiceHash history.",
        },
        "policy": (
            "Source-presence comparison only. SoloBlocks does not identify EasyMining package size "
            "(S/M/L) and neither source provides failed tickets, so this report cannot estimate hit rate "
            "or raise a BUY signal. Independent on-chain verification is tracked separately."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo-input", type=Path, default=DEFAULT_SOLO)
    parser.add_argument("--nicehash-input", type=Path, default=DEFAULT_NICEHASH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build(args.solo_input, args.nicehash_input, args.ledger, args.output)
    print("SOLOBLOCKS/NICEHASH BTC A-B-C REPORT COMPLETE; research-only")
    print(json.dumps({
        k: report[k] for k in (
            "soloBlocksBtcCount",
            "niceHashSingleRewardBtcCount",
            "A_bothCount",
            "B_niceHashOnlyCount",
            "C_soloBlocksOnlyCount",
            "soloBlocksOnchainVerifiedCount",
        )
    }, sort_keys=True))


if __name__ == "__main__":
    main()
