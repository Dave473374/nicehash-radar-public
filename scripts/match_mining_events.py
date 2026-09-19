import json
import os
from datetime import datetime, timezone

SNAPSHOTS_FILE = os.getenv(
    "RADAR_SNAPSHOTS_FILE",
    "calibration/radar-snapshots.jsonl",
)
EVENTS_FILE = os.getenv(
    "MINING_EVENTS_FILE",
    "mining-events.json",
)
OUTPUT_FILE = os.getenv(
    "MINING_EVENT_MATCH_OUTPUT",
    "calibration/mining-event-matches.jsonl",
)

# Reward events describe context near the successful reward time only.
# They must never be treated as entry-time evidence or a MISS denominator.
MAX_REWARD_CONTEXT_AGE_SECONDS = 15 * 60


def parse_time(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def event_time(event):
    value = event.get("time")
    if not value:
        return None
    return datetime.fromtimestamp(
        value / 1000,
        tz=timezone.utc,
    )


snapshots = [
    json.loads(line)
    for line in open(SNAPSHOTS_FILE, encoding="utf-8")
    if line.strip()
]

events = json.load(open(EVENTS_FILE, encoding="utf-8"))

snapshot_times = []
for snapshot in snapshots:
    ts = parse_time(snapshot.get("collected_at"))
    if ts is not None:
        snapshot_times.append((ts, snapshot))

snapshot_times.sort(key=lambda item: item[0])


def reward_context_snapshot(at):
    if at is None:
        return None

    candidates = [
        (ts, snapshot)
        for ts, snapshot in snapshot_times
        if ts <= at
        and (at - ts).total_seconds()
        <= MAX_REWARD_CONTEXT_AGE_SECONDS
    ]

    if not candidates:
        return None

    return max(candidates, key=lambda item: item[0])[1]


def package_for(snapshot, name):
    if snapshot is None:
        return None
    return next(
        (
            package
            for package in snapshot.get("feed", {}).get("packages", [])
            if package.get("name") == name
        ),
        None,
    )


records = []

for event in events:
    at = event_time(event)
    if at is None:
        continue

    snapshot = reward_context_snapshot(at)
    package = package_for(snapshot, event.get("packageName"))

    snapshot_ts = (
        parse_time(snapshot.get("collected_at"))
        if snapshot is not None
        else None
    )

    record = {
        "event_id": event.get("eventId"),
        "event_time": event.get("time"),
        "package_id": event.get("packageId"),
        "package_name": event.get("packageName"),
        "coins": event.get("coins"),
        "reward_count": event.get("rewardCount"),
        "merged_mining": event.get("mergedMining"),
        "total_payout_reward_btc": event.get("totalPayoutRewardBtc"),
        "rewards": event.get("rewards"),
        "evidence_role": "REWARD_TIME_CONTEXT_ONLY",
        "entry_time_eligible": False,
        "can_supply_miss_denominator": False,
        "snapshot_found": snapshot is not None,
        "package_match_found": package is not None,
        "radar_matched": snapshot is not None and package is not None,
        "snapshot_time": (
            snapshot.get("collected_at")
            if snapshot is not None
            else None
        ),
        "snapshot_age_seconds": (
            round((at - snapshot_ts).total_seconds(), 1)
            if snapshot_ts is not None
            else None
        ),
        "price_btc": (
            package.get("price_btc")
            if package is not None
            else None
        ),
        "primary_coin": (
            (package.get("primary_chain") or {}).get("currency")
            if package is not None
            else None
        ),
        "merge_coin": (
            (package.get("merge_chain") or {}).get("currency")
            if package is not None
            and package.get("merge_chain")
            else None
        ),
        "mining_signal": (
            package.get("mining_signal")
            if package is not None
            else None
        ),
        "economic_signal": (
            package.get("economic_signal")
            if package is not None
            else None
        ),
        "final_signal": (
            package.get("final_signal")
            if package is not None
            else None
        ),
        "decision": (
            package.get("decision")
            if package is not None
            else None
        ),
        "expected_blocks": (
            (package.get("primary_chain") or {}).get("expected_blocks")
            if package is not None
            else None
        ),
        "model_hit_probability_percent": (
            (package.get("primary_chain") or {}).get(
                "model_hit_probability_percent"
            )
            if package is not None
            else None
        ),
        "nicehash_odds": (
            (package.get("nicehash_odds") or {}).get("display")
            if package is not None
            else None
        ),
        "expected_reward_btc_equiv": (
            (package.get("profitability") or {}).get(
                "expected_reward_btc_equiv"
            )
            if package is not None
            else None
        ),
        "expected_return_percent": (
            (package.get("profitability") or {}).get(
                "expected_return_percent"
            )
            if package is not None
            else None
        ),
        "profitability_margin_percent": (
            (package.get("profitability") or {}).get(
                "profitability_margin_percent"
            )
            if package is not None
            else None
        ),
        "break_even_block_target": (
            (package.get("profitability") or {}).get(
                "break_even_block_target"
            )
            if package is not None
            else None
        ),
        "break_even_multiple_vs_expected_blocks": (
            (package.get("profitability") or {}).get(
                "break_even_multiple_vs_expected_blocks"
            )
            if package is not None
            else None
        ),
        "break_even_probability_percent": (
            (package.get("profitability") or {}).get(
                "break_even_probability_percent_approx"
            )
            if package is not None
            else None
        ),
        "break_even_risk": (
            (package.get("profitability") or {})
            .get("break_even_risk", {})
            .get("status")
            if package is not None
            else None
        ),
        "break_even_cap": (
            (package.get("profitability") or {})
            .get("break_even_risk", {})
            .get("max_final_signal")
            if package is not None
            else None
        ),
        "quality_vs_24h_percent": (
            (package.get("history_trend") or {}).get(
                "expected_blocks_per_btc_vs_24h_percent"
            )
            if package is not None
            else None
        ),
        "quality_vs_7d_percent": (
            (package.get("history_trend") or {}).get(
                "expected_blocks_per_btc_vs_7d_percent"
            )
            if package is not None
            else None
        ),
        "history_samples_24h": (
            (package.get("history_trend") or {}).get("sample_count_24h")
            if package is not None
            else None
        ),
        "history_samples_7d": (
            (package.get("history_trend") or {}).get("sample_count_7d")
            if package is not None
            else None
        ),
    }

    records.append(record)

open(OUTPUT_FILE, "w", encoding="utf-8").write(
    "".join(
        json.dumps(record, separators=(",", ":")) + "\n"
        for record in records
    )
)

print("REWARD-TIME CONTEXT MATCHING")
print("Radar snapshots:", len(snapshots))
print("Mining events:", len(events))
print("Events written:", len(records))
print(
    "Fresh reward-context matches:",
    sum(record.get("radar_matched") is True for record in records),
)
print(
    "Entry-time eligible:",
    sum(record.get("entry_time_eligible") is True for record in records),
)
print("MISS denominator supplied: NO")
