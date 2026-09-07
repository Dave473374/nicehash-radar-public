import json
from pathlib import Path
from datetime import datetime

PRIVATE_ORDERS = Path("/tmp/nicehash-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/private-order-matches.json")

MAX_SNAPSHOT_AGE_SECONDS = 2 * 60 * 60

parse_ts = lambda x: datetime.fromisoformat(
str(x).replace("Z", "+00:00")
) if x else None

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

matches = []

for order in orders:
order_time = parse_ts(order.get("startTs"))

if order_time is None:
continue

candidates = [
(ts, snap)
for ts, snap in snapshot_points
if ts <= order_time
and (order_time - ts).total_seconds() <= MAX_SNAPSHOT_AGE_SECONDS
]

if not candidates:
continue

best_time, best_snapshot = max(
candidates,
key=lambda x: x[0]
)

package = next(
(
p
for p in (best_snapshot.get("feed", {}).get("packages") or [])
if p.get("name") == order.get("packageName")
and (p.get("primary_chain") or {}).get("currency")
== order.get("soloMiningCoin")
),
None
)

if package is None:
continue

rewards = order.get("soloMiningRewards") or []

matches.append({
"orderStartTs": order.get("startTs"),
"orderEndTs": order.get("endTs"),
"packageName": order.get("packageName"),
"coin": order.get("soloMiningCoin"),
"packagePriceBtc": order.get("packagePrice"),
"closeToRewardPct": order.get("soloMiningSharesMaxPercent"),
"rewardCount": len(rewards),
"hadReward": len(rewards) > 0,
"snapshotCollectedAt": best_snapshot.get("collected_at"),
"snapshotAgeMinutes": round(
(order_time - best_time).total_seconds() / 60,
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
})

result = {
"completedOrders": len(orders),
"radarSnapshots": len(snapshots),
"validMatchedOrders": len(matches),
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
print("Private Radar calibration matcher completed successfully")
