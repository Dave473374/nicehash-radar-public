"""Audit whether Git buy-feed history can fill private Radar entry gaps.

Private, ephemeral and read-only. The CLI scans historical buy-feed.json
commits already present in the checked-out Git history, but the persisted
/tmp result contains only redacted package/outcome aggregate coverage.
No private timestamps, amounts, IDs or ROI are emitted.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
from typing import Any

DEFAULT_ORDERS = Path("/tmp/nicehash-completed-orders.json")
DEFAULT_OUTPUT = Path("/tmp/private-order-git-feed-coverage.json")
BUY_FEED_PATH = "buy-feed.json"
MAX_SCAN_COMMITS_PER_ORDER = 36


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


def resolved_currency(feed: dict, package: dict) -> str:
    explicit = str(package.get("currency_market") or "").upper()
    if explicit:
        return explicit
    if legacy_282_btc_only_feed(feed):
        return "BTC"
    return ""


def feed_checked_at(feed: dict) -> datetime | None:
    for value in (feed.get("checked_at"), feed.get("generated_at"), feed.get("updated_at")):
        dt = parse_time(value)
        if dt is not None:
            return dt
    return None


def exact_package(order: dict, feed: dict) -> bool:
    package_name = str(order.get("packageName") or "")
    coin = str(order.get("soloMiningCoin") or "").upper()
    merge = str(order.get("soloMiningMergeCoin") or "").upper()
    currency = str(order.get("currencyMarket") or "").upper()
    packages = feed.get("packages")
    if not isinstance(packages, list):
        return False
    for package in packages:
        if not isinstance(package, dict) or package.get("name") != package_name:
            continue
        primary = package.get("primary_chain") if isinstance(package.get("primary_chain"), dict) else {}
        merge_chain = package.get("merge_chain") if isinstance(package.get("merge_chain"), dict) else {}
        if str(primary.get("currency") or "").upper() != coin:
            continue
        package_merge = str(merge_chain.get("currency") or "").upper()
        if merge and package_merge and package_merge != merge:
            continue
        if resolved_currency(feed, package) != currency:
            continue
        return True
    return False


def age_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "NO_EXACT_CAUSAL_GIT_FEED"
    minutes = seconds / 60
    if minutes <= 15:
        return "FRESH_LE_15M"
    if minutes <= 30:
        return "STALE_15_30M"
    if minutes <= 60:
        return "STALE_30_60M"
    if minutes <= 120:
        return "STALE_60_120M"
    return "STALE_GT_120M"


def analyze(order_doc: dict, commit_rows: list[dict]) -> dict:
    orders = order_doc.get("list")
    if not isinstance(orders, list):
        raise ValueError("Private order document missing list")

    prepared = []
    for row in commit_rows:
        if not isinstance(row, dict):
            continue
        committed = parse_time(row.get("committedAt"))
        feed = row.get("feed")
        if committed is None or not isinstance(feed, dict):
            continue
        checked = feed_checked_at(feed)
        if checked is None or checked > committed:
            continue
        prepared.append({
            "_committed": committed,
            "_checked": checked,
            "feed": feed,
        })
    prepared.sort(key=lambda row: row["_committed"])
    committed_times = [row["_committed"] for row in prepared]

    orders_by_outcome = Counter()
    buckets = Counter()
    fresh_exact = Counter()
    no_causal_commit = Counter()

    for order in orders:
        if not isinstance(order, dict):
            continue
        package = str(order.get("packageName") or "UNKNOWN")
        result = outcome(order)
        orders_by_outcome[(package, result)] += 1
        start = parse_time(order.get("startTs"))
        if start is None:
            buckets[(package, result, "INVALID_ORDER_TIME")] += 1
            continue

        idx = bisect_right(committed_times, start)
        if idx == 0:
            no_causal_commit[(package, result)] += 1
            buckets[(package, result, "NO_CAUSAL_GIT_COMMIT")] += 1
            continue

        chosen_age = None
        lower = max(0, idx - MAX_SCAN_COMMITS_PER_ORDER)
        for row in reversed(prepared[lower:idx]):
            if row["_checked"] > start:
                continue
            if exact_package(order, row["feed"]):
                chosen_age = (start - row["_checked"]).total_seconds()
                break

        bucket = age_bucket(chosen_age)
        buckets[(package, result, bucket)] += 1
        if bucket == "FRESH_LE_15M":
            fresh_exact[(package, result)] += 1

    return {
        "schemaVersion": 1,
        "source": "PRIVATE_ORDER_GIT_BUY_FEED_COVERAGE_EPHEMERAL",
        "privateDataPersistedToRepository": False,
        "privateDataUploadedAsArtifact": False,
        "networkRequestsMade": 0,
        "gitHistoryReadOnly": True,
        "containsOrderIds": False,
        "containsTimestamps": False,
        "containsAmountsOrRoi": False,
        "ordersByPackageOutcome": {
            f"{p}|{o}": n for (p, o), n in sorted(orders_by_outcome.items())
        },
        "gitFeedAgeBucketsByPackageOutcome": {
            f"{p}|{o}|{b}": n for (p, o, b), n in sorted(buckets.items())
        },
        "freshExactGitFeedByPackageOutcome": {
            f"{p}|{o}": n for (p, o), n in sorted(fresh_exact.items())
        },
        "noCausalGitCommitByPackageOutcome": {
            f"{p}|{o}": n for (p, o), n in sorted(no_causal_commit.items())
        },
        "canRaiseSignal": False,
        "verdict": "COVERAGE_AUDIT_ONLY",
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

    # Materialize only commits near at least one private order. Commit dates are
    # public; private order times are used only inside the runner and never
    # written to the redacted result.
    orders = [row for row in order_doc.get("list", []) if isinstance(row, dict)]
    starts = [parse_time(row.get("startTs")) for row in orders]
    starts = [dt for dt in starts if dt is not None]
    index = git_commit_index()
    needed = set()
    commit_times = [dt for _, dt in index]
    for start in starts:
        idx = bisect_right(commit_times, start)
        lower = max(0, idx - MAX_SCAN_COMMITS_PER_ORDER)
        needed.update(sha for sha, _ in index[lower:idx])

    commit_rows = []
    lookup = dict(index)
    for sha in needed:
        feed = load_feed_at_commit(sha)
        if feed is not None:
            commit_rows.append({
                "committedAt": lookup[sha].isoformat(),
                "feed": feed,
            })

    result = analyze(order_doc, commit_rows)
    args.output.write_text(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print("PRIVATE GIT BUY-FEED COVERAGE AUDIT OK")
    print("Only redacted aggregate coverage written to /tmp; no private timestamps printed.")


if __name__ == "__main__":
    main()
