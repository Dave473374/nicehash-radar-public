try:
    from radar_snapshot_archive import open_history
except ModuleNotFoundError:  # Also support package/spec imports from repository root.
    from scripts.radar_snapshot_archive import open_history
import json
import math
from bisect import bisect_left, bisect_right
import os
from datetime import datetime, timezone, timedelta

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
    if ts.tzinfo is None or ts.utcoffset() is None:
        return None
    return ts.astimezone(timezone.utc)


def event_time(event):
    value = event.get("time")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value) or value <= 0:
            return None
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


snapshots = [
    json.loads(line)
    for line in open_history(SNAPSHOTS_FILE, encoding="utf-8")
    if line.strip()
]

events = json.load(open(EVENTS_FILE, encoding="utf-8"))

snapshot_times = []
for snapshot in snapshots:
    ts = parse_time(snapshot.get("collected_at"))
    if ts is not None:
        snapshot_times.append((ts, snapshot))

snapshot_times.sort(key=lambda item: item[0])


snapshot_index = [ts for ts, _ in snapshot_times]


def feed_time(snapshot):
    feed = snapshot.get("feed") or {}
    # A recently captured old quote is not a fresh quote. Never substitute
    # collected_at for an absent/invalid source timestamp.
    value = feed.get("checked_at")
    if value in (None, ""):
        value = snapshot.get("feed_generated_at")
    source = parse_time(value)
    saved = snapshot.get("feed_generated_at")
    if saved not in (None, "") and parse_time(saved) != source:
        return None
    return source


def reward_context_snapshot(at):
    if at is None:
        return None
    lo = bisect_left(snapshot_index, at - timedelta(seconds=MAX_REWARD_CONTEXT_AGE_SECONDS))
    hi = bisect_right(snapshot_index, at)
    candidates = []
    for ts, snapshot in snapshot_times[lo:hi]:
        source = feed_time(snapshot)
        feed = snapshot.get("feed") or {}
        if (source is not None and source <= ts
                and 0 <= (at - source).total_seconds() <= MAX_REWARD_CONTEXT_AGE_SECONDS
                and feed.get("status") == "BUY FEED OK" and feed.get("ok") is True):
            candidates.append((ts, snapshot))
    if not candidates:
        return None
    latest_ts, latest = candidates[-1]
    # Conflicting rows at the same receipt time cannot be silently tie-broken.
    if any(ts == latest_ts and row.get("feed") != latest.get("feed") for ts, row in candidates):
        return None
    return latest


def legacy_btc_feed(feed):
    packages = feed.get("packages")
    if feed.get("relay_version") != "2.8.2" or not isinstance(packages, list) or not packages:
        return False
    for package in packages:
        if not isinstance(package, dict) or package.get("currency_market") not in (None, ""):
            return False
        try:
            price = float(package.get("price_btc"))
        except (TypeError, ValueError, OverflowError):
            return False
        if (isinstance(package.get("price_btc"), bool) or not math.isfinite(price)
                or price <= 0 or str(package.get("size") or "") not in {"S", "M"}):
            return False
    return True


def package_for(snapshot, name, event):
    if snapshot is None:
        return None
    feed = snapshot.get("feed") or {}
    coins = event.get("coins")
    if not isinstance(coins, list) or not coins or any(not isinstance(c, str) or not c for c in coins):
        return None
    coins = set(coins)
    event_currency = event.get("currencyMarket") or event.get("currency_market")
    matched = []
    for package in feed.get("packages") or []:
        if not isinstance(package, dict) or package.get("name") != name:
            continue
        primary = (package.get("primary_chain") or {}).get("currency")
        merge = (package.get("merge_chain") or {}).get("currency")
        if not coins <= {primary, merge}:
            continue
        currency = package.get("currency_market")
        if currency in (None, "") and legacy_btc_feed(feed):
            currency = "BTC"
        if currency not in {"BTC", "USDT"}:
            continue
        if event_currency is not None and event_currency != currency:
            continue
        matched.append(package)
    # The public reward archive usually has no payment currency. Only an
    # unambiguous package may supply context; this is not proof of paid currency.
    return matched[0] if len(matched) == 1 else None


records = []

for event in events:
    at = event_time(event)
    if at is None:
        continue

    snapshot = reward_context_snapshot(at)
    package = package_for(snapshot, event.get("packageName"), event)
    source_ts = feed_time(snapshot) if snapshot is not None else None

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
        "snapshot_feed_time": source_ts.isoformat() if source_ts is not None else None,
        "snapshot_feed_age_seconds": (
            round((at - source_ts).total_seconds(), 1) if source_ts is not None else None
        ),
        "currency_match_basis": (
            "EXPLICIT_EVENT_CURRENCY" if event.get("currencyMarket") or event.get("currency_market")
            else "UNAMBIGUOUS_PACKAGE_CONTEXT_NOT_PAYMENT_PROOF"
        ) if package is not None else None,
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

# Serialize completely before opening the destination: invalid numbers must
# not truncate the last valid output.
payload = "".join(
    json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
    for record in records
)
with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
    handle.write(payload)

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
