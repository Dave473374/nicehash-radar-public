import json
from pathlib import Path
from datetime import datetime, timezone

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
    private_known = int(p.get("knownOutcomes") or 0)
    private_hits = int(p.get("hits") or 0)

    if private_known == 0:
        private_status = "NO_KNOWN_PRIVATE_OUTCOMES"
    elif private_known < 5:
        private_status = "VERY_SMALL_SAMPLE"
    elif private_known < 20:
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
            "knownOutcomes": private_known,
            "unknownOutcomes": int(p.get("unknownOutcomes") or 0),
            "hits": private_hits,
            "misses": int(p.get("misses") or 0),
            "hitRatePercent": p.get("hitRatePercent"),
            "sampleStatus": private_status,
        },
        "rewardTimeEventEvidence": {
            "matchedSuccessfulEvents": int(e.get("matched_events") or 0),
            "entryTimeEligible": False,
            "note": "Reward-time context only; never used as a HIT-rate denominator or entry-time validation.",
        },
    })

market = {
    "snapshotCount": len(market_rows),
    "status": "WARMING_UP",
    "coverageHours": 0.0,
    "latestAgeMinutes": None,
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
        now = datetime.now(timezone.utc)
        latest = times[-1] if times else None
        if latest is not None:
            market["latestAgeMinutes"] = round(
                (now - latest).total_seconds() / 60,
                2,
            )

        latest_row = market_rows[-1] if market_rows else {}
        latest_algorithms = latest_row.get("algorithms") or {}
        latest_complete = bool(latest_algorithms) and all(
            isinstance(values, dict)
            and isinstance(values.get("priceRaw"), (int, float))
            and isinstance(values.get("orders"), (int, float))
            and isinstance(values.get("speedRaw"), (int, float))
            for values in latest_algorithms.values()
        )

        if (
            len(market_rows) >= 48
            and market["coverageHours"] >= 10
            and market["latestAgeMinutes"] is not None
            and 0 <= market["latestAgeMinutes"] <= 10
            and latest_complete
        ):
            market["status"] = "READY_FOR_CONTEXT"
        elif (
            market["latestAgeMinutes"] is not None
            and market["latestAgeMinutes"] > 10
        ):
            market["status"] = "STALE"
        elif not latest_complete:
            market["status"] = "INVALID_DATA"
    except Exception:
        pass

private_overall = private.get("overall") or {}

scorecard = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
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
            if private_overall.get("totalCostBtcEquivalent") not in (None, 0)
            else "LIMITED"
        ),
        "note": "Transient /tmp only; not committed.",
    },
    "rewardTimeEventEvidence": {
        "matchedSuccessfulEvents": int(events.get("matched_events_total") or 0),
        "entryTimeEligible": False,
        "note": "Reward-time successful-event context only; never combined into order HIT rate or used as entry-time validation.",
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
print("Reward-time event evidence:", scorecard["rewardTimeEventEvidence"]["matchedSuccessfulEvents"])
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
        "| reward_time_events=", row["rewardTimeEventEvidence"]["matchedSuccessfulEvents"],
    )
print("Private data committed: NO")
print("CURRENT changed: NO")
