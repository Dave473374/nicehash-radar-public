import json
from pathlib import Path
from datetime import datetime

PRIVATE_ORDERS = Path("/tmp/nicehash-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/private-order-matches.json")

MAX_SNAPSHOT_AGE_SECONDS = 7200

parse_ts = lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00")) if x else None

to_float = lambda x: float(x) if x not in (None, "") else 0.0

orders = json.loads(
    PRIVATE_ORDERS.read_text(encoding="utf-8")
).get("list", [])

snapshots = [
    json.loads(line)
    for line in RADAR_HISTORY.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

snapshot_points = [
    (parse_ts(s.get("collected_at")), s)
    for s in snapshots
    if parse_ts(s.get("collected_at")) is not None
]

make_match = lambda order: next(
    (
        {
            "orderStartTs": order.get("startTs"),
            "orderEndTs": order.get("endTs"),
            "packageName": order.get("packageName"),
            "coin": order.get("soloMiningCoin"),

            "packagePriceBtc": to_float(order.get("packagePrice")),
            "actualCostBtc": (
                to_float(order.get("payedAmount"))
                if to_float(order.get("payedAmount")) > 0
                else to_float(order.get("packagePrice"))
            ),

            "realizedReturnBtc": sum(
                to_float(reward.get("payoutRewardBtc"))
                for reward in (order.get("soloReward") or [])
                if isinstance(reward, dict)
            ),

            "closeToRewardPct": order.get("soloMiningSharesMaxPercent"),
            "hadReward": bool(order.get("isReward")),

            "snapshotCollectedAt": snap.get("collected_at"),
            "snapshotAgeMinutes": round(
                (parse_ts(order.get("startTs")) - ts).total_seconds() / 60,
                2
            ),

            "radarPriceBtc": package.get("price_btc"),
            "miningSignal": package.get("mining_signal"),
            "economicSignal": package.get("economic_signal"),
            "finalSignal": package.get("final_signal"),

            "expectedReturnPercent": (
                package.get("profitability") or {}
            ).get("expected_return_percent"),

            "qualityVs24hPercent": (
                package.get("history_trend") or {}
            ).get("expected_blocks_per_btc_vs_24h_percent")
        }

        for ts, snap in sorted(
            [
                (ts, snap)
                for ts, snap in snapshot_points
                if ts <= parse_ts(order.get("startTs"))
                and (
                    parse_ts(order.get("startTs")) - ts
                ).total_seconds() <= MAX_SNAPSHOT_AGE_SECONDS
            ],
            key=lambda x: x[0],
            reverse=True
        )

        for package in (snap.get("feed", {}).get("packages") or [])

        if package.get("name") == order.get("packageName")
        and (package.get("primary_chain") or {}).get("currency")
        == order.get("soloMiningCoin")
    ),
    None
)

matches_raw = [
    match
    for order in orders
    if parse_ts(order.get("startTs")) is not None
    if (match := make_match(order)) is not None
]

matches = [
    {
        **x,
        "realizedReturnMultiple": round(
            x["realizedReturnBtc"] / x["actualCostBtc"],
            4
        ) if x["actualCostBtc"] > 0 else None,

        "realizedRoiPercent": round(
            (
                x["realizedReturnBtc"] / x["actualCostBtc"] - 1
            ) * 100,
            2
        ) if x["actualCostBtc"] > 0 else None
    }
    for x in matches_raw
]

signals = ["STRONG BUY", "BUY NOW", "GOOD", "WAIT", "NO BUY"]

signal_stats = [
    {
        "signal": signal,

        "orders": len(rows),

        "hits": sum(
            1 for x in rows
            if x.get("hadReward")
        ),

        "misses": sum(
            1 for x in rows
            if not x.get("hadReward")
        ),

        "hitRatePercent": round(
            100
            * sum(1 for x in rows if x.get("hadReward"))
            / len(rows),
            2
        ) if rows else None,

        "totalCostBtc": round(
            sum(x.get("actualCostBtc", 0) for x in rows),
            8
        ),

        "totalReturnBtc": round(
            sum(x.get("realizedReturnBtc", 0) for x in rows),
            8
        ),

        "realizedReturnMultiple": round(
            sum(x.get("realizedReturnBtc", 0) for x in rows)
            /
            sum(x.get("actualCostBtc", 0) for x in rows),
            4
        ) if sum(x.get("actualCostBtc", 0) for x in rows) > 0 else None,

        "realizedRoiPercent": round(
            (
                sum(x.get("realizedReturnBtc", 0) for x in rows)
                /
                sum(x.get("actualCostBtc", 0) for x in rows)
                - 1
            ) * 100,
            2
        ) if sum(x.get("actualCostBtc", 0) for x in rows) > 0 else None,

        "averageExpectedReturnPercent": round(
            sum(
                x.get("expectedReturnPercent")
                for x in rows
                if isinstance(
                    x.get("expectedReturnPercent"),
                    (int, float)
                )
            )
            /
            len([
                x for x in rows
                if isinstance(
                    x.get("expectedReturnPercent"),
                    (int, float)
                )
            ]),
            2
        ) if any(
            isinstance(
                x.get("expectedReturnPercent"),
                (int, float)
            )
            for x in rows
        ) else None
    }

    for signal in signals

    if (
        rows := [
            x for x in matches
            if x.get("finalSignal") == signal
        ]
    )
]

summary = [
    {
        "package": x.get("packageName"),
        "coin": x.get("coin"),
        "finalSignal": x.get("finalSignal"),
        "economicSignal": x.get("economicSignal"),
        "expectedReturnPercent": x.get("expectedReturnPercent"),
        "actualCostBtc": round(x.get("actualCostBtc", 0), 8),
        "realizedReturnBtc": round(x.get("realizedReturnBtc", 0), 8),
        "realizedReturnMultiple": x.get("realizedReturnMultiple"),
        "realizedRoiPercent": x.get("realizedRoiPercent"),
        "snapshotAgeMinutes": x.get("snapshotAgeMinutes"),
        "closeToRewardPct": x.get("closeToRewardPct"),
        "outcome": "HIT" if x.get("hadReward") else "MISS"
    }
    for x in matches
]

total_cost = sum(
    x.get("actualCostBtc", 0)
    for x in matches
)

total_return = sum(
    x.get("realizedReturnBtc", 0)
    for x in matches
)

overall = {
    "orders": len(matches),
    "hits": sum(
        1 for x in matches
        if x.get("hadReward")
    ),
    "totalCostBtc": round(total_cost, 8),
    "totalReturnBtc": round(total_return, 8),
    "realizedReturnMultiple": round(
        total_return / total_cost,
        4
    ) if total_cost > 0 else None,
    "realizedRoiPercent": round(
        (total_return / total_cost - 1) * 100,
        2
    ) if total_cost > 0 else None
}

result = {
    "completedOrders": len(orders),
    "radarSnapshots": len(snapshots),
    "validMatchedOrders": len(matches),
    "matchedRewards": sum(
        1 for x in matches
        if x.get("hadReward")
    ),
    "overall": overall,
    "signalStats": signal_stats,
    "matches": matches
}

OUTPUT.write_text(
    json.dumps(
        result,
        separators=(",", ":"),
        ensure_ascii=False
    ),
    encoding="utf-8"
)

print("Completed orders:", len(orders))
print("Radar snapshots:", len(snapshots))
print("Valid matched orders:", len(matches))
print(
    "Matched rewards:",
    sum(1 for x in matches if x.get("hadReward"))
)

print("MATCHED ORDER ROI SUMMARY")
print(
    json.dumps(
        summary,
        indent=2,
        ensure_ascii=False
    )
)

print("SIGNAL ROI CALIBRATION")
print(
    json.dumps(
        signal_stats,
        indent=2,
        ensure_ascii=False
    )
)

print("OVERALL MATCHED ROI")
print(
    json.dumps(
        overall,
        indent=2,
        ensure_ascii=False
    )
)

print(
    "Private Radar ROI calibration matcher completed successfully"
)
