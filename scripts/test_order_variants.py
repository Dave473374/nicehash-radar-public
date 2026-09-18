import json
from pathlib import Path

path = Path("/tmp/nicehash-completed-orders.json")
data = json.loads(path.read_text(encoding="utf-8"))

orders = data.get("list")
pages = data.get("pageSummaries")

assert isinstance(orders, list)
assert isinstance(pages, list)

for order in orders:
    assert isinstance(order, dict)
    assert "startTs" in order
    assert "packageName" in order
    assert "isReward" in order
    assert "soloReward" in order

print("PRIVATE EASYMINING SAFE STRUCTURE OK")
print("No order bodies, HIT/MISS counts, ROI, timestamps, or payouts printed")
