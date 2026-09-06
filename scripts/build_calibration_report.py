import json
from collections import Counter, defaultdict

INPUT_FILE = "calibration/radar-block-matches.jsonl"
OUTPUT_FILE = "calibration/calibration-report.json"

rows = [json.loads(x) for x in open(INPUT_FILE, "r", encoding="utf-8") if x.strip()]

signals = Counter(str(r.get("final_signal") or "UNKNOWN") for r in rows)
coins = Counter(str(r.get("block_coin") or "UNKNOWN") for r in rows)
packages = Counter(str(r.get("package_name") or "UNKNOWN") for r in rows)

signal_rewards = defaultdict(list)
[(signal_rewards[str(r.get("final_signal") or "UNKNOWN")].append(float(r.get("block_reward_btc") or 0))) for r in rows]

signal_stats = {
signal: {
"matched_blocks": signals[signal],
"total_reward_btc": round(sum(signal_rewards[signal]), 12),
"avg_reward_btc": round(sum(signal_rewards[signal]) / len(signal_rewards[signal]), 12) if signal_rewards[signal] else 0
}
for signal in signals
}

coin_signal = Counter(
(str(r.get("block_coin") or "UNKNOWN"), str(r.get("final_signal") or "UNKNOWN"))
for r in rows
)

package_signal = Counter(
(str(r.get("package_name") or "UNKNOWN"), str(r.get("final_signal") or "UNKNOWN"))
for r in rows
)

report = {
"matched_blocks_total": len(rows),
"important_note": "Matched block counts are observational. They are not hit rates because unsuccessful radar opportunities are not represented by realized blocks alone.",
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

open(OUTPUT_FILE, "w", encoding="utf-8").write(json.dumps(report, indent=2, sort_keys=True))

print("Matched blocks:", len(rows))
print("Signals:", dict(signals))
print("Coins:", dict(coins))
print("Packages:", dict(packages))
print("Output:", OUTPUT_FILE)
