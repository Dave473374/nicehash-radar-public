import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

GLOBAL_ORDERS = Path(
    os.getenv(
        "GLOBAL_ORDERS_PATH",
        "/tmp/nicehash-global-completed-orders.json",
    )
)
RADAR_HISTORY = Path(
    os.getenv(
        "RADAR_HISTORY_PATH",
        "calibration/radar-snapshots.jsonl",
    )
)
OUTPUT = Path(
    os.getenv(
        "GLOBAL_MATCH_OUTPUT",
        "/tmp/global-order-matches.json",
    )
)

MAX_SNAPSHOT_AGE_SECONDS = 7200

SIGNALS = [
    "STRONG BUY",
    "BUY NOW",
    "GOOD",
    "WAIT",
    "NO BUY",
]


def parse_ts(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def load_orders():
    if not GLOBAL_ORDERS.exists():
        print("GLOBAL INPUT MISSING:", GLOBAL_ORDERS)
        return []

    data = json.loads(
        GLOBAL_ORDERS.read_text(encoding="utf-8")
    )

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data.get("list", [])

    return []


def load_snapshots():
    if not RADAR_HISTORY.exists():
        return []

    rows = []

    for line in RADAR_HISTORY.read_text(
        encoding="utf-8"
    ).splitlines():
        if not line.strip():
            continue

        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return rows


def order_fingerprint(order):
    parts = [
        order.get("startTs") or order.get("orderStartTs"),
        order.get("endTs") or order.get("orderEndTs"),
        order.get("packageName") or order.get("package_name"),
        order.get("currencyMarket") or order.get("currency_market"),
        order.get("payedAmount") or order.get("amountSpent"),
        order.get("soloMiningCoin") or order.get("coin"),
        order.get("soloMiningMergeCoin") or order.get("mergeCoin"),
        order.get("isReward") if isinstance(order.get("isReward"), bool) else order.get("hadReward"),
    ]

    canonical = "|".join(
        "" if value is None else str(value)
        for value in parts
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def dedupe_orders(orders):
    seen = set()
    unique = []
    duplicates = 0

    for order in orders:
        fingerprint = order_fingerprint(order)

        if fingerprint in seen:
            duplicates += 1
            continue

        seen.add(fingerprint)
        unique.append(order)

    return unique, duplicates


def is_current_scope(order):
    package_name = str(
        order.get("packageName")
        or order.get("package_name")
        or ""
    ).strip()

    currency = str(
        order.get("currencyMarket")
        or order.get("currency_market")
        or ""
    ).upper()

    if not package_name:
        return False

    if package_name.startswith("Team "):
        return False

    if currency == "BTC":
        return bool(
            re.match(r"^.+ [SM]$", package_name)
        )

    if currency == "USDT":
        return bool(
            re.match(
                r"^(Silver|Gold) \d+(?:\.\d+)?$",
                package_name,
            )
        )

    return False


def get_order_cost_btc(order):
    for field in (
        "actualCostBtc",
        "packageCostBtcEquiv",
        "package_cost_btc_equiv",
        "payedAmountBtc",
    ):
        value = to_float(order.get(field))

        if value is not None and value > 0:
            return value

    currency = str(
        order.get("currencyMarket")
        or order.get("currency_market")
        or ""
    ).upper()

    if currency == "BTC":
        value = to_float(
            order.get("payedAmount")
            or order.get("packagePrice")
            or order.get("amountSpent")
        )

        if value is not None and value > 0:
            return value

    return None


def get_return_btc_with_availability(order):
    if "realizedReturnBtc" in order:
        value = to_float(order.get("realizedReturnBtc"))
        if value is not None:
            return value, True

    rewards = (
        order.get("rewards")
        or order.get("soloReward")
    )

    if not isinstance(rewards, list) or not rewards:
        return None, False

    values = []

    for reward in rewards:
        if not isinstance(reward, dict):
            continue

        if "payoutRewardBtc" not in reward:
            continue

        value = to_float(reward.get("payoutRewardBtc"))

        if value is not None:
            values.append(value)

    if not values:
        return None, False

    return sum(values), True


def get_reward_count(order):
    reward_count = order.get("rewardCount")

    if isinstance(reward_count, int):
        return reward_count

    rewards = (
        order.get("rewards")
        or order.get("soloReward")
    )

    if isinstance(rewards, list) and rewards:
        return len(rewards)

    if order.get("hadReward") is True:
        return 1

    if order.get("isReward") is True:
        return 1

    return 0


def get_had_reward(order):
    if isinstance(order.get("hadReward"), bool):
        return order["hadReward"]

    if isinstance(order.get("isReward"), bool):
        return order["isReward"]

    return get_reward_count(order) > 0


def mean_numeric(rows, key):
    values = [
        float(row[key])
        for row in rows
        if isinstance(row.get(key), (int, float))
    ]

    if not values:
        return None

    return round(sum(values) / len(values), 4)


def make_hit_miss_stats(rows, key=None):
    groups = {}

    if key is None:
        groups["ALL"] = rows
    else:
        for row in rows:
            label = row.get(key)
            if label in (None, ""):
                label = "UNKNOWN"
            groups.setdefault(str(label), []).append(row)

    result = []

    for label, group in groups.items():
        hits = sum(
            1 for row in group
            if row.get("hadReward") is True
        )
        misses = len(group) - hits

        item = {
            "orders": len(group),
            "hits": hits,
            "misses": misses,
            "hitRatePercent": (
                round(hits / len(group) * 100, 4)
                if group
                else None
            ),
        }

        if key is not None:
            item[key] = label

        result.append(item)

    result.sort(
        key=lambda item: (
            -item["orders"],
            str(item.get(key, "")),
        )
    )

    return result


orders_raw = load_orders()
orders, duplicate_orders = dedupe_orders(orders_raw)
snapshots = load_snapshots()

snapshot_points = []

for snapshot in snapshots:
    ts = parse_ts(snapshot.get("collected_at"))

    if ts is not None:
        snapshot_points.append((ts, snapshot))

snapshot_points.sort(key=lambda item: item[0])


def find_match(order):
    if not is_current_scope(order):
        return None, "OUT_OF_CURRENT_SCOPE"

    order_start = parse_ts(
        order.get("startTs")
        or order.get("orderStartTs")
    )

    if order_start is None:
        return None, "INVALID_START_TIME"

    package_name = (
        order.get("packageName")
        or order.get("package_name")
    )

    if not package_name:
        return None, "PACKAGE_MAPPING_MISSING"

    coin = (
        order.get("soloMiningCoin")
        or order.get("coin")
        or order.get("primaryCoin")
    )

    currency = str(
        order.get("currencyMarket")
        or order.get("currency_market")
        or ""
    ).upper()

    candidates = [
        (ts, snapshot)
        for ts, snapshot in snapshot_points
        if ts <= order_start
        and (
            order_start - ts
        ).total_seconds() <= MAX_SNAPSHOT_AGE_SECONDS
    ]

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    for ts, snapshot in candidates:
        packages = (
            snapshot.get("feed", {}).get("packages")
            or []
        )

        for package in packages:
            package_coin = (
                package.get("primary_chain") or {}
            ).get("currency")

            package_currency = str(
                package.get("currency_market") or ""
            ).upper()

            if package.get("name") != package_name:
                continue

            if coin and package_coin != coin:
                continue

            if currency and package_currency and package_currency != currency:
                continue

            cost_btc = get_order_cost_btc(order)
            realized_btc, return_available = (
                get_return_btc_with_availability(order)
            )
            reward_count = get_reward_count(order)
            had_reward = get_had_reward(order)

            roi_available = bool(
                cost_btc is not None
                and cost_btc > 0
                and return_available
            )

            result = {
                "orderStartTs": (
                    order.get("startTs")
                    or order.get("orderStartTs")
                ),
                "orderEndTs": (
                    order.get("endTs")
                    or order.get("orderEndTs")
                ),
                "packageName": package_name,
                "coin": coin,
                "mergeCoin": (
                    order.get("soloMiningMergeCoin")
                    or order.get("mergeCoin")
                ),
                "currencyMarket": currency or None,
                "actualCostBtc": cost_btc,
                "rewardCount": reward_count,
                "hadReward": had_reward,
                "outcome": "HIT" if had_reward else "MISS",
                "roiAvailable": roi_available,
                "realizedReturnBtc": (
                    realized_btc
                    if roi_available
                    else None
                ),
                "realizedReturnMultiple": (
                    round(realized_btc / cost_btc, 4)
                    if roi_available
                    else None
                ),
                "realizedRoiPercent": (
                    round(
                        (realized_btc / cost_btc - 1) * 100,
                        2,
                    )
                    if roi_available
                    else None
                ),
                "snapshotCollectedAt": snapshot.get(
                    "collected_at"
                ),
                "snapshotAgeMinutes": round(
                    (
                        order_start - ts
                    ).total_seconds() / 60,
                    2,
                ),
                "relayVersion": snapshot.get(
                    "relay_version"
                ),
                "miningSignal": package.get(
                    "mining_signal"
                ),
                "economicSignal": package.get(
                    "economic_signal"
                ),
                "finalSignal": package.get(
                    "final_signal"
                ),
                "modelHitProbabilityPercent": (
                    package.get("primary_chain") or {}
                ).get("model_hit_probability_percent"),
                "nicehashHitProbabilityPercent": (
                    package.get("nicehash_odds") or {}
                ).get("hit_probability_percent"),
                "expectedReturnPercent": (
                    package.get("profitability") or {}
                ).get("expected_return_percent"),
                "profitabilityMarginPercent": (
                    package.get("profitability") or {}
                ).get("profitability_margin_percent"),
                "qualityVs24hPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_24h_percent"
                ),
                "qualityVs7dPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_7d_percent"
                ),
            }

            return result, "MATCHED"

    return None, "NO_RADAR_MATCH"


matches = []
skip_reasons = {}

for order in orders:
    match, reason = find_match(order)

    if match is not None:
        matches.append(match)
    else:
        skip_reasons[reason] = (
            skip_reasons.get(reason, 0) + 1
        )

signal_stats = []

for signal in SIGNALS:
    rows = [
        row for row in matches
        if row.get("finalSignal") == signal
    ]

    if not rows:
        continue

    hits = sum(
        1 for row in rows
        if row.get("hadReward") is True
    )

    signal_stats.append(
        {
            "signal": signal,
            "orders": len(rows),
            "hits": hits,
            "misses": len(rows) - hits,
            "hitRatePercent": round(
                hits / len(rows) * 100,
                4,
            ),
            "averageModelHitProbabilityPercent": mean_numeric(
                rows,
                "modelHitProbabilityPercent",
            ),
            "averageExpectedReturnPercent": mean_numeric(
                rows,
                "expectedReturnPercent",
            ),
            "averageQualityVs24hPercent": mean_numeric(
                rows,
                "qualityVs24hPercent",
            ),
            "averageQualityVs7dPercent": mean_numeric(
                rows,
                "qualityVs7dPercent",
            ),
        }
    )

package_stats = make_hit_miss_stats(
    matches,
    "packageName",
)
coin_stats = make_hit_miss_stats(
    matches,
    "coin",
)
currency_stats = make_hit_miss_stats(
    matches,
    "currencyMarket",
)

total_hits = sum(
    1 for row in matches
    if row.get("hadReward") is True
)

roi_rows = [
    row for row in matches
    if row.get("roiAvailable") is True
]

roi_summary = {
    "status": (
        "AVAILABLE_FOR_ALL_MATCHED_ORDERS"
        if matches and len(roi_rows) == len(matches)
        else "PARTIAL"
        if roi_rows
        else "UNAVAILABLE"
    ),
    "ordersWithCompleteRoi": len(roi_rows),
    "matchedOrders": len(matches),
}

if roi_rows:
    total_cost = sum(
        row["actualCostBtc"]
        for row in roi_rows
        if isinstance(row.get("actualCostBtc"), (int, float))
    )
    total_return = sum(
        row["realizedReturnBtc"]
        for row in roi_rows
        if isinstance(row.get("realizedReturnBtc"), (int, float))
    )

    roi_summary.update(
        {
            "totalCostBtc": round(total_cost, 12),
            "totalReturnBtc": round(total_return, 12),
            "realizedReturnMultiple": (
                round(total_return / total_cost, 4)
                if total_cost > 0
                else None
            ),
            "realizedRoiPercent": (
                round(
                    (total_return / total_cost - 1) * 100,
                    2,
                )
                if total_cost > 0
                else None
            ),
        }
    )

result = {
    "source": "GLOBAL_COMPLETED_EASYMINING_ORDERS",
    "modelUse": "SHADOW_CALIBRATION_ONLY",
    "importantNote": (
        "One completed order is one exposure. isReward/hadReward "
        "defines HIT/MISS. ROI is calculated only when an explicit "
        "BTC reward payout amount is present; HIT/MISS-only batches "
        "must not be interpreted as zero-return ROI batches."
    ),
    "inputOrders": len(orders_raw),
    "uniqueOrders": len(orders),
    "duplicateOrdersRemoved": duplicate_orders,
    "radarSnapshots": len(snapshots),
    "validMatchedOrders": len(matches),
    "skippedOrders": len(orders) - len(matches),
    "skipReasons": skip_reasons,
    "overall": {
        "orders": len(matches),
        "hits": total_hits,
        "misses": len(matches) - total_hits,
        "hitRatePercent": (
            round(total_hits / len(matches) * 100, 4)
            if matches
            else None
        ),
    },
    "roi": roi_summary,
    "signalStats": signal_stats,
    "packageStats": package_stats,
    "coinStats": coin_stats,
    "currencyStats": currency_stats,
    "matches": matches,
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    json.dumps(
        result,
        separators=(",", ":"),
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

print("GLOBAL ORDER HIT/MISS CALIBRATION")
print("Input orders:", len(orders_raw))
print("Unique orders:", len(orders))
print("Duplicates removed:", duplicate_orders)
print("Radar snapshots:", len(snapshots))
print("Valid matched orders:", len(matches))
print("Skipped orders:", len(orders) - len(matches))
print("Skip reasons:", skip_reasons)
print("Hits:", total_hits)
print("Misses:", len(matches) - total_hits)
print("ROI status:", roi_summary["status"])
print("Global order matcher completed successfully")
