import json
from pathlib import Path
from itertools import groupby

SOURCE = Path("recent-blocks.json")
OUTPUT = Path("mining-events.json")

rewards = json.loads(SOURCE.read_text(encoding="utf-8"))

rewards = sorted(
rewards,
key=lambda x: (
str(x.get("packageId")),
x.get("time") or 0
)
)

groups = groupby(
rewards,
key=lambda x: (
str(x.get("packageId")),
x.get("time") or 0
)
)

events = [
{
"eventId": str(key[0]) + "|" + str(key[1]),
"packageId": key[0],
"time": key[1],
"packageName": items[0].get("packageName"),
"coins": [x.get("coin") for x in items],
"rewards": items,
"rewardCount": len(items),
"mergedMining": set(x.get("coin") for x in items) >= {"LTC", "DOGE"},
"totalPayoutRewardBtc": sum(float(x.get("payoutRewardBtc") or 0) for x in items)
}
for key, group in groups
for items in [list(group)]
]

events = sorted(
events,
key=lambda x: x.get("time") or 0,
reverse=True
)

OUTPUT.write_text(
json.dumps(events, indent=2, ensure_ascii=False),
encoding="utf-8"
)

print("Raw reward records:", len(rewards))
print("Mining events:", len(events))
print("Merged LTC+DOGE events:", sum(x.get("mergedMining") is True for x in events))
