import json
from pathlib import Path
from datetime import datetime

PRIVATE_ORDERS = Path("/tmp/nicehash-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/private-order-matches.json")

MAX_SNAPSHOT_AGE_SECONDS = 7200

parse_ts = lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00")) if x else None

orders = json.loads(PRIVATE_ORDERS.read_text(encoding="utf-8")).get("list", [])

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
            "packagePriceBtc": order.get("packagePrice"),
            "payedAmountBtc": order.get("payedAmount"),
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

matches = [
    match
    for order in orders
    if parse_ts(order.get("startTs")) is not None
    if (match := make_match(order)) is not None
]

signals = ["STRONG BUY", "BUY NOW", "GOOD", "WAIT", "NO BUY"]

signal_stats = [
    {
        "signal": signal,
        "orders": len(rows),
        "hits": sum(1 for x in rows if x.get("hadReward")),
        "misses": sum(1 for x in rows if not x.get("hadReward")),
        "hitRatePercent": round(
            100 * sum(1 for x in rows if x.get("hadReward")) / len(rows), 2
        ) if rows else None,
        "averageExpectedReturnPercent": round(
            sum(
                x.get("expectedReturnPercent")
                for x in rows
                if isinstance(x.get("expectedReturnPercent"), (int, float))
            )
            /
            len([
                x for x in rows
                if isinstance(x.get("expectedReturnPercent"), (int, float))
            ]),
            2
        ) if any(
            isinstance(x.get("expectedReturnPercent"), (int, float))
            for x in rows
        ) else None
    }
    for signal in signals
    if (rows := [x for x in matches if x.get("finalSignal") == signal])
]

summary = [
    {
        "package": x.get("packageName"),
        "coin": x.get("coin"),
        "finalSignal": x.get("finalSignal"),
        "economicSignal": x.get("economicSignal"),
        "expectedReturnPercent": x.get("expectedReturnPercent"),
        "snapshotAgeMinutes": x.get("snapshotAgeMinutes"),
        "closeToRewardPct": x.get("closeToRewardPct"),
        "outcome": "HIT" if x.get("hadReward") else "MISS"
    }
    for x in matches
]

result = {
    "completedOrders": len(orders),
    "radarSnapshots": len(snapshots),
    "validMatchedOrders": len(matches),
    "matchedRewards": sum(1 for x in matches if x.get("hadReward")),
    "signalStats": signal_stats,
    "matches": matches
}

OUTPUT.write_text(
    json.dumps(result, separators=(",", ":"), ensure_ascii=False),
    encoding="utf-8"
)

print("Completed orders:", len(orders))
print("Radar snapshots:", len(snapshots))
print("Valid matched orders:", len(matches))
print("Matched rewards:", sum(1 for x in matches if x.get("hadReward")))

print("MATCHED ORDER SUMMARY")
print(json.dumps(summary, indent=2, ensure_ascii=False))

print("SIGNAL CALIBRATION SUMMARY")
print(json.dumps(signal_stats, indent=2, ensure_ascii=False))

print("Private Radar calibration matcher completed successfully")
