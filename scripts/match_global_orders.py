import json
from pathlib import Path
from datetime import datetime

GLOBAL_ORDERS = Path("/tmp/nicehash-global-completed-orders.json")
RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/global-order-matches.json")

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
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0


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

    return [
        json.loads(line)
        for line in RADAR_HISTORY.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


orders = load_orders()
snapshots = load_snapshots()

snapshot_points = []

for snapshot in snapshots:
    ts = parse_ts(snapshot.get("collected_at"))

    if ts is not None:
        snapshot_points.append((ts, snapshot))


def get_order_cost_btc(order):
    for field in (
        "actualCostBtc",
        "packageCostBtcEquiv",
        "package_cost_btc_equiv",
        "payedAmountBtc",
    ):
        value = to_float(order.get(field))

        if value > 0:
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

        if value > 0:
            return value

    return 0.0


def get_realized_return_btc(order):
    direct = to_float(
        order.get("realizedReturnBtc")
    )

    if direct > 0:
        return direct

    rewards = (
        order.get("rewards")
        or order.get("soloReward")
        or []
    )

    if not isinstance(rewards, list):
        return 0.0

    return sum(
        to_float(reward.get("payoutRewardBtc"))
        for reward in rewards
        if isinstance(reward, dict)
    )


def get_reward_count(order):
    if isinstance(order.get("rewardCount"), int):
        return order["rewardCount"]

    rewards = (
        order.get("rewards")
        or order.get("soloReward")
        or []
    )

    if isinstance(rewards, list):
        return len(rewards)

    return 1 if order.get("hadReward") is True else 0


def get_had_reward(order):
    if isinstance(order.get("hadReward"), bool):
        return order["hadReward"]

    if isinstance(order.get("isReward"), bool):
        return order["isReward"]

    return get_reward_count(order) > 0


def find_match(order):
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

            if package.get("name") != package_name:
                continue

            if coin and package_coin != coin:
                continue

            cost_btc = get_order_cost_btc(order)
            realized_btc = get_realized_return_btc(
                order
            )
            reward_count = get_reward_count(order)
            had_reward = get_had_reward(order)

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

                "currencyMarket": (
                    order.get("currencyMarket")
                    or order.get("currency_market")
                ),

                "actualCostBtc": cost_btc,
                "realizedReturnBtc": realized_btc,

                "rewardCount": reward_count,
                "hadReward": had_reward,
                "outcome": (
                    "HIT"
                    if had_reward
                    else "MISS"
                ),

                "snapshotCollectedAt": (
                    snapshot.get("collected_at")
                ),

                "snapshotAgeMinutes": round(
                    (
                        order_start - ts
                    ).total_seconds() / 60,
                    2,
                ),

                "radarPriceBtc": (
                    package.get("price_btc")
                ),

                "radarPriceBtcEquiv": (
                    package.get("price_btc_equiv")
                ),

                "miningSignal": (
                    package.get("mining_signal")
                ),

                "economicSignal": (
                    package.get("economic_signal")
                ),

                "finalSignal": (
                    package.get("final_signal")
                ),

                "expectedReturnPercent": (
                    package.get("profitability") or {}
                ).get("expected_return_percent"),

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

            if cost_btc > 0:
                result["realizedReturnMultiple"] = round(
                    realized_btc / cost_btc,
                    4,
                )

                result["realizedRoiPercent"] = round(
                    (
                        realized_btc / cost_btc - 1
                    ) * 100,
                    2,
                )
            else:
                result["realizedReturnMultiple"] = None
                result["realizedRoiPercent"] = None

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
        row
        for row in matches
        if row.get("finalSignal") == signal
    ]

    if not rows:
        continue

    hits = sum(
        1
        for row in rows
        if row.get("hadReward")
    )

    misses = len(rows) - hits

    total_cost = sum(
        to_float(row.get("actualCostBtc"))
        for row in rows
    )

    total_return = sum(
        to_float(row.get("realizedReturnBtc"))
        for row in rows
    )

    expected_values = [
        float(row["expectedReturnPercent"])
        for row in rows
        if isinstance(
            row.get("expectedReturnPercent"),
            (int, float),
        )
    ]

    signal_stats.append(
        {
            "signal": signal,
            "orders": len(rows),
            "hits": hits,
            "misses": misses,

            "hitRatePercent": round(
                hits / len(rows) * 100,
                4,
            ),

            "totalCostBtc": round(
                total_cost,
                12,
            ),

            "totalReturnBtc": round(
                total_return,
                12,
            ),

            "realizedReturnMultiple": (
                round(
                    total_return / total_cost,
                    4,
                )
                if total_cost > 0
                else None
            ),

            "realizedRoiPercent": (
                round(
                    (
                        total_return / total_cost
                        - 1
                    ) * 100,
                    2,
                )
                if total_cost > 0
                else None
            ),

            "averageExpectedReturnPercent": (
                round(
                    sum(expected_values)
                    / len(expected_values),
                    2,
                )
                if expected_values
                else None
            ),
        }
    )


total_cost = sum(
    to_float(row.get("actualCostBtc"))
    for row in matches
)

total_return = sum(
    to_float(row.get("realizedReturnBtc"))
    for row in matches
)

total_hits = sum(
    1
    for row in matches
    if row.get("hadReward")
)

overall = {
    "orders": len(matches),
    "hits": total_hits,
    "misses": len(matches) - total_hits,

    "hitRatePercent": (
        round(
            total_hits / len(matches) * 100,
            4,
        )
        if matches
        else None
    ),

    "totalCostBtc": round(
        total_cost,
        12,
    ),

    "totalReturnBtc": round(
        total_return,
        12,
    ),

    "realizedReturnMultiple": (
        round(
            total_return / total_cost,
            4,
        )
        if total_cost > 0
        else None
    ),

    "realizedRoiPercent": (
        round(
            (
                total_return / total_cost
                - 1
            ) * 100,
            2,
        )
        if total_cost > 0
        else None
    ),
}


result = {
    "source": "GLOBAL_COMPLETED_EASYMINING_ORDERS",

    "importantNote": (
        "One completed order is one exposure. "
        "An order with one or more rewards counts "
        "as one HIT. Multiple rewards from the same "
        "order do not create multiple HITs."
    ),

    "inputOrders": len(orders),
    "radarSnapshots": len(snapshots),
    "validMatchedOrders": len(matches),

    "skippedOrders": (
        len(orders) - len(matches)
    ),

    "skipReasons": skip_reasons,

    "overall": overall,
    "signalStats": signal_stats,
    "matches": matches,
}


OUTPUT.write_text(
    json.dumps(
        result,
        separators=(",", ":"),
        ensure_ascii=False,
    ),
    encoding="utf-8",
)


print("GLOBAL ORDER CALIBRATION")
print("Input orders:", len(orders))
print("Radar snapshots:", len(snapshots))
print("Valid matched orders:", len(matches))
print("Skipped orders:", len(orders) - len(matches))
print("Skip reasons:", skip_reasons)
print("Hits:", total_hits)
print("Misses:", len(matches) - total_hits)

print(
    "Signal calibration:",
    json.dumps(
        signal_stats,
        indent=2,
        ensure_ascii=False,
    ),
)

print(
    "Global order matcher completed successfully"
)
