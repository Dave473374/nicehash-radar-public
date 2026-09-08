import json
import urllib.request
from pathlib import Path

URL = "https://api2.nicehash.com/hashpower/api/v2/public/solo/singleReward?limit=100&page=0"
OUTPUT = Path("recent-blocks.json")

request = urllib.request.Request(
URL,
headers={
"User-Agent": "Mozilla/5.0 NiceHash-Radar/1.0",
"Accept": "application/json"
}
)

response = urllib.request.urlopen(request, timeout=30)
data = json.loads(response.read().decode("utf-8"))

cleaned = [
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

OUTPUT.write_text(
json.dumps(cleaned, indent=2, ensure_ascii=False),
encoding="utf-8"
)

print("OK: saved", len(cleaned), "NiceHash rewards to", OUTPUT)
