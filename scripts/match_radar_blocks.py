import json
from datetime import datetime

SNAPSHOTS_FILE = "calibration/radar-snapshots.jsonl"
BLOCKS_FILE = "calibration/realized-blocks.jsonl"
OUTPUT_FILE = "calibration/radar-block-matches.jsonl"

snapshots = [json.loads(x) for x in open(SNAPSHOTS_FILE, encoding="utf-8") if x.strip()]
blocks = [json.loads(x) for x in open(BLOCKS_FILE, encoding="utf-8") if x.strip()]

parse_time = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))
snapshot_times = [(parse_time(s["collected_at"]), s) for s in snapshots]

latest_snapshot = lambda bt: max((s for t, s in snapshot_times if t <= bt), key=lambda s: parse_time(s["collected_at"]), default=None)
package_for = lambda s, name: next((p for p in s.get("feed", {}).get("packages", []) if p.get("name") == name), None) if s else None

pairs = [(b, latest_snapshot(parse_time(b["createdTs"]))) for b in blocks if b.get("createdTs")]
triples = [(b, s, package_for(s, b.get("packageName"))) for b, s in pairs]
triples = [(b, s, p) for b, s, p in triples if s is not None and p is not None]

records = [{
"block_coin": b.get("coin"),
"block_height": b.get("blockHeight"),
"block_hash": b.get("blockHash"),
"block_reward": b.get("payoutReward"),
"block_reward_btc": b.get("payoutRewardBtc"),
"block_time": b.get("createdTs"),
"package_name": b.get("packageName"),
"package_id": b.get("packageId"),
"shared": b.get("shared"),
"snapshot_time": s.get("collected_at"),
"snapshot_age_seconds": round((parse_time(b["createdTs"]) - parse_time(s["collected_at"])).total_seconds(), 1),
"price_btc": p.get("price_btc"),
"primary_coin": p.get("primary_chain", {}).get("currency"),
"merge_coin": p.get("merge_chain", {}).get("currency") if p.get("merge_chain") else None,
"mining_signal": p.get("mining_signal"),
"economic_signal": p.get("economic_signal"),
"final_signal": p.get("final_signal"),
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
} for b, s, p in triples]

open(OUTPUT_FILE, "w", encoding="utf-8").write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))

print("Radar snapshots:", len(snapshots))
print("Realized blocks:", len(blocks))
print("Matched blocks:", len(records))
print("Output:", OUTPUT_FILE)
