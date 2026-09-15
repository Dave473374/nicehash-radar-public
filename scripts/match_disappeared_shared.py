import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

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


def safe_time(value):
    ts = parse_ts(value)
    if not ts:
        return None
    return ts.isoformat()


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


disappeared_rows = [
    row
    for row in history
    if row.get("status") == "DISAPPEARED"
    and row.get("package_id")
]


# One final DISAPPEARED record per concrete shared package instance
latest_disappeared_by_id = {}

for row in disappeared_rows:
    package_id = row.get("package_id")
    ts = parse_ts(row.get("collected_at"))

    existing = latest_disappeared_by_id.get(package_id)

    if (
        existing is None
        or (
            ts is not None
            and (
                parse_ts(existing.get("collected_at")) is None
                or ts > parse_ts(existing.get("collected_at"))
            )
        )
    ):
        latest_disappeared_by_id[package_id] = row


results = []


for package_id, target in latest_disappeared_by_id.items():

    lifecycle = [
        row
        for row in history
        if row.get("package_id") == package_id
    ]

    lifecycle.sort(
        key=lambda row: (
            parse_ts(row.get("collected_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
    )

    pre_disappear = [
        row
        for row in lifecycle
        if row.get("status") != "DISAPPEARED"
    ]

    observed_statuses = sorted({
        str(row.get("status"))
        for row in pre_disappear
        if row.get("status")
    })

    waiting_only = (
        bool(pre_disappear)
        and set(observed_statuses) == {"WAITING"}
    )

    non_waiting_statuses = [
        status
        for status in observed_statuses
        if status != "WAITING"
    ]

    if waiting_only:
        lifecycle_class = "WAITING_ONLY"
        calibration_eligibility = "EXCLUDE_NO_OBSERVED_MINING"
    elif non_waiting_statuses:
        lifecycle_class = "NON_WAITING_STATUS_OBSERVED"
        calibration_eligibility = "REVIEW_REQUIRED"
    else:
        lifecycle_class = "INSUFFICIENT_LIFECYCLE_DATA"
        calibration_eligibility = "EXCLUDE_INSUFFICIENT_DATA"

    ticket = target.get("currencyAlgoTicket") or {}
    primary = ticket.get("currencyAlgo") or {}
    merge = ticket.get("mergeCurrencyAlgo") or {}

    ticket_id = ticket.get("id")
    package_name = ticket.get("name")
    primary_coin = primary.get("currency")
    merge_coin = merge.get("currency")

    created = parse_ts(target.get("createdTs"))
    disappeared_at = parse_ts(target.get("collected_at"))

    exact_candidates = []
    buffered_candidates = []

    if created and disappeared_at:

        buffer_start = created - timedelta(
            minutes=BUFFER_MINUTES
        )

        buffer_end = disappeared_at + timedelta(
            minutes=BUFFER_MINUTES
        )

        for block in blocks:

            block_ts = (
                parse_ts(block.get("createdTs"))
                or parse_ts(block.get("time"))
                or parse_ts(block.get("collected_at"))
            )

            if not block_ts:
                continue

            if block.get("shared") is not True:
                continue

            if block.get("packageName") != package_name:
                continue

            if block.get("coin") != primary_coin:
                continue

            safe_block = {
                "coin": block.get("coin"),
                "packageName": block.get("packageName"),
                "createdTs": block.get("createdTs"),
                "time": block.get("time"),
                "payoutRewardBtc": block.get("payoutRewardBtc")
            }

            if created <= block_ts <= disappeared_at:
                exact_candidates.append(safe_block)

            elif buffer_start <= block_ts <= buffer_end:
                buffered_candidates.append(safe_block)

    lifecycle_timeline = []

    for row in lifecycle:
        lifecycle_timeline.append({
            "observedAt": safe_time(row.get("collected_at")),
            "status": row.get("status"),
            "countdownDuration": row.get("countdownDuration"),
            "duration": row.get("duration"),
            "participants": row.get("numberOfParticipants"),
            "probability": row.get("probability"),
            "mergeProbability": row.get("mergeProbability"),
            "projectedSpeed": row.get("projectedSpeed")
        })

    results.append({
        "packageId": package_id,
        "ticketId": ticket_id,

        "packageName": package_name,
        "primaryCoin": primary_coin,
        "mergeCoin": merge_coin,

        "createdTs": target.get("createdTs"),
        "disappearedAt": target.get("collected_at"),

        "durationSeconds": target.get("duration"),
        "countdownDurationSeconds": target.get("countdownDuration"),

        "lifecycleRecords": len(lifecycle),

        "observedStatuses": observed_statuses,

        "lifecycleClass": lifecycle_class,

        "calibrationEligibility": calibration_eligibility,

        "candidateBlocksExactWindow": len(exact_candidates),

        "candidateBlocksBufferedWindow": len(
            buffered_candidates
        ),

        "exactWindowBlocks": exact_candidates,

        "bufferedWindowBlocks": buffered_candidates,

        "lifecycleTimeline": lifecycle_timeline
    })


print("DISAPPEARED PACKAGE INSTANCES", len(results))

print(
    "WAITING ONLY",
    sum(
        1
        for x in results
        if x["lifecycleClass"] == "WAITING_ONLY"
    )
)

print(
    "REVIEW REQUIRED",
    sum(
        1
        for x in results
        if x["calibrationEligibility"] == "REVIEW_REQUIRED"
    )
)

print(
    "INSUFFICIENT DATA",
    sum(
        1
        for x in results
        if x["calibrationEligibility"]
        == "EXCLUDE_INSUFFICIENT_DATA"
    )
)

print("")
print("REVIEW REQUIRED LIFECYCLES")

review_required = [
    x
    for x in results
    if x["calibrationEligibility"] == "REVIEW_REQUIRED"
]

print(json.dumps(
    review_required,
    indent=2,
    ensure_ascii=False
))

print("")
print("IMPORTANT")
print(
    "No package is automatically labelled HIT or MISS."
)
print(
    "WAITING-only packages are excluded from calibration."
)
print(
    "COUNTDOWN or any other non-WAITING status remains REVIEW_REQUIRED."
)
