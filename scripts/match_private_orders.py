import json
from pathlib import Path
from datetime import datetime

PRIVATE_ORDERS = Path("/tmp/nicehash-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")

parse_ts = lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00")) if x else None

orders = json.loads(
PRIVATE_ORDERS.read_text(encoding="utf-8")
).get("list", [])

snapshots = [
json.loads(line)
for line in RADAR_HISTORY.read_text(encoding="utf-8").splitlines()
if line.strip()
]

order_times = [
parse_ts(x.get("startTs"))
for x in orders
if parse_ts(x.get("startTs")) is not None
]

snapshot_times = [
parse_ts(x.get("collected_at"))
for x in snapshots
if parse_ts(x.get("collected_at")) is not None
]

print("Completed orders:", len(orders))
print("Radar snapshots:", len(snapshots))

print(
"Oldest completed order:",
min(order_times).isoformat() if order_times else "NONE"
)

print(
"Newest completed order:",
max(order_times).isoformat() if order_times else "NONE"
)

print(
"Oldest radar snapshot:",
min(snapshot_times).isoformat() if snapshot_times else "NONE"
)

print(
"Newest radar snapshot:",
max(snapshot_times).isoformat() if snapshot_times else "NONE"
)

orders_inside_radar_range = [
x for x in order_times
if snapshot_times
and min(snapshot_times) <= x <= max(snapshot_times)
]

print(
"Orders inside Radar time range:",
len(orders_inside_radar_range)
)

print("Diagnostic completed successfully")
