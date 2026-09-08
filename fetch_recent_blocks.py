import json
import urllib.request
from pathlib import Path

URL = (
"https://api2.nicehash.com/hashpower/api/v2/public/solo/"
"singleReward?limit=100&page=0"
)

OUTPUT = Path("recent-blocks.json")


def fetch_json(url):
request = urllib.request.Request(
url,
headers={
"User-Agent": "Mozilla/5.0 NiceHash-Radar/1.0",
"Accept": "application/json",
},
)

with urllib.request.urlopen(request, timeout=30) as response:
if response.status != 200:
raise RuntimeError(f"HTTP {response.status}")

return json.loads(response.read().decode("utf-8"))


def main():
data = fetch_json(URL)

if not isinstance(data, list):
raise RuntimeError("Unexpected NiceHash response: expected a list")

cleaned = []

for item in data:
cleaned.append(
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
"shared": item.get("shared"),
}
)

OUTPUT.write_text(
json.dumps(cleaned, indent=2, ensure_ascii=False),
encoding="utf-8",
)

print(f"OK: saved {len(cleaned)} NiceHash rewards to {OUTPUT}")


if __name__ == "__main__":
main()
