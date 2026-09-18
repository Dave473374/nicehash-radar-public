import json
from pathlib import Path

path = Path("/tmp/nicehash-completed-orders.json")
data = json.loads(path.read_text(encoding="utf-8"))

orders = data.get("list") or []
pages = data.get("pageSummaries") or []

hits = sum(1 for row in orders if row.get("isReward") is True)
misses = sum(1 for row in orders if row.get("isReward") is False)
with_payout = sum(
    1
    for row in orders
    if any(
        reward.get("payoutRewardBtc") not in (None, "")
        for reward in (row.get("soloReward") or [])
        if isinstance(reward, dict)
    )
)

print("PRIVATE EASYMINING SAFE SUMMARY")
print("Pages:", len(pages))
print("Rows by page:", [x.get("rows") for x in pages])
print("Sanitized orders:", len(orders))
print("HIT:", hits)
print("MISS:", misses)
print("Orders with payout field:", with_payout)
print("No raw order bodies printed")
