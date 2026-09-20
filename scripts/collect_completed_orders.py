import json
import os
from pathlib import Path

from nicehash_private_readonly import (
    get_completed_easymining_orders,
    policy_summary,
)

PAGE_SIZE = 100
MAX_PAGES = int(os.getenv("PRIVATE_EASYMINING_MAX_PAGES", "10"))
DIAGNOSTICS = os.getenv("PRIVATE_EASYMINING_DIAGNOSTICS", "0") == "1"
OUTPUT = Path("/tmp/nicehash-completed-orders.json")


def first_value(row, *keys):
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def sanitize_reward(reward):
    if not isinstance(reward, dict):
        return None

    out = {
        "payoutRewardBtc": first_value(
            reward,
            "payoutRewardBtc",
            "rewardBtc",
        ),
    }

    return out if out["payoutRewardBtc"] not in (None, "") else None


def sanitize_order(row):
    if not isinstance(row, dict):
        return None

    raw_rewards = row.get("soloReward")
    # Keep an unknown placeholder rather than erase a missing payout leg.
    # Otherwise the downstream matcher could mistake a partial sum for total ROI.
    rewards = (
        [sanitize_reward(reward) for reward in raw_rewards]
        if isinstance(raw_rewards, list)
        else [] if raw_rewards is None else [None]
    )

    out = {
        "startTs": first_value(row, "startTs", "orderStartTs"),
        "endTs": first_value(row, "endTs", "orderEndTs"),
        "packageName": first_value(row, "packageName", "soloMiningPackageName"),
        "packagePrice": row.get("packagePrice"),
        "payedAmount": row.get("payedAmount"),
        "currencyMarket": row.get("currencyMarket"),
        "soloMiningCoin": row.get("soloMiningCoin"),
        "soloMiningMergeCoin": row.get("soloMiningMergeCoin"),
        "isReward": row.get("isReward")
        if isinstance(row.get("isReward"), bool)
        else None,
        "soloMiningSharesMaxPercent": row.get("soloMiningSharesMaxPercent"),
        "soloReward": rewards,
    }

    if not out["startTs"] or not out["packageName"]:
        return None

    return out


orders = []
page_summaries = []

for page in range(MAX_PAGES):
    rows = get_completed_easymining_orders(
        page=page,
        limit=PAGE_SIZE,
    )

    starts = sorted(
        str(row.get("startTs"))
        for row in rows
        if isinstance(row, dict) and row.get("startTs")
    )

    page_summaries.append(
        {
            "page": page,
            "rows": len(rows),
            "oldestStartTs": starts[0] if starts else None,
            "newestStartTs": starts[-1] if starts else None,
        }
    )

    for row in rows:
        clean = sanitize_order(row)
        if clean is not None:
            orders.append(clean)

    if len(rows) < PAGE_SIZE:
        break

seen = set()
deduped = []

for row in orders:
    key = (
        row.get("startTs"),
        row.get("endTs"),
        row.get("packageName"),
        str(row.get("payedAmount")),
        row.get("soloMiningCoin"),
    )
    if key in seen:
        continue
    seen.add(key)
    deduped.append(row)

OUTPUT.write_text(
    json.dumps(
        {
            "source": "PRIVATE_EASYMINING_READONLY_SANITIZED",
            "policy": policy_summary(),
            "pageSummaries": page_summaries,
            "list": deduped,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

all_starts = sorted(
    str(row.get("startTs"))
    for row in deduped
    if row.get("startTs")
)

print("PRIVATE EASYMINING READ-ONLY COLLECTOR OK")
print("Raw private response persisted: NO")
print("Private response printed to logs: NO")
print("Admin access allowed: NO")

if DIAGNOSTICS:
    print("Allowed host:", policy_summary()["host"])
    print("Allowed method:", policy_summary()["method"])
    print("Allowed paths:", policy_summary()["paths"])
    print("Pages fetched:", len(page_summaries))
    print("Rows by page:", [x["rows"] for x in page_summaries])
    print("Sanitized unique orders:", len(deduped))
    print("Oldest startTs:", all_starts[0] if all_starts else None)
    print("Newest startTs:", all_starts[-1] if all_starts else None)
