import json
from pathlib import Path

shared = [
    json.loads(x)
    for x in Path("calibration/shared-package-history.jsonl").read_text().splitlines()
    if x.strip()
]

tickets = [
    x.get("currencyAlgoTicket")
    for x in shared
    if isinstance(x.get("currencyAlgoTicket"), dict)
]

print("TICKETS FOUND", len(tickets))

top_keys = sorted(set(k for t in tickets for k in t.keys()))
print("TOP LEVEL TICKET KEYS")
print(top_keys)

id_like = sorted(set(
    k
    for t in tickets
    for k in t.keys()
    if "id" in k.lower() or "ticket" in k.lower() or "package" in k.lower()
))

print("TOP LEVEL ID-LIKE KEYS")
print(id_like)

nested = []

for t in tickets:
    for k, v in t.items():
        if isinstance(v, dict):
            for nk in v.keys():
                if "id" in nk.lower() or "ticket" in nk.lower() or "package" in nk.lower():
                    nested.append(f"{k}.{nk}")

print("NESTED ID-LIKE KEYS")
print(sorted(set(nested)))
