"""Normalize the collected public success-event archive for on-chain research.

No network access. Input is calibration/realized-blocks.jsonl, which is collected
by the existing allowlisted public NiceHash collector.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_INPUT = Path("calibration/realized-blocks.jsonl")
DEFAULT_OUTPUT = Path("research/public-easymining-hit-history.jsonl")


def event_id(record):
    basis = "|".join(
        str(record.get(k) or "")
        for k in ("coin", "blockHeight", "packageId", "blockHash")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def identity(record):
    return (
        str(record.get("coin") or "").upper(),
        str(record.get("blockHeight") or ""),
        str(record.get("packageId") or ""),
        str(record.get("blockHash") or ""),
    )


def normalize(row):
    out = {
        "schemaVersion": 1,
        "eventId": event_id(row),
        "firstSeenAt": row.get("collected_at"),
        "coin": row.get("coin"),
        "blockHeight": row.get("blockHeight"),
        "blockHash": row.get("blockHash"),
        "payoutReward": row.get("payoutReward"),
        "payoutRewardBtc": row.get("payoutRewardBtc"),
        "time": row.get("time"),
        "createdTs": row.get("createdTs"),
        "packageId": row.get("packageId"),
        "packageName": row.get("packageName"),
        "shared": row.get("shared"),
        "source": "NICEHASH_PUBLIC_SINGLE_REWARD",
        "sourceRole": "SUCCESS_EVENTS_ONLY_NO_MISS_DENOMINATOR",
        "credentialsUsed": False,
        "privateApiUsed": False,
        "adminApiUsed": False,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
    }
    return out


def build(input_path: Path, output_path: Path):
    by_key = {}
    if input_path.exists():
        for line in input_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("coin") is None or row.get("blockHeight") is None:
                continue
            item = normalize(row)
            key = identity(item)
            if key in by_key and by_key[key] != item:
                raise ValueError(f"Conflicting source event: {key}")
            by_key[key] = item
    rows = sorted(
        by_key.values(),
        key=lambda r: (str(r.get("time") or ""), str(r.get("coin") or ""), str(r.get("blockHeight") or "")),
        reverse=True,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return {"recordsStored": len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.input, args.output)
    print("PUBLIC EASYMINING HIT RESEARCH ARCHIVE OK; no network; no decision changes")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
