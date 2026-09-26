"""Measure whether hourly phone checks are becoming a practical alert bottleneck.

Public/local files only. This report never changes CURRENT, BUY signals, or orders.
"""
from __future__ import annotations
try:
    from radar_snapshot_archive import history_exists, read_history_text
except ModuleNotFoundError:  # Also support package/spec imports from repository root.
    from scripts.radar_snapshot_archive import history_exists, read_history_text
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

SNAPSHOTS = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("research/alert-latency-report.json")

LOOKBACK_DAYS = 7
MAX_CAPTURE_DELAY_MINUTES = 7.0
MAX_CONTIGUOUS_GAP_MINUTES = 12.0
SHORT_WINDOW_MAX_MINUTES = 45.0
VERY_SHORT_STRONG_MAX_MINUTES = 30.0
MIN_RESOLVED_WINDOWS = 3
MIN_SHORT_WINDOWS = 2
MIN_SHORT_RATE = 0.5
SIGNALS = {"BUY NOW", "STRONG BUY"}
MATH_VERIFIED = {"PASS", "NOT_APPLICABLE"}


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except Exception:
        return None


def finite(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def canonical(value):
    def encode(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Unsupported canonical type: {type(obj).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode)


def primary(package):
    size = str(package.get("size") or "").upper()
    market = str(package.get("currency_market") or "").upper()
    return size == "S" or (market == "USDT" and size in {"5", "20"})


def verified_phone_candidate(package):
    """Strict subset used only to decide whether faster delivery is worthwhile."""
    if not isinstance(package, dict) or package.get("available") is not True:
        return False
    if not primary(package):
        return False
    signal = str(package.get("final_signal") or "")
    if signal not in SIGNALS:
        return False
    profitability = package.get("profitability") or {}
    economics = package.get("economics") or {}
    math_shadow = package.get("math_consistency_shadow") or {}
    ev = profitability.get("expected_return_percent")
    if profitability.get("complete") is not True or not finite(ev) or float(ev) < 95.0:
        return False
    if math_shadow.get("status") not in MATH_VERIFIED:
        return False
    cost = economics.get("package_cost_eur")
    if cost is not None and (not finite(cost) or float(cost) <= 0):
        return False
    return True


def load_snapshots(path, now):
    counts = Counter()
    by_time = {}
    conflicts = set()
    if not history_exists(path):
        return [], counts
    for raw in read_history_text(path, encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            counts["invalidJson"] += 1
            continue
        if not isinstance(row, dict):
            counts["invalidRow"] += 1
            continue
        feed = row.get("feed")
        if not isinstance(feed, dict):
            counts["missingFeed"] += 1
            continue
        if (
            feed.get("status") != "BUY FEED OK"
            or feed.get("ok") is not True
            or feed.get("upstream_status") != 200
            or feed.get("market_status") != "MARKET OK"
            or not isinstance(feed.get("packages"), list)
            or not feed.get("packages")
        ):
            counts["unhealthyFeed"] += 1
            continue
        checked = parse_time(feed.get("checked_at"))
        available = parse_time(row.get("collected_at"))
        if checked is None or available is None or checked > now or available > now:
            counts["invalidTime"] += 1
            continue
        delay = (available - checked).total_seconds() / 60
        if delay < -1 or delay > MAX_CAPTURE_DELAY_MINUTES:
            counts["lateCapture"] += 1
            continue
        key = checked.isoformat()
        value = {
            "checked": checked,
            "available": available,
            "relayVersion": str(feed.get("relay_version") or ""),
            "decisionEngine": str(feed.get("decision_engine") or ""),
            "packages": feed["packages"],
        }
        if key in conflicts:
            continue
        if key in by_time and canonical(by_time[key]) != canonical(value):
            conflicts.add(key)
            by_time.pop(key, None)
            counts["conflictingSnapshot"] += 1
        elif key not in by_time:
            by_time[key] = value
        else:
            counts["duplicateSnapshot"] += 1
    return [v for k, v in sorted(by_time.items()) if k not in conflicts], counts


def latest_regime(rows):
    if not rows:
        return None
    latest = rows[-1]
    return (latest["relayVersion"], latest["decisionEngine"])


def points_for_package(rows, name, regime, start):
    points = []
    for snap in rows:
        if snap["checked"] < start:
            continue
        if (snap["relayVersion"], snap["decisionEngine"]) != regime:
            continue
        package = next((p for p in snap["packages"] if isinstance(p, dict) and p.get("name") == name), None)
        points.append({
            "at": snap["checked"],
            "qualifies": verified_phone_candidate(package) if package else False,
            "signal": str((package or {}).get("final_signal") or ""),
            "ev": ((package or {}).get("profitability") or {}).get("expected_return_percent"),
        })
    return points


def build_windows(points, package):
    windows = []
    active = None

    def censor(reason):
        nonlocal active
        if active is not None:
            active["status"] = "CENSORED"
            active["closureReason"] = reason
            windows.append(active)
            active = None

    for point in points:
        if active is not None:
            gap = (point["at"] - active["lastQualifiedAt"]).total_seconds() / 60
            if gap > MAX_CONTIGUOUS_GAP_MINUTES:
                censor("DATA_GAP")

        if point["qualifies"]:
            if active is None:
                active = {
                    "package": package,
                    "startAt": point["at"],
                    "lastQualifiedAt": point["at"],
                    "maxSignal": point["signal"],
                    "minEvPercent": float(point["ev"]),
                    "status": "OPEN",
                    "closureReason": None,
                }
            else:
                active["lastQualifiedAt"] = point["at"]
                active["minEvPercent"] = min(active["minEvPercent"], float(point["ev"]))
                if point["signal"] == "STRONG BUY":
                    active["maxSignal"] = "STRONG BUY"
        elif active is not None:
            gap = (point["at"] - active["lastQualifiedAt"]).total_seconds() / 60
            if gap <= MAX_CONTIGUOUS_GAP_MINUTES:
                active["status"] = "RESOLVED"
                active["endAt"] = point["at"]
                active["closureReason"] = "NEXT_VALID_SNAPSHOT_NOT_QUALIFIED"
                active["observedLowerBoundMinutes"] = round(
                    (active["lastQualifiedAt"] - active["startAt"]).total_seconds() / 60, 3
                )
                active["observedUpperBoundMinutes"] = round(
                    (point["at"] - active["startAt"]).total_seconds() / 60, 3
                )
                windows.append(active)
                active = None
            else:
                censor("DATA_GAP")

    if active is not None:
        windows.append(active)

    serial = []
    for window in windows:
        item = dict(window)
        for key in ("startAt", "lastQualifiedAt", "endAt"):
            if isinstance(item.get(key), datetime):
                item[key] = item[key].isoformat()
        serial.append(item)
    return serial


def evaluate(rows, now):
    regime = latest_regime(rows)
    if regime is None:
        return {
            "status": "NO_DATA",
            "fiveMinutePushRecommended": False,
            "verdict": "KEEP_HOURLY_FOR_NOW",
            "windows": [],
        }

    lookback_start = now - timedelta(days=LOOKBACK_DAYS)
    names = set()
    for snap in rows:
        if snap["checked"] < lookback_start or (snap["relayVersion"], snap["decisionEngine"]) != regime:
            continue
        for package in snap["packages"]:
            if isinstance(package, dict) and primary(package) and package.get("name"):
                names.add(str(package["name"]))

    windows = []
    for name in sorted(names):
        windows.extend(build_windows(points_for_package(rows, name, regime, lookback_start), name))

    resolved = [w for w in windows if w["status"] == "RESOLVED"]
    short = [w for w in resolved if w["observedUpperBoundMinutes"] <= SHORT_WINDOW_MAX_MINUTES]
    very_short_strong = [
        w for w in resolved
        if w["maxSignal"] == "STRONG BUY"
        and w["observedUpperBoundMinutes"] <= VERY_SHORT_STRONG_MAX_MINUTES
    ]
    rate = len(short) / len(resolved) if resolved else 0.0
    sufficient = len(resolved) >= MIN_RESOLVED_WINDOWS
    recommended = sufficient and (
        (len(short) >= MIN_SHORT_WINDOWS and rate >= MIN_SHORT_RATE)
        or bool(very_short_strong)
    )
    status = "RECOMMENDED" if recommended else "ENOUGH_EVIDENCE_KEEP_HOURLY" if sufficient else "INSUFFICIENT_EVIDENCE"
    reason = (
        "Verified PRIMARY BUY NOW/STRONG BUY windows are often short enough for hourly polling to miss."
        if recommended
        else "Enough resolved windows exist, but short-window evidence does not justify faster phone polling."
        if sufficient
        else "Need at least 3 resolved verified PRIMARY BUY NOW/STRONG BUY windows before changing delivery infrastructure."
    )
    return {
        "status": status,
        "fiveMinutePushRecommended": recommended,
        "verdict": "UPGRADE_PHONE_DELIVERY" if recommended else "KEEP_HOURLY_FOR_NOW",
        "reason": reason,
        "currentRegime": {"relayVersion": regime[0], "decisionEngine": regime[1]},
        "lookbackDays": LOOKBACK_DAYS,
        "resolvedWindowCount": len(resolved),
        "shortWindowCount": len(short),
        "veryShortStrongWindowCount": len(very_short_strong),
        "shortWindowRate": round(rate, 4),
        "thresholds": {
            "minimumResolvedWindows": MIN_RESOLVED_WINDOWS,
            "shortWindowMaxMinutes": SHORT_WINDOW_MAX_MINUTES,
            "minimumShortWindows": MIN_SHORT_WINDOWS,
            "minimumShortRate": MIN_SHORT_RATE,
            "veryShortStrongMaxMinutes": VERY_SHORT_STRONG_MAX_MINUTES,
            "minimumExpectedReturnPercent": 95.0,
            "mathStatusesAccepted": sorted(MATH_VERIFIED),
            "signalsAccepted": sorted(SIGNALS),
            "primaryOnly": True,
        },
        "windows": sorted(windows, key=lambda w: (w["startAt"], w["package"]))[-20:],
        "limitations": [
            "This is infrastructure evidence, not a BUY signal and not a profitability backtest.",
            "Only PRIMARY BUY NOW/STRONG BUY windows with EV >=95% and MATH PASS/N/A can trigger the recommendation.",
            "GOOD, WARNING, CRITICAL and UNKNOWN math states do not justify a faster-push recommendation.",
            "Window duration is bounded by observed snapshots; data gaps are censored rather than guessed.",
            "A short window means hourly polling can miss it; it does not prove a particular historical phone check actually missed it.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--now")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be an aware ISO timestamp")
    rows, counts = load_snapshots(args.snapshots, now)
    report = {
        "schemaVersion": 1,
        "generatedAt": now.isoformat(),
        "role": "PHONE_DELIVERY_LATENCY_RESEARCH_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseBuySignal": False,
        "automaticPurchase": False,
        "source": "calibration/radar-snapshots.jsonl",
        "inputCounts": dict(counts),
        **evaluate(rows, now),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("ALERT LATENCY REPORT OK |", report["status"], "| 5-min:", report["fiveMinutePushRecommended"])


if __name__ == "__main__":
    main()
