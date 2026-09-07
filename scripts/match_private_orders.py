import json
from pathlib import Path
from datetime import datetime, timezone

PRIVATE_ORDERS = Path("/tmp/nicehash-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/private-order-matches.json")

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

matches = [
{
"orderStartTs": order.get("startTs"),
"orderEndTs": order.get("endTs"),
"packageName": order.get("packageName"),
"coin": order.get("soloMiningCoin"),
"packagePriceBtc": order.get("packagePrice"),
"isReward": bool(order.get("isReward")),
"closeToRewardPct": order.get("soloMiningSharesMaxPercent"),
"snapshotCollectedAt": best_snapshot.get("collected_at"),
"snapshotAgeMinutes": round((order_time - best_time).total_seconds() / 60, 2),
"radarPriceBtc": package.get("price_btc"),
"miningSignal": package.get("mining_signal"),
"economicSignal": package.get("economic_signal"),
"finalSignal": package.get("final_signal"),
"expectedReturnPercent": (package.get("profitability") or {}).get("expected_return_percent"),
"qualityVs24hPercent": (package.get("history_trend") or {}).get("expected_blocks_per_btc_vs_24h_percent")
}
for order in orders
if (order_time := parse_ts(order.get("startTs"))) is not None
if (candidates := [
(ts, snap)
for ts, snap in snapshot_points
if ts <= order_time and (order_time - ts).total_seconds() <= 7200
])
if (best := max(candidates, key=lambda x: x[0]))
if (best_time := best[0])
if (best_snapshot := best[1])
if (package := next(
(
p for p in (best_snapshot.get("feed", {}).get("packages") or [])
if p.get("name") == order.get("packageName")
and (p.get("primary_chain") or {}).get("currency") == order.get("soloMiningCoin")
),
None
)) is not None
]

result = {
"completedOrders": len(orders),
"matchedOrders": len(matches),
"unmatchedOrders": len(orders) - len(matches),
"matches": matches
}

OUTPUT.write_text(
json.dumps(result, separators=(",", ":"), ensure_ascii=False),
encoding="utf-8"
)

print("Completed orders:", len(orders))
print("Matched to Radar:", len(matches))
print("Unmatched:", len(orders) - len(matches))
print("Reward matches:", sum(1 for x in matches if x.get("isReward")))
print("Private matcher completed successfully")
