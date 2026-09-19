import json
import os
from datetime import datetime, timezone
from pathlib import Path

PRIVATE_ORDERS = Path(
    os.getenv(
        "PRIVATE_ORDERS_PATH",
        "/tmp/nicehash-completed-orders.json",
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
        "PRIVATE_MATCH_OUTPUT",
        "/tmp/private-order-matches.json",
    )
)

# Entry calibration must use data that was actually fresh at order time.
MAX_ENTRY_SNAPSHOT_AGE_SECONDS = 15 * 60

SIGNALS = ["STRONG BUY", "BUY NOW", "GOOD", "WAIT", "NO BUY"]


def parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def snapshot_feed_time(snapshot):
    feed = snapshot.get("feed") or {}
    for value in (
        snapshot.get("feed_generated_at"),
        feed.get("checked_at"),
        feed.get("generated_at"),
        snapshot.get("collected_at"),
    ):
        ts = parse_ts(value)
        if ts is not None:
            return ts
    return None


def order_outcome(order):
    value = order.get("isReward")
    if value is True:
        return "HIT"
    if value is False:
        return "MISS"
    return "UNKNOWN"


def realized_return_btc(order, outcome):
    rewards = order.get("soloReward") or []
    values = []

    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        value = to_float(reward.get("payoutRewardBtc"))
        if value is not None:
            values.append(value)

    if values:
        return sum(values), True

    # A completed explicit MISS establishes a zero payout.
    if outcome == "MISS":
        return 0.0, True

    # For HIT/UNKNOWN, missing payout fields must not be coerced to zero.
    return None, False


def actual_native_cost(order):
    # An explicit zero is ambiguous (e.g. cancellation/refund semantics) and
    # must not be silently replaced with nominal package price.
    if "payedAmount" in order and order.get("payedAmount") not in (None, ""):
        paid = to_float(order.get("payedAmount"))
        if paid is not None and paid > 0:
            return paid, "PAYED_AMOUNT"
        return None, "PAYED_AMOUNT_NONPOSITIVE"

    package_price = to_float(order.get("packagePrice"))
    if package_price is not None and package_price > 0:
        return package_price, "PACKAGE_PRICE_FALLBACK"

    return None, "UNAVAILABLE"


def cost_btc_equivalent(order, package):
    native_cost, source = actual_native_cost(order)
    currency = str(order.get("currencyMarket") or "").upper()

    if native_cost is None:
        return native_cost, None, source

    if currency == "BTC":
        return native_cost, native_cost, source

    if currency == "USDT":
        native_quote = to_float(package.get("price_native"))
        btc_quote = to_float(package.get("price_btc_equiv"))
        if (
            native_quote is None
            or native_quote <= 0
            or btc_quote is None
            or btc_quote <= 0
        ):
            return native_cost, None, source + "_NO_CONVERSION"
        return native_cost, native_cost * btc_quote / native_quote, source

    return native_cost, None, source + "_UNSUPPORTED_CURRENCY"


orders = json.loads(
    PRIVATE_ORDERS.read_text(encoding="utf-8")
).get("list", [])

snapshots = [
    json.loads(line)
    for line in RADAR_HISTORY.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

snapshot_points = []
for snapshot in snapshots:
    ts = snapshot_feed_time(snapshot)
    if ts is not None:
        snapshot_points.append((ts, snapshot))

snapshot_points.sort(key=lambda item: item[0])


def find_match(order):
    order_start = parse_ts(order.get("startTs"))
    if order_start is None:
        return None, "INVALID_START_TIME", {}

    package_name = order.get("packageName")
    order_coin = order.get("soloMiningCoin")
    order_currency = str(order.get("currencyMarket") or "").upper()

    if not package_name or not order_coin or not order_currency:
        return None, "MISSING_MATCH_KEY", {}

    past = [
        (ts, snapshot)
        for ts, snapshot in snapshot_points
        if ts <= order_start
    ]
    if not past:
        if snapshot_points and snapshot_points[0][0] > order_start:
            return None, "ORDER_PREDATES_RADAR_HISTORY", {}
        return None, "NO_RADAR_HISTORY_BEFORE_ENTRY", {}

    latest_past_ts = past[-1][0]
    if (order_start - latest_past_ts).total_seconds() > MAX_ENTRY_SNAPSHOT_AGE_SECONDS:
        return None, "NO_FRESH_RADAR_SNAPSHOT", {}

    candidates = [
        (ts, snapshot)
        for ts, snapshot in past
        if (order_start - ts).total_seconds()
        <= MAX_ENTRY_SNAPSHOT_AGE_SECONDS
    ]
    candidates.sort(key=lambda item: item[0], reverse=True)

    saw_package_name = False
    saw_package_coin = False
    radar_currencies = set()
    for ts, snapshot in candidates:
        for package in (snapshot.get("feed", {}).get("packages") or []):
            package_coin = (package.get("primary_chain") or {}).get("currency")
            package_currency = str(
                package.get("currency_market") or ""
            ).upper()

            if package.get("name") != package_name:
                continue
            saw_package_name = True
            if package_coin != order_coin:
                continue
            saw_package_coin = True
            if package_currency != order_currency:
                radar_currencies.add(package_currency or "MISSING")
                continue

            outcome = order_outcome(order)
            return_btc, return_available = realized_return_btc(
                order,
                outcome,
            )
            native_cost, cost_btc, cost_source = cost_btc_equivalent(
                order,
                package,
            )

            roi_available = bool(
                outcome in {"HIT", "MISS"}
                and cost_btc is not None
                and cost_btc > 0
                and return_available
                and return_btc is not None
            )

            return {
                "orderStartTs": order.get("startTs"),
                "orderEndTs": order.get("endTs"),
                "packageName": package_name,
                "coin": order_coin,
                "mergeCoin": order.get("soloMiningMergeCoin"),
                "currencyMarket": order_currency,
                "packagePriceNative": to_float(order.get("packagePrice")),
                "actualCostNative": native_cost,
                "actualCostBtcEquivalent": cost_btc,
                "costSource": cost_source,
                "realizedReturnBtc": return_btc if return_available else None,
                "roiAvailable": roi_available,
                "outcome": outcome,
                "hadReward": (
                    True if outcome == "HIT"
                    else False if outcome == "MISS"
                    else None
                ),
                "closeToRewardPct": order.get(
                    "soloMiningSharesMaxPercent"
                ),
                "snapshotCollectedAt": snapshot.get("collected_at"),
                "snapshotFeedTime": ts.isoformat(),
                "snapshotAgeMinutes": round(
                    (order_start - ts).total_seconds() / 60,
                    2,
                ),
                "radarPriceBtcEquivalent": package.get(
                    "price_btc_equiv"
                ),
                "miningSignal": package.get("mining_signal"),
                "economicSignal": package.get("economic_signal"),
                "finalSignal": package.get("final_signal"),
                "expectedReturnPercent": (
                    package.get("profitability") or {}
                ).get("expected_return_percent"),
                "qualityVs24hPercent": (
                    package.get("history_trend") or {}
                ).get("expected_blocks_per_btc_vs_24h_percent"),
                "qualityVs7dPercent": (
                    package.get("history_trend") or {}
                ).get("expected_blocks_per_btc_vs_7d_percent"),
            }, "MATCHED", {}

    if not saw_package_name:
        return None, "FRESH_RADAR_NO_EXACT_PACKAGE", {}
    if not saw_package_coin:
        return None, "FRESH_RADAR_PRIMARY_COIN_MISMATCH", {}
    return None, "FRESH_RADAR_CURRENCY_MISMATCH", {
        "orderCurrency": order_currency,
        "radarCurrencies": sorted(radar_currencies) or ["MISSING"],
    }


matches = []
skip_reasons = {}
skip_reasons_by_package = {}
skip_reasons_by_package_outcome = {}
currency_mismatch_by_package_outcome = {}

for order in orders:
    match, reason, diagnostic = find_match(order)
    if match is None:
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        package_name = str(order.get("packageName") or "UNKNOWN")
        package_key = f"{package_name}|{reason}"
        skip_reasons_by_package[package_key] = (
            skip_reasons_by_package.get(package_key, 0) + 1
        )
        outcome = order_outcome(order)
        outcome_key = f"{package_name}|{outcome}|{reason}"
        skip_reasons_by_package_outcome[outcome_key] = (
            skip_reasons_by_package_outcome.get(outcome_key, 0) + 1
        )
        if reason == "FRESH_RADAR_CURRENCY_MISMATCH":
            order_currency = str(diagnostic.get("orderCurrency") or "MISSING")
            radar_currencies = ",".join(diagnostic.get("radarCurrencies") or ["MISSING"])
            currency_key = (
                f"{package_name}|{outcome}|ORDER_{order_currency}|RADAR_{radar_currencies}"
            )
            currency_mismatch_by_package_outcome[currency_key] = (
                currency_mismatch_by_package_outcome.get(currency_key, 0) + 1
            )
        continue

    if match["roiAvailable"]:
        cost = match["actualCostBtcEquivalent"]
        ret = match["realizedReturnBtc"]
        match["realizedReturnMultiple"] = round(ret / cost, 4)
        match["realizedRoiPercent"] = round(
            (ret / cost - 1) * 100,
            2,
        )
    else:
        match["realizedReturnMultiple"] = None
        match["realizedRoiPercent"] = None

    matches.append(match)


def stats_for_rows(rows):
    known = [
        row for row in rows
        if row.get("outcome") in {"HIT", "MISS"}
    ]
    hits = sum(1 for row in known if row.get("outcome") == "HIT")
    misses = sum(1 for row in known if row.get("outcome") == "MISS")
    unknown = len(rows) - len(known)

    roi_rows = [
        row for row in rows
        if row.get("roiAvailable") is True
    ]
    total_cost = sum(
        row["actualCostBtcEquivalent"]
        for row in roi_rows
        if isinstance(row.get("actualCostBtcEquivalent"), (int, float))
    )
    total_return = sum(
        row["realizedReturnBtc"]
        for row in roi_rows
        if isinstance(row.get("realizedReturnBtc"), (int, float))
    )

    expected_values = [
        row["expectedReturnPercent"]
        for row in rows
        if isinstance(row.get("expectedReturnPercent"), (int, float))
    ]

    return {
        "orders": len(rows),
        "knownOutcomes": len(known),
        "hits": hits,
        "misses": misses,
        "unknownOutcomes": unknown,
        "hitRatePercent": (
            round(100 * hits / len(known), 2)
            if known
            else None
        ),
        "roiOrders": len(roi_rows),
        "totalCostBtcEquivalent": round(total_cost, 8),
        "totalReturnBtc": round(total_return, 8),
        "realizedReturnMultiple": (
            round(total_return / total_cost, 4)
            if total_cost > 0
            else None
        ),
        "realizedRoiPercent": (
            round((total_return / total_cost - 1) * 100, 2)
            if total_cost > 0
            else None
        ),
        "averageExpectedReturnPercent": (
            round(sum(expected_values) / len(expected_values), 2)
            if expected_values
            else None
        ),
    }


signal_stats = []
for signal in SIGNALS:
    rows = [row for row in matches if row.get("finalSignal") == signal]
    if not rows:
        continue
    signal_stats.append({
        "signal": signal,
        **stats_for_rows(rows),
    })

overall = stats_for_rows(matches)

result = {
    "reportVersion": 2,
    "source": "PRIVATE_EASYMINING_ENTRY_TIME_VALIDATION",
    "entrySnapshotMaxAgeMinutes": (
        MAX_ENTRY_SNAPSHOT_AGE_SECONDS / 60
    ),
    "completedOrders": len(orders),
    "radarSnapshots": len(snapshots),
    "validMatchedOrders": len(matches),
    "matchedRewards": overall["hits"],
    "unknownOutcomes": overall["unknownOutcomes"],
    "skipReasons": skip_reasons,
    "skipReasonsByPackage": skip_reasons_by_package,
    "skipReasonsByPackageOutcome": skip_reasons_by_package_outcome,
    "currencyMismatchByPackageOutcome": currency_mismatch_by_package_outcome,
    "overall": overall,
    "signalStats": signal_stats,
    "matches": matches,
    "policy": {
        "entryTimeOnly": True,
        "unknownOutcomeExcludedFromHitRate": True,
        "missingHitPayoutIsNotZero": True,
        "currencyMarketMustMatch": True,
        "usdtCostConvertedUsingEntrySnapshot": True,
        "explicitNonpositivePayedAmountIsUnknownCost": True,
    },
}

OUTPUT.write_text(
    json.dumps(result, separators=(",", ":"), ensure_ascii=False),
    encoding="utf-8",
)

print("PRIVATE RADAR ENTRY-TIME CALIBRATION OK")
print("Details retained only in temporary /tmp output")
print("Matched:", len(matches))
print("Known outcomes:", overall["knownOutcomes"])
print("Unknown outcomes:", overall["unknownOutcomes"])
print("ROI-complete:", overall["roiOrders"])
