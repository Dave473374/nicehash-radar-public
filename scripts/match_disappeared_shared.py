import json
from pathlib import Path
from datetime import datetime, timedelta

HISTORY = Path("calibration/shared-package-history.jsonl")
BLOCKS = Path("calibration/realized-blocks.jsonl")

BUFFER_MINUTES = 15


def parse_ts(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except Exception:
        return None


history = [
    json.loads(line)
    for line in HISTORY.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

blocks = [
    json.loads(line)
    for line in BLOCKS.read_text(encoding="utf-8").splitlines()
    if line.strip()
]


disappeared = [
    row
    for row in history
    if row.get("status") == "DISAPPEARED"
]

print("DISAPPEARED RECORDS", len(disappeared))


if not disappeared:
    print("NO DISAPPEARED RECORD FOUND")
    raise SystemExit(0)


target = disappeared[-1]

package_id = target.get("package_id")

ticket = target.get("currencyAlgoTicket") or {}
primary = ticket.get("currencyAlgo") or {}
merge = ticket.get("mergeCurrencyAlgo") or {}

package_name = ticket.get("name")
primary_coin = primary.get("currency")
merge_coin = merge.get("currency")

created = parse_ts(target.get("createdTs"))
disappeared_at = parse_ts(target.get("collected_at"))


lifecycle = [
    row
    for row in history
    if row.get("package_id") == package_id
]

lifecycle = sorted(
    lifecycle,
    key=lambda row: (
        parse_ts(row.get("collected_at"))
        or datetime.min.replace(tzinfo=created.tzinfo)
    )
)


print("")
print("TARGET PACKAGE")
print(json.dumps(
    {
        "package_name": package_name,
        "primary_coin": primary_coin,
        "merge_coin": merge_coin,
        "createdTs": target.get("createdTs"),
        "disappearedAt": target.get("collected_at"),
        "durationSeconds": target.get("duration"),
        "participants": target.get("numberOfParticipants"),
        "probability": target.get("probability"),
        "mergeProbability": target.get("mergeProbability"),
        "lifecycleRecords": len(lifecycle)
    },
    indent=2,
    ensure_ascii=False
))


print("")
print("PACKAGE LIFECYCLE")

safe_lifecycle = [
    {
        "collected_at": row.get("collected_at"),
        "status": row.get("status"),
        "participants": row.get("numberOfParticipants"),
        "probability": row.get("probability"),
        "mergeProbability": row.get("mergeProbability")
    }
    for row in lifecycle
]

print(json.dumps(
    safe_lifecycle,
    indent=2,
    ensure_ascii=False
))


if not created or not disappeared_at:
    print("")
    print("INVALID TARGET TIME RANGE")
    raise SystemExit(0)


buffer_start = created - timedelta(minutes=BUFFER_MINUTES)
buffer_end = disappeared_at + timedelta(minutes=BUFFER_MINUTES)


def block_time(block):
    return (
        parse_ts(block.get("createdTs"))
        or parse_ts(block.get("time"))
        or parse_ts(block.get("collected_at"))
    )


def same_package_type(block):
    return (
        block.get("coin") == primary_coin
        and block.get("packageName") == package_name
        and block.get("shared") is True
    )


exact_candidates = []

buffer_candidates = []


for block in blocks:

    ts = block_time(block)

    if not ts:
        continue

    if not same_package_type(block):
        continue

    safe_block = {
        "coin": block.get("coin"),
        "packageName": block.get("packageName"),
        "shared": block.get("shared"),
        "createdTs": block.get("createdTs"),
        "time": block.get("time"),
        "payoutRewardBtc": block.get("payoutRewardBtc")
    }

    if created <= ts <= disappeared_at:
        exact_candidates.append(safe_block)

    elif buffer_start <= ts <= buffer_end:
        buffer_candidates.append(safe_block)


print("")
print("EXACT WINDOW")
print(
    created.isoformat(),
    "->",
    disappeared_at.isoformat()
)

print(
    "TEAM/PACKAGE BTC BLOCKS IN EXACT WINDOW",
    len(exact_candidates)
)

print(json.dumps(
    exact_candidates,
    indent=2,
    ensure_ascii=False
))


print("")
print("BUFFERED WINDOW")
print(
    buffer_start.isoformat(),
    "->",
    buffer_end.isoformat()
)

print(
    "ADDITIONAL BLOCKS IN +/- 15 MIN BUFFER",
    len(buffer_candidates)
)

print(json.dumps(
    buffer_candidates,
    indent=2,
    ensure_ascii=False
))


print("")
print("DIAGNOSTIC COMPLETE")
