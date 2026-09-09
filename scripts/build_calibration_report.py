import json
from collections import Counter, defaultdict

INPUT_FILE = "calibration/mining-event-matches.jsonl"
OUTPUT_FILE = "calibration/calibration-report.json"

all_rows = [
json.loads(x)
for x in open(INPUT_FILE, "r", encoding="utf-8")
if x.strip()
]

rows = [
r
for r in all_rows
if r.get("radar_matched") is True
]

signals = Counter(
str(r.get("final_signal") or "UNKNOWN")
for r in rows
)

coins = Counter(
str(r.get("primary_coin") or "UNKNOWN")
for r in rows
)

packages = Counter(
str(r.get("package_name") or "UNKNOWN")
for r in rows
)

signal_rewards = defaultdict(list)

[
signal_rewards[
str(r.get("final_signal") or "UNKNOWN")
].append(
float(r.get("total_payout_reward_btc") or 0)
)
for r in rows
]

signal_stats = {
signal: {
"matched_events": signals[signal],
"total_payout_reward_btc": round(sum(signal_rewards[signal]), 12),
"avg_payout_reward_btc": round(
sum(signal_rewards[signal]) / len(signal_rewards[signal]),
12
)
if signal_rewards[signal]
else 0
}
for signal in signals
}

coin_signal = Counter(
(
str(r.get("primary_coin") or "UNKNOWN"),
str(r.get("final_signal") or "UNKNOWN")
)
for r in rows
)

package_signal = Counter(
(
str(r.get("package_name") or "UNKNOWN"),
str(r.get("final_signal") or "UNKNOWN")
)
for r in rows
)

report = {
"source": "EVENT_LEVEL_MINING_CALIBRATION",
"events_total": len(all_rows),
"matched_events_total": len(rows),
"unmatched_events_total": sum(
r.get("radar_matched") is not True
for r in all_rows
),
"merged_events_total": sum(
r.get("merged_mining") is True
for r in all_rows
),
"merged_matched_events_total": sum(
r.get("merged_mining") is True
and r.get("radar_matched") is True
for r in all_rows
),
"important_note": "Calibration is event-level. One EasyMining mining event counts once. Merged LTC+DOGE rewards from the same event are not counted as separate successful events. Only events with an exact radar package match are included in signal calibration.",
"signals": signal_stats,
"coins": dict(coins),
"packages": dict(packages),
"coin_by_signal": {
coin + "|" + signal: count
for (coin, signal), count in coin_signal.items()
},
"package_by_signal": {
package + "|" + signal: count
for (package, signal), count in package_signal.items()
}
}

open(
OUTPUT_FILE,
"w",
encoding="utf-8"
).write(
json.dumps(
report,
indent=2,
sort_keys=True
)
)

print("All mining events:", len(all_rows))
print("Radar matched events:", len(rows))
print("Unmatched events:", report["unmatched_events_total"])
print("Merged events:", report["merged_events_total"])
print("Merged radar matches:", report["merged_matched_events_total"])
print("Signals:", dict(signals))
print("Coins:", dict(coins))
print("Packages:", dict(packages))
print("Output:", OUTPUT_FILE)
