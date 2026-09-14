import json
from pathlib import Path

history_path = Path("calibration/shared-package-history.jsonl")
blocks_path = Path("calibration/realized-blocks.jsonl")

history = [
    json.loads(line)
    for line in history_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

blocks = [
    json.loads(line)
    for line in blocks_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

disappeared = [
    x
    for x in history
    if x.get("status") == "DISAPPEARED"
]

latest = disappeared[-1] if disappeared else None

print("DISAPPEARED RECORDS", len(disappeared))

if not latest:
    print("NO DISAPPEARED RECORD FOUND")
else:
    ticket = latest.get("currencyAlgoTicket") or {}
    primary = ticket.get("currencyAlgo") or {}
    merge = ticket.get("mergeCurrencyAlgo") or {}

    print("LATEST DISAPPEARED")

    print(json.dumps(
        {
            "package_id_present": bool(latest.get("package_id")),
            "collected_at": latest.get("collected_at"),
            "createdTs": latest.get("createdTs"),
            "duration": latest.get("duration"),
            "last_status": latest.get("status"),
            "participants": latest.get("numberOfParticipants"),
            "probability": latest.get("probability"),
            "mergeProbability": latest.get("mergeProbability"),
            "ticket_id": ticket.get("id"),
            "package_name": ticket.get("name"),
            "primary_coin": primary.get("currency"),
            "primary_algorithm": primary.get("miningAlgorithm"),
            "merge_coin": merge.get("currency"),
            "merge_algorithm": merge.get("miningAlgorithm")
        },
        indent=2,
        ensure_ascii=False
    ))

    ticket_id = str(ticket.get("id") or "")

    matching_blocks = [
        {
            "coin": x.get("coin"),
            "packageName": x.get("packageName"),
            "shared": x.get("shared"),
            "createdTs": x.get("createdTs")
        }
        for x in blocks
        if str(x.get("packageId") or "") == ticket_id
    ]

    print("BLOCKS WITH SAME TICKET ID", len(matching_blocks))
    print("LATEST 10 SAME-TICKET BLOCKS")
    print(json.dumps(matching_blocks[-10:], indent=2, ensure_ascii=False))
