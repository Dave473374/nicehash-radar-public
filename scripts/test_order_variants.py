import json
from pathlib import Path

path = Path("calibration/shared-package-history.jsonl")
rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

package_ids = sorted(set(str(x.get("package_id") or "") for x in rows if x.get("package_id")))

groups = [
[x for x in rows if str(x.get("package_id") or "") == package_id]
for package_id in package_ids
]

changed_groups = [g for g in groups if len(g) > 1]

print("TOTAL ROWS", len(rows))
print("UNIQUE PACKAGES", len(package_ids))
print("PACKAGES WITH MULTIPLE STATES", len(changed_groups))

summary = [
{
"states": len(g),
"status": [x.get("status") for x in g],
"participants": [x.get("numberOfParticipants") for x in g],
"probability": [x.get("probability") for x in g],
"mergeProbability": [x.get("mergeProbability") for x in g],
"addedAmount": [x.get("addedAmount") for x in g],
"fallAmount": [x.get("fallAmount") for x in g]
}
for g in changed_groups
]

print("CHANGES")
print(json.dumps(summary, indent=2))
