import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

GLOBAL_MATCHES = Path(
    os.getenv(
        "GLOBAL_MATCH_INPUT",
        "/tmp/global-order-matches.json",
    )
)
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
EVENT_MATCHES = Path("calibration/mining-event-matches.jsonl")
OUTPUT = Path("calibration/daily-package-stats.json")

SIGNAL_SCORE = {
    "NO BUY": 0,
    "WAIT": 1,
    "GOOD": 2,
    "BUY NOW": 3,
    "STRONG BUY": 4,
}
SCORE_SIGNAL = {value: key for key, value in SIGNAL_SCORE.items()}


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None


def utc_date_from_ms(value):
    try:
        return datetime.fromtimestamp(
            float(value) / 1000,
            tz=timezone.utc,
        ).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def mean_numeric(rows, key):
    values = [
        float(row[key])
        for row in rows
        if isinstance(row.get(key), (int, float))
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def signal_summary(rows, key):
    signals = [
        str(row.get(key))
        for row in rows
        if row.get(key) in SIGNAL_SCORE
    ]
    counts = Counter(signals)

    if not signals:
        return {
            "counts": {},
            "dominant": None,
            "averageScore": None,
            "averageApprox": None,
            "scoreScale": SIGNAL_SCORE,
        }

    average = sum(
        SIGNAL_SCORE[signal]
        for signal in signals
    ) / len(signals)
    rounded = max(
        min(int(round(average)), max(SCORE_SIGNAL)),
        min(SCORE_SIGNAL),
    )

    dominant = sorted(
        counts.items(),
        key=lambda item: (-item[1], -SIGNAL_SCORE[item[0]]),
    )[0][0]

    return {
        "counts": dict(counts),
        "dominant": dominant,
        "averageScore": round(average, 4),
        "averageApprox": SCORE_SIGNAL[rounded],
        "scoreScale": SIGNAL_SCORE,
    }


def load_global_matches():
    if not GLOBAL_MATCHES.exists():
        raise SystemExit(
            f"Global order matches missing: {GLOBAL_MATCHES}"
        )

    data = json.loads(
        GLOBAL_MATCHES.read_text(encoding="utf-8")
    )
    rows = data.get("matches") or []
    if not isinstance(rows, list):
        raise SystemExit("Global matches payload is invalid")
    return rows


def load_jsonl(path):
    if not path.exists():
        return []

    rows = []
    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


global_rows = load_global_matches()
radar_rows = load_jsonl(RADAR_HISTORY)
event_rows = load_jsonl(EVENT_MATCHES)

orders_by_key = defaultdict(list)
radar_by_key = defaultdict(list)
events_by_key = defaultdict(list)
keys = set()

for row in global_rows:
    ts = parse_iso(row.get("orderStartTs"))
    package = row.get("packageName")
    if ts is None or not package:
        continue
    key = (ts.date().isoformat(), str(package))
    orders_by_key[key].append(row)
    keys.add(key)

for snapshot in radar_rows:
    ts = parse_iso(snapshot.get("collected_at"))
    if ts is None:
        continue

    for package in (
        snapshot.get("feed", {}).get("packages") or []
    ):
        name = package.get("name")
        if not name or str(name).startswith("Team "):
            continue

        key = (ts.date().isoformat(), str(name))
        radar_by_key[key].append(
            {
                "finalSignal": package.get("final_signal"),
                "expectedReturnPercent": (
                    package.get("profitability") or {}
                ).get("expected_return_percent"),
                "modelHitProbabilityPercent": (
                    package.get("primary_chain") or {}
                ).get("model_hit_probability_percent"),
                "qualityVs24hPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_24h_percent"
                ),
                "qualityVs7dPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_7d_percent"
                ),
            }
        )
        keys.add(key)

for row in event_rows:
    if row.get("radar_matched") is not True:
        continue

    date = utc_date_from_ms(row.get("event_time"))
    package = row.get("package_name")
    if not date or not package:
        continue

    key = (date, str(package))
    events_by_key[key].append(row)
    keys.add(key)

output_rows = []

for date, package in sorted(
    keys,
    key=lambda item: (item[0], item[1]),
    reverse=True,
):
    orders = orders_by_key.get((date, package), [])
    radar = radar_by_key.get((date, package), [])
    events = events_by_key.get((date, package), [])

    hits = sum(
        1 for row in orders
        if row.get("hadReward") is True
    )
    misses = len(orders) - hits
    actual_hit_rate = (
        round(hits / len(orders) * 100, 4)
        if orders
        else None
    )

    probabilities = [
        float(row["modelHitProbabilityPercent"])
        for row in orders
        if isinstance(
            row.get("modelHitProbabilityPercent"),
            (int, float),
        )
    ]
    modeled_hit_rate = (
        round(sum(probabilities) / len(probabilities), 4)
        if probabilities
        else None
    )
    expected_hits = (
        round(sum(probabilities) / 100, 4)
        if probabilities
        else None
    )

    total_payout = round(
        sum(
            float(row.get("total_payout_reward_btc") or 0)
            for row in events
        ),
        12,
    )

    output_rows.append(
        {
            "dateUtc": date,
            "packageName": package,
            "globalOrders": {
                "orders": len(orders),
                "soldQuantity": len(orders),
                "hits": hits,
                "misses": misses,
                "actualHitRatePercent": actual_hit_rate,
                "modeledHitRatePercentAtEntry": modeled_hit_rate,
                "hitRateDeviationPctPoints": (
                    round(
                        actual_hit_rate - modeled_hit_rate,
                        4,
                    )
                    if actual_hit_rate is not None
                    and modeled_hit_rate is not None
                    else None
                ),
                "expectedHitsFromModel": expected_hits,
                "actualMinusExpectedHits": (
                    round(hits - expected_hits, 4)
                    if expected_hits is not None
                    else None
                ),
                "averageExpectedReturnPercent": mean_numeric(
                    orders,
                    "expectedReturnPercent",
                ),
                "averageQualityVs24hPercent": mean_numeric(
                    orders,
                    "qualityVs24hPercent",
                ),
                "averageQualityVs7dPercent": mean_numeric(
                    orders,
                    "qualityVs7dPercent",
                ),
                "signalAtEntry": signal_summary(
                    orders,
                    "finalSignal",
                ),
                "roiStatus": (
                    "UNAVAILABLE_FROM_GLOBAL_HIT_MISS_SOURCE"
                ),
            },
            "radarObservations": {
                "snapshotsObserved": len(radar),
                "averageModeledHitProbabilityPercent": mean_numeric(
                    radar,
                    "modelHitProbabilityPercent",
                ),
                "averageExpectedReturnPercent": mean_numeric(
                    radar,
                    "expectedReturnPercent",
                ),
                "averageQualityVs24hPercent": mean_numeric(
                    radar,
                    "qualityVs24hPercent",
                ),
                "averageQualityVs7dPercent": mean_numeric(
                    radar,
                    "qualityVs7dPercent",
                ),
                "signals": signal_summary(
                    radar,
                    "finalSignal",
                ),
            },
            "eventEvidence": {
                "successfulMiningEvents": len(events),
                "mergedMiningEvents": sum(
                    1 for row in events
                    if row.get("merged_mining") is True
                ),
                "totalPayoutRewardBtc": total_payout,
                "averagePayoutRewardBtc": (
                    round(total_payout / len(events), 12)
                    if events
                    else None
                ),
                "note": (
                    "Event evidence is a successful-event numerator only "
                    "and is never used as the completed-order denominator."
                ),
            },
        }
    )

report = {
    "reportVersion": 1,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "source": "AGGREGATE_GLOBAL_ORDER_AND_EVENT_CALIBRATION",
    "privacy": {
        "individualGlobalOrdersStored": False,
        "privateUserOrdersStored": False,
        "customerIdentifiersStored": False,
        "aggregateOnly": True,
    },
    "denominatorPolicy": {
        "hitMissDenominator": "GLOBAL_COMPLETED_EASYMINING_ORDERS",
        "eventEvidenceRole": "SUCCESSFUL_EVENT_NUMERATOR_ONLY",
        "privateRoiRole": (
            "Computed only in private temporary calibration paths; "
            "never written to this public report."
        ),
    },
    "rowCount": len(output_rows),
    "rows": output_rows,
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
    ) + "\n",
    encoding="utf-8",
)

print("DAILY PACKAGE STATS")
print("Rows:", len(output_rows))
print("Global matched orders:", len(global_rows))
print(
    "Matched event evidence:",
    sum(len(value) for value in events_by_key.values()),
)
print("Output:", OUTPUT)
