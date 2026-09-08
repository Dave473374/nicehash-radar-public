import json
import urllib.request
from pathlib import Path

URL = "https://api2.nicehash.com/hashpower/api/v2/public/solo/singleReward?limit=100&page=0"
LATEST = Path("recent-blocks.json")
HISTORY = Path("recent-blocks-history.json")

request = urllib.request.Request(
URL,
headers={
"User-Agent": "Mozilla/5.0 NiceHash-Radar/1.0",
"Accept": "application/json"
}
)

response = urllib.request.urlopen(request, timeout=30)
data = json.loads(response.read().decode("utf-8"))

latest = [
{
"coin": item.get("coin"),
"blockHeight": item.get("blockHeight"),
"blockHash": item.get("blockHash"),
"payoutReward": item.get("payoutReward"),
"payoutRewardBtc": item.get("payoutRewardBtc"),
"time": item.get("time"),
"createdTs": item.get("createdTs"),
"packageId": item.get("packageId"),
"packageName": item.get("packageName"),
"shared": item.get("shared")
}
for item in data
]

old_history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []

combined = old_history + latest

unique = {
str(item.get("coin")) + "|" + str(item.get("blockHash")) + "|" + str(item.get("packageId")): item
for item in combined
}

history = sorted(
unique.values(),
key=lambda item: item.get("time") or 0,
reverse=True
)

LATEST.write_text(
json.dumps(latest, indent=2, ensure_ascii=False),
encoding="utf-8"
)

HISTORY.write_text(
json.dumps(history, indent=2, ensure_ascii=False),
encoding="utf-8"
)

print("Latest rewards:", len(latest))
print("Total historical rewards:", len(history))
