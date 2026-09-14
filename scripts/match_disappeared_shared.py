import json
from pathlib import Path

shared = [json.loads(x) for x in Path("calibration/shared-package-history.jsonl").read_text().splitlines() if x.strip()]
blocks = [json.loads(x) for x in Path("calibration/realized-blocks.jsonl").read_text().splitlines() if x.strip()]

ticket_ids = set(
    str(x["currencyAlgoTicket"]["id"])
    for x in shared
    if isinstance(x.get("currencyAlgoTicket"), dict)
    and x["currencyAlgoTicket"].get("id")
)

block_package_ids = set(
    str(x["packageId"])
    for x in blocks
    if x.get("packageId")
)

matches = ticket_ids & block_package_ids

print("UNIQUE TICKET IDS", len(ticket_ids))
print("UNIQUE REALIZED PACKAGE IDS", len(block_package_ids))
print("EXACT TICKET-ID MATCHES", len(matches))

matched_blocks = [
    {
        "coin": x.get("coin"),
        "packageName": x.get("packageName"),
        "shared": x.get("shared"),
        "createdTs": x.get("createdTs")
    }
    for x in blocks
    if str(x.get("packageId")) in matches
]

print("MATCHED BLOCKS", len(matched_blocks))
print("MATCHED BLOCK SUMMARY")
print(matched_blocks[:20])
