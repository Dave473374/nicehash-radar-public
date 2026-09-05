import json
import os
from datetime import datetime, timezone

SOURCE_FILE = "buy-feed.json"
OUTPUT_DIR = "calibration"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "radar-snapshots.jsonl")

EXPECTED_VERSION = "2.8.2"


def load_feed():
with open(SOURCE_FILE, "r", encoding="utf-8") as f:
return json.load(f)


def find_packages(obj):
"""
Recursively find dictionaries that look like package records.
This intentionally avoids depending on one exact buy-feed layout.
"""
found = []

if isinstance(obj, dict):
keys = set(obj.keys())

looks_like_package = (
("expected_blocks" in keys or "expected_primary_blocks" in keys)
and (
"final_signal" in keys
or "mining_signal" in keys
or "economic_signal" in keys
)
)

if looks_like_package:
found.append(obj)

for value in obj.values():
found.extend(find_packages(value))

elif isinstance(obj, list):
for value in obj:
found.extend(find_packages(value))

return found


def first(record, *keys):
for key in keys:
if key in record:
return record[key]
return None


def compact_package(record):
return {
"package": first(
record,
"package",
"package_name",
"name",
"label",
),
"coin": first(
record,
"coin",
"symbol",
"primary_coin",
),
"btc_cost": first(
record,
"btc_cost",
"cost_btc",
"price_btc",
),
"mining_signal": record.get("mining_signal"),
"economic_signal": record.get("economic_signal"),
"final_signal": record.get("final_signal"),
"decision": record.get("decision"),

"expected_blocks": first(
record,
"expected_blocks",
"expected_primary_blocks",
),

"expected_reward_btc_equiv": record.get(
"expected_reward_btc_equiv"
),

"expected_return_multiple": record.get(
"expected_return_multiple"
),

"expected_return_percent": record.get(
"expected_return_percent"
),

"profitability_margin_percent": record.get(
"profitability_margin_percent"
),

"break_even_primary_blocks": record.get(
"break_even_primary_blocks"
),

"break_even_multiple_vs_expected_blocks": record.get(
"break_even_multiple_vs_expected_blocks"
),

"break_even_probability_percent_approx": record.get(
"break_even_probability_percent_approx"
),

"break_even_risk": first(
record,
"break_even_risk",
"break_even_risk_assessment",
),

"break_even_cap": first(
record,
"break_even_cap",
"max_final_signal",
),

"model_hit_probability_percent": first(
record,
"model_hit_probability_percent",
"hit_probability_percent",
),

"odds": first(
record,
"odds",
"nicehash_odds",
),
}


def main():
feed = load_feed()

# Safety: this collector must never silently start recording
# incompatible radar versions as if they were v2.8.2 data.
if feed.get("relay_version") != EXPECTED_VERSION:
raise RuntimeError(
f"Unexpected relay_version: "
f"{feed.get('relay_version')!r}"
)

if feed.get("status") != "BUY FEED OK":
raise RuntimeError(
f"Feed status is not healthy: {feed.get('status')!r}"
)

if feed.get("ok") is not True:
raise RuntimeError("Feed reports ok != true")

if feed.get("upstream_status") != 200:
raise RuntimeError(
f"Unexpected upstream_status: "
f"{feed.get('upstream_status')!r}"
)

if feed.get("market_status") != "MARKET OK":
raise RuntimeError(
f"Unexpected market_status: "
f"{feed.get('market_status')!r}"
)

packages = find_packages(feed)

if not packages:
raise RuntimeError(
"No package records found in buy-feed.json"
)

compact = [compact_package(p) for p in packages]

# Remove accidental duplicate records.
unique = []
seen = set()

for package in compact:
fingerprint = json.dumps(
package,
sort_keys=True,
ensure_ascii=False,
)

if fingerprint not in seen:
seen.add(fingerprint)
unique.append(package)

snapshot = {
"collected_at": datetime.now(timezone.utc).isoformat(),
"feed_generated_at": first(
feed,
"generated_at",
"timestamp",
"updated_at",
),
"relay_version": feed.get("relay_version"),
"decision_engine": feed.get("decision_engine"),
"package_count": len(unique),
"packages": unique,
}

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
f.write(
json.dumps(
snapshot,
separators=(",", ":"),
ensure_ascii=False,
)
+ "\n"
)

print(
f"Calibration snapshot saved: "
f"{len(unique)} package records"
)


if __name__ == "__main__":
main()
