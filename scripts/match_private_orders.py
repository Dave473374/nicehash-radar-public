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
"closeToRewardPct": order.get("soloMiningSharesMaxPercent"),
"rewardCount": len(order.get("soloMiningRewards") or []),
"hadReward": len(order.get("soloMiningRewards") or []) > 0,
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

result = {
"completedOrders": len(orders),
"radarSnapshots": len(snapshots),
"validMatchedOrders": len(matches),
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
print("Private Radar calibration matcher completed successfully")
