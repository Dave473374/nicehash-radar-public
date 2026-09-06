import json
import os
import urllib.request
from datetime import datetime, timezone

URL = "https://api2.nicehash.com/hashpower/api/v2/public/solo/singleReward?limit=50&page=0"
OUTPUT_FILE = "calibration/realized-blocks.jsonl"

req = urllib.request.Request(URL, headers={"User-Agent": "nicehash-radar-public/1.0", "Accept": "application/json"})
response = urllib.request.urlopen(req, timeout=30)
data = json.loads(response.read().decode("utf-8"))
response.close()

blocks = data if isinstance(data, list) else data.get("list") if isinstance(data, dict) and isinstance(data.get("list"), list) else data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else None
assert isinstance(blocks, list), "Unexpected singleReward response structure"

os.makedirs("calibration", exist_ok=True)

old_lines = open(OUTPUT_FILE, "r", encoding="utf-8").read().splitlines() if os.path.exists(OUTPUT_FILE) else []
old_records = [json.loads(line) for line in old_lines if line.strip()]
existing = {str(x.get("coin")) + ":" + str(x.get("blockHeight")) for x in old_records}

candidates = {str(b.get("coin")) + ":" + str(b.get("blockHeight")): b for b in blocks if b.get("coin") is not None and b.get("blockHeight") is not None}
new_blocks = [b for key, b in candidates.items() if key not in existing]

records = [{"collected_at": datetime.now(timezone.utc).isoformat(), "coin": b.get("coin"), "blockHeight": b.get("blockHeight"), "blockHash": b.get("blockHash"), "payoutReward": b.get("payoutReward"), "payoutRewardBtc": b.get("payoutRewardBtc"), "time": b.get("time"), "createdTs": b.get("createdTs"), "packageId": b.get("packageId"), "packageName": b.get("packageName"), "shared": b.get("shared")} for b in new_blocks]

open(OUTPUT_FILE, "a", encoding="utf-8").write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))

print("Blocks received:", len(blocks))
print("New blocks saved:", len(records))
print("Output:", OUTPUT_FILE)
