import json
from pathlib import Path

BUY_FEED = Path("buy-feed.json")
CALIBRATION = Path("calibration/global-order-calibration.json")


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    return None


def index_signal_stats(report):
    result = {}

    for row in report.get("signalStats") or []:
        signal = row.get("signal")
        if signal:
            result[str(signal)] = row

    return result


def build_shadow_for_package(package, report, signal_stats, overall):
    current_signal = package.get("final_signal")
    confidence = str(report.get("confidence") or "UNKNOWN").upper()

    stats = signal_stats.get(current_signal) or {}
    observed_orders = int(stats.get("orders") or 0)
    observed_hits = int(stats.get("hits") or 0)
    observed_rate = as_float(stats.get("hitRatePercent"))
    overall_rate = as_float(overall.get("hitRatePercent"))

    lift = None
    if observed_rate is not None and overall_rate not in (None, 0):
        lift = round(observed_rate / overall_rate, 4)

    # Shadow v1 is deliberately observe-only. LOW-confidence calibration
    # is never allowed to override the CURRENT production signal.
    calibrated_signal = current_signal

    status = (
        "LOW_CONFIDENCE_OBSERVE_ONLY"
        if confidence == "LOW"
        else "OBSERVE_ONLY_V1"
    )

    rationale = (
        "Calibration confidence is LOW; NEW/CALIBRATED mirrors CURRENT "
        "and records global HIT/MISS evidence without overriding production."
        if confidence == "LOW"
        else
        "Shadow model v1 is observe-only; CURRENT remains authoritative "
        "until a separately approved calibrated decision rule is enabled."
    )

    return {
        "model_version": 1,
        "status": status,
        "signal": calibrated_signal,
        "changed_from_current": calibrated_signal != current_signal,
        "confidence": confidence,
        "current_signal": current_signal,
        "evidence": {
            "total_matched_orders": int(overall.get("orders") or 0),
            "total_hits": int(overall.get("hits") or 0),
            "total_hit_rate_percent": overall_rate,
            "current_signal_orders": observed_orders,
            "current_signal_hits": observed_hits,
            "current_signal_hit_rate_percent": observed_rate,
            "current_signal_hit_rate_lift_vs_overall": lift,
        },
        "rationale": rationale,
    }


if not BUY_FEED.exists():
    raise SystemExit(f"BUY feed missing: {BUY_FEED}")

if not CALIBRATION.exists():
    raise SystemExit(f"Calibration report missing: {CALIBRATION}")

feed = json.loads(BUY_FEED.read_text(encoding="utf-8"))
report = json.loads(CALIBRATION.read_text(encoding="utf-8"))

if feed.get("status") != "BUY FEED OK" or feed.get("ok") is not True:
    raise SystemExit("Refusing to shadow-annotate an unhealthy BUY feed")

if report.get("modelUse") != "NEW_CALIBRATED_SHADOW_ONLY":
    raise SystemExit("Calibration report is not approved for shadow use")

privacy = report.get("privacy") or {}
if privacy.get("reportContainsAggregatesOnly") is not True:
    raise SystemExit("Calibration report is not aggregate-only")

if report.get("currentProductionModelChanged") is not False:
    raise SystemExit("Calibration report unexpectedly marks CURRENT as changed")

packages = feed.get("packages")
if not isinstance(packages, list):
    raise SystemExit("BUY feed packages missing")

overall = report.get("overall") or {}
signal_stats = index_signal_stats(report)

for package in packages:
    if not isinstance(package, dict):
        continue

    shadow = build_shadow_for_package(
        package,
        report,
        signal_stats,
        overall,
    )

    package["calibrated_signal"] = shadow["signal"]
    package["calibrated_shadow"] = shadow

feed["calibrated_shadow"] = {
    "model_version": 1,
    "model_use": "NEW_CALIBRATED_SHADOW_ONLY",
    "production_model": "CURRENT",
    "production_model_changed": False,
    "confidence": report.get("confidence"),
    "matched_orders": int(overall.get("orders") or 0),
    "hits": int(overall.get("hits") or 0),
    "misses": int(overall.get("misses") or 0),
    "observed_hit_rate_percent": as_float(
        overall.get("hitRatePercent")
    ),
    "roi_status": (report.get("roi") or {}).get("status"),
    "source_report_version": report.get("reportVersion"),
    "policy": (
        "Shadow v1 is observe-only. LOW-confidence calibration cannot "
        "override CURRENT. Any future decision-rule activation requires "
        "a separately approved model version."
    ),
}

BUY_FEED.write_text(
    json.dumps(feed, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print("CALIBRATED SHADOW APPLIED")
print("Packages annotated:", len(packages))
print("Confidence:", report.get("confidence"))
print("Matched orders:", int(overall.get("orders") or 0))
print("Hits:", int(overall.get("hits") or 0))
print("CURRENT production fields were not modified")
