import json
import os
import urllib.request
from datetime import datetime, timezone

URL = "https://api2.nicehash.com/hashpower/api/v2/public/solo/singleReward?limit=50&page=0"
OUTPUT_FILE = "calibration/realized-blocks.jsonl"

req = urllib.request.Request(
URL,
headers={"User-Agent": "nicehash-radar-public/1.0", "Accept": "application/json"}
)

with urllib.request.urlopen(req, timeout=30) as response:
data = json.loads(response.read().decode("utf-8"))

if isinstance(data, list):
blocks = data
elif isinstance(data, dict) and isinstance(data.get("list"), list):
blocks = data["list"]
elif isinstance(data, dict) and isinstance(data.get("data"), list):
blocks = data["data"]
elif isinstance(data, dict) and isinstance(data.get("items"), list):
blocks = data["items"]
else:
raise RuntimeError("Unexpected singleReward response structure: " + str(type(data)))

existing = set()

if os.path.exists(OUTPUT_FILE):
with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
for line in f:
if line.strip():
old = json.loads(line)
existing.add(str(old.get("coin")) + ":" + str(old.get("blockHeight")))

new_count = 0

with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
for block in blocks:
coin = block.get("coin")
height = block.get("blockHeight")

if coin is None or height is None:
continue

key = str(coin) + ":" + str(height)

if key in existing:
continue

record = {
"collected_at": datetime.now(timezone.utc).isoformat(),
"coin": coin,
"blockHeight": height,
"blockHash": block.get("blockHash"),
"payoutReward": block.get("payoutReward"),
"payoutRewardBtc": block.get("payoutRewardBtc"),
"time": block.get("time"),
"createdTs": block.get("createdTs"),
"packageId": block.get("packageId"),
"packageName": block.get("packageName"),
"shared": block.get("shared")
}

f.write(json.dumps(record, separators=(",", ":")) + "\n")
existing.add(key)
new_count += 1

print("Blocks received:", len(blocks))
print("New blocks saved:", new_count)
print("Output:", OUTPUT_FILE)
