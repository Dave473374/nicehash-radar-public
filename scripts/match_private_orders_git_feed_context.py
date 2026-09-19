"""Private Git buy-feed pre-entry HIT/MISS research.

Uses only historical buy-feed.json commits that were already committed before
each completed EasyMining order entry, with feed checked_at also before entry.
Detailed results remain ephemeral in /tmp. No BUY logic is changed.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median
import subprocess
from typing import Any

DEFAULT_ORDERS = Path("/tmp/nicehash-completed-orders.json")
DEFAULT_OUTPUT = Path("/tmp/private-order-git-feed-context.json")
BUY_FEED_PATH = "buy-feed.json"

MAX_ENDPOINT_AGE_SECONDS = 15 * 60
BASELINE_TOLERANCE_SECONDS = 10 * 60
WINDOWS_MINUTES = (15, 30, 60)
MAX_SCAN_COMMITS_PER_ORDER = 72

FEATURES = (
    "packageWorkChangePercent",
    "packageHashrateChangePercent",
    "primaryDifficultyChangePercent",
    "mergeDifficultyChangePercent",
    "primaryModelHitProbabilityChangePercentagePoints",
    "mergeModelHitProbabilityChangePercentagePoints",
    "expectedReturnChangePercentagePoints",
    "qualityVs24hChangePercentagePoints",
    "qualityVs7dChangePercentagePoints",
)


def parse_time(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        if abs(number) > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def number(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(out):
        return None
    if positive and out <= 0:
        return None
    return out


def percent_change(end: Any, start: Any) -> float | None:
    a = number(start, positive=True)
    b = number(end, positive=True)
    if a is None or b is None:
        return None
    return round((b / a - 1) * 100, 6)


def point_delta(end: Any, start: Any) -> float | None:
    a = number(start)
    b = number(end)
    if a is None or b is None:
        return None
    return round(b - a, 6)


def outcome(order: dict) -> str:
    value = order.get("isReward")
    if value is True:
        return "HIT"
    if value is False:
        return "MISS"
    return "UNKNOWN"


def legacy_282_btc_only_feed(feed: dict) -> bool:
    packages = feed.get("packages")
    if feed.get("relay_version") != "2.8.2" or not isinstance(packages, list) or not packages:
        return False
    for package in packages:
        if not isinstance(package, dict):
            return False
        if package.get("currency_market") not in (None, ""):
            return False
        if str(package.get("size") or "") not in {"S", "M"}:
            return False
        if number(package.get("price_btc"), positive=True) is None:
            return False
    return True


def resolved_currency(feed: dict, package: dict) -> tuple[str, str]:
    explicit = str(package.get("currency_market") or "").upper()
    if explicit:
        return explicit, "EXPLICIT_CURRENCY_MARKET"
    if legacy_282_btc_only_feed(feed):
        return "BTC", "LEGACY_2_8_2_BTC_ONLY_SCHEMA"
    return "", "MISSING"


def feed_checked_at(feed: dict) -> datetime | None:
    for value in (feed.get("checked_at"), feed.get("generated_at"), feed.get("updated_at")):
        dt = parse_time(value)
        if dt is not None:
            return dt
    return None


def package_point(feed: dict, package: dict, checked: datetime, committed: datetime) -> dict | None:
    primary = package.get("primary_chain") if isinstance(package.get("primary_chain"), dict) else {}
    merge = package.get("merge_chain") if isinstance(package.get("merge_chain"), dict) else {}
    currency, currency_source = resolved_currency(feed, package)
    hashrate = number(package.get("package_hashrate_hps"), positive=True)
    duration = number(package.get("duration_seconds"), positive=True)
    if hashrate is None or duration is None:
        return None
    return {
        "_quote": checked,
        "_available": committed,
        "package": package.get("name"),
        "currency": currency,
        "currencySource": currency_source,
        "primaryCoin": str(primary.get("currency") or "").upper(),
        "mergeCoin": str(merge.get("currency") or "").upper(),
        "relayVersion": feed.get("relay_version"),
        "hashrateHps": hashrate,
        "durationSeconds": duration,
        "packageWork": hashrate * duration,
        "primaryDifficulty": primary.get("network_difficulty"),
        "mergeDifficulty": merge.get("network_difficulty"),
        "primaryModelHitProbabilityPercent": primary.get("model_hit_probability_percent"),
        "mergeModelHitProbabilityPercent": merge.get("model_hit_probability_percent"),
        "expectedReturnPercent": (package.get("profitability") or {}).get("expected_return_percent"),
        "qualityVs24hPercent": (package.get("history_trend") or {}).get("expected_blocks_per_btc_vs_24h_percent"),
        "qualityVs7dPercent": (package.get("history_trend") or {}).get("expected_blocks_per_btc_vs_7d_percent"),
        "signal": package.get("final_signal"),
    }


def build_points(commit_rows: list[dict]) -> list[dict]:
    out = []
    for row in commit_rows:
        if not isinstance(row, dict):
            continue
        committed = parse_time(row.get("committedAt"))
        feed = row.get("feed")
        if committed is None or not isinstance(feed, dict):
            continue
        checked = feed_checked_at(feed)
        packages = feed.get("packages")
        if checked is None or checked > committed or not isinstance(packages, list):
            continue
        for package in packages:
            if not isinstance(package, dict):
                continue
            point = package_point(feed, package, checked, committed)
            if point is not None:
                out.append(point)
    out.sort(key=lambda row: (row["_quote"], row["_available"], str(row.get("package") or "")))
    return out


def exact_points(order: dict, points: list[dict], start: datetime) -> list[dict]:
    package = str(order.get("packageName") or "")
    coin = str(order.get("soloMiningCoin") or "").upper()
    merge = str(order.get("soloMiningMergeCoin") or "").upper()
    currency = str(order.get("currencyMarket") or "").upper()
    rows = []
    for point in points:
        if point["_quote"] > start or point["_available"] > start:
            continue
        if point.get("package") != package:
            continue
        if point.get("primaryCoin") != coin:
            continue
        if point.get("currency") != currency:
            continue
        point_merge = str(point.get("mergeCoin") or "").upper()
        if merge and point_merge and point_merge != merge:
            continue
        rows.append(point)
    return rows


def same_series(a: dict, b: dict) -> bool:
    return all(a.get(key) == b.get(key) for key in (
        "package", "currency", "primaryCoin", "mergeCoin", "relayVersion"
    ))


def window_features(endpoint: dict, baseline: dict, horizon: int, target: datetime, start: datetime) -> dict:
    return {
        "horizonMinutes": horizon,
        "status": "MATCHED",
        "baselineDistanceSeconds": round((target - baseline["_quote"]).total_seconds(), 3),
        "endpointAgeSeconds": round((start - endpoint["_quote"]).total_seconds(), 3),
        "endpointAvailabilityAgeSeconds": round((start - endpoint["_available"]).total_seconds(), 3),
        "packageWorkChangePercent": percent_change(endpoint.get("packageWork"), baseline.get("packageWork")),
        "packageHashrateChangePercent": percent_change(endpoint.get("hashrateHps"), baseline.get("hashrateHps")),
        "primaryDifficultyChangePercent": percent_change(endpoint.get("primaryDifficulty"), baseline.get("primaryDifficulty")),
        "mergeDifficultyChangePercent": percent_change(endpoint.get("mergeDifficulty"), baseline.get("mergeDifficulty")),
        "primaryModelHitProbabilityChangePercentagePoints": point_delta(endpoint.get("primaryModelHitProbabilityPercent"), baseline.get("primaryModelHitProbabilityPercent")),
        "mergeModelHitProbabilityChangePercentagePoints": point_delta(endpoint.get("mergeModelHitProbabilityPercent"), baseline.get("mergeModelHitProbabilityPercent")),
        "expectedReturnChangePercentagePoints": point_delta(endpoint.get("expectedReturnPercent"), baseline.get("expectedReturnPercent")),
        "qualityVs24hChangePercentagePoints": point_delta(endpoint.get("qualityVs24hPercent"), baseline.get("qualityVs24hPercent")),
        "qualityVs7dChangePercentagePoints": point_delta(endpoint.get("qualityVs7dPercent"), baseline.get("qualityVs7dPercent")),
        "baselineSignal": baseline.get("signal"),
        "endpointSignal": endpoint.get("signal"),
    }


def build_order_context(order: dict, points: list[dict]) -> dict:
    start = parse_time(order.get("startTs"))
    if start is None:
        return {"status": "INVALID_ORDER_TIME", "windows": []}

    rows = exact_points(order, points, start)
    if not rows:
        return {"status": "NO_CAUSAL_EXACT_GIT_FEED_POINT", "windows": []}

    endpoint = rows[-1]
    endpoint_age = (start - endpoint["_quote"]).total_seconds()
    if endpoint_age > MAX_ENDPOINT_AGE_SECONDS:
        return {
            "status": "NO_FRESH_ENDPOINT",
            "endpointAgeSeconds": round(endpoint_age, 3),
            "windows": [],
        }

    series = [row for row in rows if same_series(row, endpoint)]
    windows = []
    for horizon in WINDOWS_MINUTES:
        target = start - timedelta(minutes=horizon)
        candidates = [
            row for row in series
            if row["_quote"] <= target and row["_available"] <= start
        ]
        if not candidates:
            windows.append({
                "horizonMinutes": horizon,
                "status": "NO_BASELINE_AT_OR_BEFORE_TARGET",
            })
            continue
        baseline = candidates[-1]
        distance = (target - baseline["_quote"]).total_seconds()
        if distance > BASELINE_TOLERANCE_SECONDS:
            windows.append({
                "horizonMinutes": horizon,
                "status": "BASELINE_TOO_FAR_FROM_TARGET",
                "baselineDistanceSeconds": round(distance, 3),
            })
            continue
        windows.append(window_features(endpoint, baseline, horizon, target, start))

    return {
        "status": "FRESH_ENDPOINT",
        "endpointAgeSeconds": round(endpoint_age, 3),
        "currencySource": endpoint.get("currencySource"),
        "relayVersion": endpoint.get("relayVersion"),
        "windows": windows,
    }


def summarize(rows: list[dict]) -> dict:
    orders = Counter()
    endpoint_status = Counter()
    grouped = defaultdict(list)

    for row in rows:
        package = str(row.get("packageName") or "UNKNOWN")
        result = str(row.get("outcome") or "UNKNOWN")
        context = row.get("gitFeedPreEntry") if isinstance(row.get("gitFeedPreEntry"), dict) else {}
        orders[(package, result)] += 1
        endpoint_status[(package, result, str(context.get("status") or "UNKNOWN"))] += 1
        for window in context.get("windows") or []:
            if isinstance(window, dict) and window.get("status") == "MATCHED":
                grouped[(package, int(window["horizonMinutes"]), result)].append(window)

    by_group = {}
    package_horizon = defaultdict(dict)
    for (package, horizon, result), items in sorted(grouped.items()):
        summary = {"matchedOrders": len(items)}
        for field in FEATURES:
            values = [
                float(item[field])
                for item in items
                if number(item.get(field)) is not None
            ]
            summary["median" + field[0].upper() + field[1:]] = (
                round(median(values), 6) if values else None
            )
        key = f"{package}|{horizon}m|{result}"
        by_group[key] = summary
        package_horizon[(package, horizon)][result] = summary

    comparisons = {}
    for (package, horizon), outcomes in sorted(package_horizon.items()):
        hit = outcomes.get("HIT")
        miss = outcomes.get("MISS")
        if not hit or not miss:
            continue
        comp = {
            "hitOrders": hit["matchedOrders"],
            "missOrders": miss["matchedOrders"],
        }
        for field in FEATURES:
            metric = "median" + field[0].upper() + field[1:]
            hv, mv = hit.get(metric), miss.get(metric)
            comp["hitMinusMiss" + field[0].upper() + field[1:]] = (
                round(hv - mv, 6)
                if isinstance(hv, (int, float)) and isinstance(mv, (int, float))
                else None
            )
        comparisons[f"{package}|{horizon}m"] = comp

    return {
        "role": "PRIVATE_GIT_FEED_PRE_ENTRY_HIT_MISS_DESCRIPTIVE",
        "ordersByPackageOutcome": {
            f"{p}|{o}": n for (p, o), n in sorted(orders.items())
        },
        "endpointStatusByPackageOutcome": {
            f"{p}|{o}|{s}": n for (p, o, s), n in sorted(endpoint_status.items())
        },
        "byPackageHorizonOutcome": by_group,
        "hitMinusMiss": comparisons,
        "canRaiseSignal": False,
    }


def analyze(order_doc: dict, commit_rows: list[dict]) -> dict:
    orders = order_doc.get("list")
    if not isinstance(orders, list):
        raise ValueError("Private order document missing list")
    points = build_points(commit_rows)
    rows = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        rows.append({
            "packageName": order.get("packageName"),
            "outcome": outcome(order),
            "gitFeedPreEntry": build_order_context(order, points),
        })
    return {
        "schemaVersion": 1,
        "source": "PRIVATE_GIT_BUY_FEED_PRE_ENTRY_EPHEMERAL",
        "privateDataPersistedToRepository": False,
        "privateDataUploadedAsArtifact": False,
        "networkRequestsMade": 0,
        "gitHistoryReadOnly": True,
        "currentProductionModelChanged": False,
        "canRaiseSignal": False,
        "automaticPurchase": False,
        "automaticCancel": False,
        "settings": {
            "maxFreshEndpointAgeSeconds": MAX_ENDPOINT_AGE_SECONDS,
            "baselineToleranceSeconds": BASELINE_TOLERANCE_SECONDS,
            "windowsMinutes": list(WINDOWS_MINUTES),
            "commitMustPrecedeOrderEntry": True,
            "feedCheckedAtMustPrecedeOrderEntry": True,
            "sameRelayVersionWithinWindow": True,
            "legacy282BtcOnlySchemaBridge": True,
        },
        "summary": summarize(rows),
        "orders": rows,
        "verdict": "PRIVATE_GIT_FEED_HIT_MISS_DESCRIPTIVE_NO_AUTOMATIC_EDGE_CLAIM",
        "limitations": [
            "Completed orders are user-selected rather than randomized trials.",
            "Only one Palladium S HIT is currently available, so HIT/MISS differences are exploratory.",
            "Git commit availability is required in addition to feed checked_at to prevent future leakage.",
            "No feature here can change CURRENT/final_signal without separate time-separated validation.",
        ],
    }


def git_commit_index() -> list[tuple[str, datetime]]:
    proc = subprocess.run(
        ["git", "log", "--reverse", "--format=%H%x09%cI", "--", BUY_FEED_PATH],
        check=True,
        capture_output=True,
        text=True,
    )
    out = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        sha, stamp = line.split("\t", 1)
        dt = parse_time(stamp)
        if dt is not None:
            out.append((sha.strip(), dt))
    return out


def load_feed_at_commit(sha: str) -> dict | None:
    try:
        proc = subprocess.run(
            ["git", "show", f"{sha}:{BUY_FEED_PATH}"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_relevant_commits(order_doc: dict) -> list[dict]:
    orders = [row for row in order_doc.get("list", []) if isinstance(row, dict)]
    starts = [parse_time(row.get("startTs")) for row in orders]
    starts = [dt for dt in starts if dt is not None]
    index = git_commit_index()
    times = [dt for _, dt in index]
    needed = set()
    for start in starts:
        idx = bisect_right(times, start)
        lower = max(0, idx - MAX_SCAN_COMMITS_PER_ORDER)
        needed.update(sha for sha, _ in index[lower:idx])

    lookup = dict(index)
    rows = []
    for sha in needed:
        feed = load_feed_at_commit(sha)
        if feed is not None:
            rows.append({
                "committedAt": lookup[sha].isoformat(),
                "feed": feed,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=DEFAULT_ORDERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.orders.exists():
        parser.error(f"Missing input: {args.orders}")
    if args.output.resolve() == args.orders.resolve():
        parser.error("Output must differ from input")

    order_doc = json.loads(args.orders.read_text(encoding="utf-8"))
    if not isinstance(order_doc, dict):
        parser.error("Private order document must be an object")

    result = analyze(order_doc, load_relevant_commits(order_doc))
    args.output.write_text(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print("PRIVATE GIT BUY-FEED PRE-ENTRY ANALYSIS OK")
    print("Detailed order data retained only in /tmp; no public artifact created.")


if __name__ == "__main__":
    main()
