import json
from datetime import datetime, timezone

SNAPSHOTS_FILE = "calibration/radar-snapshots.jsonl"
EVENTS_FILE = "mining-events.json"
OUTPUT_FILE = "calibration/mining-event-matches.jsonl"

snapshots = [
json.loads(x)
for x in open(SNAPSHOTS_FILE, encoding="utf-8")
if x.strip()
]

events = json.load(
open(EVENTS_FILE, encoding="utf-8")
)

parse_time = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))

snapshot_times = [
(parse_time(s["collected_at"]), s)
for s in snapshots
]

event_time = lambda e: datetime.fromtimestamp(
(e.get("time") or 0) / 1000,
tz=timezone.utc
)

latest_snapshot = lambda t: max(
((st, s) for st, s in snapshot_times if st <= t),
default=(None, None),
key=lambda x: x[0] if x[0] is not None else datetime.min.replace(tzinfo=timezone.utc)
)[1]

package_for = lambda s, name: next(
(
p
for p in s.get("feed", {}).get("packages", [])
if p.get("name") == name
),
None
)

pairs = [
(e, latest_snapshot(event_time(e)))
for e in events
if e.get("time")
]

triples = [
(e, s, package_for(s, e.get("packageName")))
for e, s in pairs
if s is not None
]

triples = [
(e, s, p)
for e, s, p in triples
if p is not None
]

records = [
{
"event_id": e.get("eventId"),
"event_time": e.get("time"),
"package_id": e.get("packageId"),
"package_name": e.get("packageName"),
"coins": e.get("coins"),
"reward_count": e.get("rewardCount"),
"merged_mining": e.get("mergedMining"),
"total_payout_reward_btc": e.get("totalPayoutRewardBtc"),

"snapshot_time": s.get("collected_at"),
"snapshot_age_seconds": round(
(event_time(e) - parse_time(s.get("collected_at"))).total_seconds(),
1
),

"price_btc": p.get("price_btc"),
"primary_coin": p.get("primary_chain", {}).get("currency"),
"merge_coin": p.get("merge_chain", {}).get("currency")
if p.get("merge_chain")
else None,

"mining_signal": p.get("mining_signal"),
"economic_signal": p.get("economic_signal"),
"final_signal": p.get("final_signal"),
"decision": p.get("decision"),

"expected_blocks": p.get("primary_chain", {}).get("expected_blocks"),
"model_hit_probability_percent": p.get("primary_chain", {}).get("model_hit_probability_percent"),
"nicehash_odds": p.get("nicehash_odds", {}).get("display"),

"expected_reward_btc_equiv": p.get("profitability", {}).get("expected_reward_btc_equiv"),
"expected_return_percent": p.get("profitability", {}).get("expected_return_percent"),
"profitability_margin_percent": p.get("profitability", {}).get("profitability_margin_percent"),

"break_even_block_target": p.get("profitability", {}).get("break_even_block_target"),
"break_even_multiple_vs_expected_blocks": p.get("profitability", {}).get("break_even_multiple_vs_expected_blocks"),
"break_even_probability_percent": p.get("profitability", {}).get("break_even_probability_percent_approx"),
"break_even_risk": p.get("profitability", {}).get("break_even_risk", {}).get("status"),
"break_even_cap": p.get("profitability", {}).get("break_even_risk", {}).get("max_final_signal"),

"quality_vs_24h_percent": p.get("history_trend", {}).get("expected_blocks_per_btc_vs_24h_percent"),
"quality_vs_7d_percent": p.get("history_trend", {}).get("expected_blocks_per_btc_vs_7d_percent"),
"history_samples_24h": p.get("history_trend", {}).get("sample_count_24h"),
"history_samples_7d": p.get("history_trend", {}).get("sample_count_7d")
}
for e, s, p in triples
]

open(
OUTPUT_FILE,
"w",
encoding="utf-8"
).write(
"".join(
json.dumps(r, separators=(",", ":")) + "\n"
for r in records
)
)

print("Radar snapshots:", len(snapshots))
print("Mining events:", len(events))
print("Matched mining events:", len(records))
print("Merged mining matches:", sum(r.get("merged_mining") is True for r in records))
print("Output:", OUTPUT_FILE)
