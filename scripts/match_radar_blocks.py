import json
from datetime import datetime, timezone

SNAPSHOTS_FILE = "calibration/radar-snapshots.jsonl"
BLOCKS_FILE = "calibration/realized-blocks.jsonl"
OUTPUT_FILE = "calibration/radar-block-matches.jsonl"

snapshots = [json.loads(x) for x in open(SNAPSHOTS_FILE, encoding="utf-8") if x.strip()]
blocks = [json.loads(x) for x in open(BLOCKS_FILE, encoding="utf-8") if x.strip()]

snapshot_times = [(datetime.fromisoformat(s["collected_at"].replace("Z", "+00:00")), s) for s in snapshots]
block_times = [(datetime.fromisoformat(b["createdTs"].replace("Z", "+00:00")), b) for b in blocks if b.get("createdTs")]

matches = [{"block": b, "radar_snapshot": max((s for t, s in snapshot_times if t <= bt), key=lambda s: datetime.fromisoformat(s["collected_at"].replace("Z", "+00:00")), default=None)} for bt, b in block_times]

matched = [m for m in matches if m["radar_snapshot"] is not None]

open(OUTPUT_FILE, "w", encoding="utf-8").write("".join(json.dumps(m, separators=(",", ":")) + "\n" for m in matched))

print("Radar snapshots:", len(snapshots))
print("Realized blocks:", len(blocks))
print("Matched blocks:", len(matched))
print("Output:", OUTPUT_FILE)
