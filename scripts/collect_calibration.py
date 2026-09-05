import json
import os
from datetime import datetime, timezone

SOURCE_FILE="buy-feed.json"
OUTPUT_DIR="calibration"
OUTPUT_FILE=os.path.join(OUTPUT_DIR,"radar-snapshots.jsonl")

feed=json.load(open(SOURCE_FILE,"r",encoding="utf-8"))

assert feed.get("relay_version")=="2.8.2",f"Wrong relay_version: {feed.get('relay_version')}"
assert feed.get("status")=="BUY FEED OK",f"Bad status: {feed.get('status')}"
assert feed.get("ok") is True,"Feed ok is not true"
assert feed.get("upstream_status")==200,f"Bad upstream_status: {feed.get('upstream_status')}"
assert feed.get("market_status")=="MARKET OK",f"Bad market_status: {feed.get('market_status')}"

snapshot={
"collected_at":datetime.now(timezone.utc).isoformat(),
"feed_generated_at":feed.get("generated_at") or feed.get("timestamp") or feed.get("updated_at"),
"relay_version":feed.get("relay_version"),
"decision_engine":feed.get("decision_engine"),
"feed":feed
}

os.makedirs(OUTPUT_DIR,exist_ok=True)

open(OUTPUT_FILE,"a",encoding="utf-8").write(json.dumps(snapshot,separators=(",",":"),ensure_ascii=False)+"\n")

print("Calibration snapshot created successfully")
