import json
from pathlib import Path
from datetime import datetime

LEGACY = Path("calibration/global-order-calibration.json")
EVENTS = Path("calibration/calibration-report.json")
MARKET = Path("calibration/public-market-history.jsonl")
PRIVATE = Path("/tmp/private-order-matches.json")
OUTPUT = Path("/tmp/buy-radar-evidence-scorecard.json")

SIGNALS = ["STRONG BUY", "BUY NOW", "GOOD", "WAIT", "NO BUY"]


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def signal_map(rows):
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        signal = row.get("signal")
        if signal:
            out[str(signal)] = row
    return out


legacy = load_json(LEGACY, {}) or {}
events = load_json(EVENTS, {}) or {}
private = load_json(PRIVATE, {}) or {}

market_rows = []
if MARKET.exists():
    for line in MARKET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            market_rows.append(row)

legacy_signals = signal_map(legacy.get("signalStats"))
private_signals = signal_map(private.get("signalStats"))
event_signals = events.get("signals") or {}

signal_evidence = []

for signal in SIGNALS:
    l = legacy_signals.get(signal) or {}
    p = private_signals.get(signal) or {}
    e = event_signals.get(signal) or {}

    private_orders = int(p.get("orders") or 0)
    private_hits = int(p.get("hits") or 0)

    if private_orders == 0:
        private_status = "NO_MATCHED_PRIVATE_EVIDENCE"
    elif private_orders < 5:
        private_status = "VERY_SMALL_SAMPLE"
    elif private_orders < 20:
        private_status = "SMALL_SAMPLE"
    else:
        private_status = "USABLE_SAMPLE"

    signal_evidence.append({
        "signal": signal,
        "legacy": {
            "orders": int(l.get("orders") or 0),
            "hits": int(l.get("hits") or 0),
            "hitRatePercent": l.get("hitRatePercent"),
        },
        "privateValidation": {
            "orders": private_orders,
            "hits": private_hits,
            "misses": int(p.get("misses") or 0),
            "hitRatePercent": p.get("hitRatePercent"),
            "sampleStatus": private_status,
        },
        "successfulEventEvidence": {
            "matchedSuccessfulEvents": int(e.get("matched_events") or 0),
            "note": "Numerator-only evidence; never used as a HIT-rate denominator.",
        },
    })

market = {
    "snapshotCount": len(market_rows),
    "status": "WARMING_UP",
    "coverageHours": 0.0,
}

if market_rows:
    try:
        times = sorted(
            datetime.fromisoformat(
                str(row.get("collected_at")).replace("Z", "+00:00")
            )
            for row in market_rows
            if row.get("collected_at")
        )
        if len(times) >= 2:
            market["coverageHours"] = round(
                (times[-1] - times[0]).total_seconds() / 3600,
                2,
            )
        if len(market_rows) >= 48 and market["coverageHours"] >= 10:
            market["status"] = "READY_FOR_CONTEXT"
    except Exception:
        pass

private_overall = private.get("overall") or {}

scorecard = {
    "generatedAt": datetime.utcnow().isoformat() + "Z",
    "modelUse": "VALIDATION_ONLY",
    "currentProductionModelChanged": False,
    "privateDataPersistedToRepo": False,
    "legacyEvidence": {
        "matchedOrders": int((legacy.get("overall") or {}).get("orders") or 0),
        "hits": int((legacy.get("overall") or {}).get("hits") or 0),
        "confidence": legacy.get("confidence"),
        "note": "Frozen legacy aggregate; kept separate from private validation to avoid possible overlap/double counting.",
    },
    "privateValidation": {
        "completedOrdersAvailable": int(private.get("completedOrders") or 0),
        "matchedToRadar": int(private.get("validMatchedOrders") or 0),
        "matchedHits": int(private.get("matchedRewards") or 0),
        "realizedRoiStatus": (
            "AVAILABLE_IN_TRANSIENT_MATCHES"
            if private_overall.get("totalCostBtc") not in (None, 0)
            else "LIMITED"
        ),
        "note": "Transient /tmp only; not committed.",
    },
    "successfulEventEvidence": {
        "matchedSuccessfulEvents": int(events.get("matched_events_total") or 0),
        "note": "Separate numerator-only evidence; never combined into order HIT rate.",
    },
    "publicMarket": market,
    "signals": signal_evidence,
    "policy": [
        "Never add legacy + private order counts as one HIT-rate denominator because overlap cannot be ruled out.",
        "Never use successful event counts as MISS denominator.",
        "Private validation can corroborate or challenge thresholds but cannot by itself override CURRENT while sample is small.",
        "Public market context remains informational until sufficient history exists.",
    ],
}

OUTPUT.write_text(
    json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print("BUY RADAR EVIDENCE VALIDATION")
print("Legacy matched:", scorecard["legacyEvidence"]["matchedOrders"])
print("Private matched:", scorecard["privateValidation"]["matchedToRadar"])
print("Private matched HITs:", scorecard["privateValidation"]["matchedHits"])
print("Successful event evidence:", scorecard["successfulEventEvidence"]["matchedSuccessfulEvents"])
print(
    "Public market:",
    scorecard["publicMarket"]["status"],
    "| snapshots=", scorecard["publicMarket"]["snapshotCount"],
    "| coverage_h=", scorecard["publicMarket"]["coverageHours"],
)
for row in signal_evidence:
    p = row["privateValidation"]
    l = row["legacy"]
    print(
        row["signal"],
        "| legacy=", f"{l['hits']}/{l['orders']}",
        "| private=", f"{p['hits']}/{p['orders']}",
        "| private_sample=", p["sampleStatus"],
        "| success_events=", row["successfulEventEvidence"]["matchedSuccessfulEvents"],
    )
print("Private data committed: NO")
print("CURRENT changed: NO")
