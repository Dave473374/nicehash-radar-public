import json
from pathlib import Path

path = Path("/tmp/nicehash-completed-orders.json")

data = json.loads(path.read_text(encoding="utf-8"))
orders = data.get("list", [])

reward_orders = [x for x in orders if bool(x.get("isReward"))]

print("TOTAL ORDERS", len(orders))
print("REWARD ORDERS", len(reward_orders))

if reward_orders:
    order = reward_orders[0]

    interesting = {
        k: v
        for k, v in order.items()
        if any(word in k.lower() for word in [
            "reward",
            "amount",
            "payout",
            "pay",
            "price",
            "coin"
        ])
    }

    print("REWARD ORDER INTERESTING FIELDS")
    print(json.dumps(interesting, indent=2, ensure_ascii=False))
else:
    print("NO REWARD ORDER FOUND")
