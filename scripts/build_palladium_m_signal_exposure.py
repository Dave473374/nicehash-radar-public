"""Build research-only Palladium M daily Radar signal exposure.

This does not use Admin data and does not infer order-level outcomes. It turns
causally available BUY-feed snapshots into per-day signal exposure so later
authorized aggregate outcome data can be compared conservatively.

Important: daily aggregate outcomes cannot establish order-level causality when
the signal changes within a day. Mixed or poorly covered days are therefore
flagged and should not be used as primary performance evidence.
"""
from __future__ import annotations

try:
    from radar_snapshot_archive import read_history_text
except ModuleNotFoundError:  # Also support package/spec imports from repository root.
    from scripts.radar_snapshot_archive import read_history_text
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_HISTORY = Path("calibration/radar-snapshots.jsonl")
DEFAULT_OUTPUT = Path("research/palladium-m-signal-exposure.json")
PACKAGE_NAME = "Palladium M"
PRIMARY_COIN = "LTC"
CURRENCY_MARKET = "BTC"
VALID_SIGNALS = {"STRONG BUY", "BUY NOW", "GOOD", "WAIT", "NO BUY"}
MAX_FRESH_AGE_SECONDS = 15 * 60
DEFAULT_DOMINANT_FRACTION = 0.80
DEFAULT_DAY_COVERAGE_FRACTION = 0.75


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def snapshot_point(snapshot):
    if not isinstance(snapshot, dict):
        return None

    feed = snapshot.get("feed") or {}
    source = parse_ts(feed.get("checked_at") or snapshot.get("feed_generated_at"))
    receipt = parse_ts(snapshot.get("collected_at"))
    if source is None or receipt is None:
        return None

    age = (receipt - source).total_seconds()
    if age < 0 or age > MAX_FRESH_AGE_SECONDS:
        return None

    package = None
    for row in feed.get("packages") or []:
        if not isinstance(row, dict):
            continue
        if row.get("name") != PACKAGE_NAME:
            continue
        coin = (row.get("primary_chain") or {}).get("currency")
        currency = str(row.get("currency_market") or "").upper()
        if coin != PRIMARY_COIN or currency != CURRENCY_MARKET:
            continue
        package = row
        break

    if package is None:
        return None

    signal = package.get("final_signal")
    if signal not in VALID_SIGNALS:
        return None

    return {
        "availableAt": receipt,
        "sourceAt": source,
        "signal": signal,
        "relayVersion": feed.get("relay_version") or snapshot.get("relay_version"),
    }


def load_points(path):
    points = []
    for line in read_history_text(path, encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        point = snapshot_point(row)
        if point is not None:
            points.append(point)

    # Same receipt time with conflicting signals is ambiguous; drop that instant.
    grouped = defaultdict(list)
    for point in points:
        grouped[point["availableAt"]].append(point)

    unique = []
    for at, rows in grouped.items():
        signals = {row["signal"] for row in rows}
        if len(signals) != 1:
            continue
        rows.sort(key=lambda row: row["sourceAt"])
        unique.append(rows[-1])

    unique.sort(key=lambda row: row["availableAt"])
    return unique


def split_interval(start, end, signal, tz, buckets):
    cursor = start
    while cursor < end:
        local = cursor.astimezone(tz)
        next_local_midnight = (
            datetime(
                local.year,
                local.month,
                local.day,
                tzinfo=tz,
            )
            + timedelta(days=1)
        )
        boundary = min(end, next_local_midnight.astimezone(timezone.utc))
        seconds = (boundary - cursor).total_seconds()
        day = local.date().isoformat()
        buckets[day][signal] += seconds
        cursor = boundary


def build_exposure(points, tz_name, dominant_fraction, day_coverage_fraction):
    tz = ZoneInfo(tz_name)
    buckets = defaultdict(lambda: defaultdict(float))

    for index, point in enumerate(points):
        start = point["availableAt"]
        freshness_end = start + timedelta(seconds=MAX_FRESH_AGE_SECONDS)
        if index + 1 < len(points):
            next_available = points[index + 1]["availableAt"]
            end = min(freshness_end, next_available)
        else:
            end = freshness_end

        if end <= start:
            continue

        split_interval(start, end, point["signal"], tz, buckets)

    rows = []
    for day in sorted(buckets):
        by_signal_seconds = dict(buckets[day])
        observed = sum(by_signal_seconds.values())
        if observed <= 0:
            continue

        dominant_signal, dominant_seconds = max(
            by_signal_seconds.items(),
            key=lambda item: (item[1], item[0]),
        )
        dominant_share = dominant_seconds / observed
        day_seconds = 24 * 60 * 60
        coverage = observed / day_seconds

        rows.append({
            "date": day,
            "timezone": tz_name,
            "observedMinutes": round(observed / 60, 2),
            "coverageFractionOfDay": round(coverage, 6),
            "signalMinutes": {
                signal: round(seconds / 60, 2)
                for signal, seconds in sorted(by_signal_seconds.items())
            },
            "signalFractionOfObserved": {
                signal: round(seconds / observed, 6)
                for signal, seconds in sorted(by_signal_seconds.items())
            },
            "dominantSignal": dominant_signal,
            "dominantSignalFraction": round(dominant_share, 6),
            "aggregateEligible": bool(
                coverage >= day_coverage_fraction
                and dominant_share >= dominant_fraction
            ),
        })

    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timezone", default="Europe/Ljubljana")
    parser.add_argument(
        "--dominant-fraction",
        type=float,
        default=DEFAULT_DOMINANT_FRACTION,
    )
    parser.add_argument(
        "--day-coverage-fraction",
        type=float,
        default=DEFAULT_DAY_COVERAGE_FRACTION,
    )
    args = parser.parse_args()

    if not 0 < args.dominant_fraction <= 1:
        raise SystemExit("dominant-fraction must be in (0, 1]")
    if not 0 < args.day_coverage_fraction <= 1:
        raise SystemExit("day-coverage-fraction must be in (0, 1]")

    points = load_points(args.history)
    rows = build_exposure(
        points,
        args.timezone,
        args.dominant_fraction,
        args.day_coverage_fraction,
    )

    result = {
        "schemaVersion": 1,
        "role": "PALLADIUM_M_AGGREGATE_SIGNAL_EXPOSURE_RESEARCH_ONLY",
        "packageName": PACKAGE_NAME,
        "primaryCoin": PRIMARY_COIN,
        "currencyMarket": CURRENCY_MARKET,
        "timezone": args.timezone,
        "snapshotFreshnessMaxMinutes": MAX_FRESH_AGE_SECONDS / 60,
        "dominantSignalThreshold": args.dominant_fraction,
        "minimumDayCoverageFraction": args.day_coverage_fraction,
        "validSnapshotPoints": len(points),
        "days": rows,
        "guardrails": {
            "usesAdminData": False,
            "usesOnlyCausallyAvailableRadarState": True,
            "dailyAggregateCanEstablishOrderLevelCausality": False,
            "mixedDaysExcludedFromPrimaryComparison": True,
            "poorCoverageDaysExcludedFromPrimaryComparison": True,
            "outcomeDataMustUseCompletedOrders": True,
            "rewardEventsMustNotBeAssumedToEqualWinningOrders": True,
            "currentProductionModelChanged": False,
            "canRaiseSignal": False,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("PALLADIUM M SIGNAL EXPOSURE RESEARCH ONLY")
    print("Valid snapshot points:", len(points))
    print("Days:", len(rows))
    print("Aggregate-eligible days:", sum(1 for row in rows if row["aggregateEligible"]))
    print("Current production model changed: NO")


if __name__ == "__main__":
    main()
