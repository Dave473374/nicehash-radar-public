import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

BUY_FEED = Path(os.getenv("BUY_RADAR_FEED", "buy-feed.json"))
STATE_FILE = Path(os.getenv("BUY_RADAR_ALERT_STATE", "alerts/alert-state.json"))
HISTORY_FILE = Path(os.getenv("BUY_RADAR_ALERT_HISTORY", "alerts/alert-history.jsonl"))
OUT_FILE = Path(os.getenv("BUY_RADAR_ALERT_OUTPUT", "/tmp/buy-radar-alerts.json"))
PUBLIC_MARKET_HISTORY = Path(
    os.getenv(
        "BUY_RADAR_PUBLIC_MARKET_HISTORY",
        "calibration/public-market-history.jsonl",
    )
)

ACTIONABLE_SIGNALS = {"GOOD", "BUY NOW", "STRONG BUY"}

BATCH_CONFIRMATION_SNAPSHOTS = 2
BATCH_MIN_EDGE_SCORE = 10.0
BATCH_MIN_EXPECTED_RETURN_PERCENT = 95.0
BATCH_MIN_Q24_PERCENT = 10.0
BATCH_MIN_Q7_PERCENT = 5.0
BATCH_SIGNALS = {"BUY NOW", "STRONG BUY"}

MARKET_MAX_AGE_MINUTES = 10.0
FUTURE_TIME_TOLERANCE_MINUTES = 1.0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def compact_number(value, digits=4):
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def median(values):
    values = sorted(float(x) for x in values if isinstance(x, (int, float)))
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def pct_vs(value, baseline):
    if not isinstance(value, (int, float)):
        return None
    if not isinstance(baseline, (int, float)) or baseline == 0:
        return None
    return round((float(value) / float(baseline) - 1) * 100, 3)


def load_public_market_history():
    if not PUBLIC_MARKET_HISTORY.exists():
        return []

    rows = []
    for line in PUBLIC_MARKET_HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


PUBLIC_MARKET_ROWS = load_public_market_history()


def public_market_context(package, now):
    primary = package.get("primary_chain") or {}
    algorithm = str(primary.get("algorithm") or "").upper()
    if not algorithm:
        return {
            "status": "UNAVAILABLE",
            "role": "INFORMATIONAL_ONLY",
            "algorithm": None,
        }

    samples = []
    for row in PUBLIC_MARKET_ROWS:
        ts = parse_time(row.get("collected_at"))
        algo = (row.get("algorithms") or {}).get(algorithm)
        if ts is None or not isinstance(algo, dict):
            continue
        samples.append((ts, algo))

    if not samples:
        return {
            "status": "UNAVAILABLE",
            "role": "INFORMATIONAL_ONLY",
            "algorithm": algorithm,
        }

    samples.sort(key=lambda item: item[0])
    latest_ts, latest = samples[-1]
    age_minutes = (now - latest_ts).total_seconds() / 60

    required_fields = ("priceRaw", "orders", "speedRaw")
    latest_complete = all(
        isinstance(latest.get(key), (int, float))
        for key in required_fields
    )

    cutoff = now - timedelta(hours=24)
    recent = [
        (ts, data)
        for ts, data in samples
        if ts >= cutoff
        and all(
            isinstance(data.get(key), (int, float))
            for key in required_fields
        )
    ]

    price_values = [data.get("priceRaw") for _, data in recent]
    order_values = [data.get("orders") for _, data in recent]
    speed_values = [data.get("speedRaw") for _, data in recent]

    coverage_hours = 0.0
    if len(recent) >= 2:
        coverage_hours = (
            recent[-1][0] - recent[0][0]
        ).total_seconds() / 3600

    if age_minutes < -FUTURE_TIME_TOLERANCE_MINUTES:
        status = "INVALID_FUTURE"
    elif age_minutes > MARKET_MAX_AGE_MINUTES:
        status = "STALE"
    elif not latest_complete:
        status = "INVALID_DATA"
    elif len(recent) >= 48 and coverage_hours >= 10:
        status = "READY"
    else:
        status = "WARMING_UP"

    ready = status == "READY"

    return {
        "status": status,
        "role": "INFORMATIONAL_ONLY",
        "algorithm": algorithm,
        "collectedAt": latest_ts.isoformat(),
        "ageMinutes": round(age_minutes, 1),
        "speedUnit": latest.get("speedUnit"),
        "orders": latest.get("orders"),
        "rigs": latest.get("rigs"),
        "priceRaw": compact_number(latest.get("priceRaw"), 12),
        "speedRaw": compact_number(latest.get("speedRaw"), 4),
        "samples24h": len(recent),
        "coverageHours24h": round(coverage_hours, 2),
        "priceVs24hMedianPercent": (
            pct_vs(latest.get("priceRaw"), median(price_values))
            if ready
            else None
        ),
        "ordersVs24hMedianPercent": (
            pct_vs(latest.get("orders"), median(order_values))
            if ready
            else None
        ),
        "speedVs24hMedianPercent": (
            pct_vs(latest.get("speedRaw"), median(speed_values))
            if ready
            else None
        ),
    }


def purchase_priority(package):
    size = str(package.get("size") or "").upper()
    market = str(package.get("currency_market") or "").upper()

    if size == "S":
        return "PRIMARY"

    if market == "USDT" and size in {"5", "20"}:
        return "PRIMARY"

    return "SECONDARY"


def feed_freshness(feed, now):
    feed_checked_at = parse_time(feed.get("checked_at"))
    feed_age_minutes = None

    if feed_checked_at is not None:
        feed_age_minutes = round(
            (now - feed_checked_at).total_seconds() / 60,
            1,
        )

    if feed_age_minutes is None:
        status = "UNKNOWN"
    elif feed_age_minutes < -FUTURE_TIME_TOLERANCE_MINUTES:
        status = "INVALID_FUTURE"
    elif feed_age_minutes <= 7:
        status = "FRESH"
    elif feed_age_minutes <= 15:
        status = "DELAYED"
    else:
        status = "STALE"

    return {
        "status": status,
        "feedCheckedAt": feed.get("checked_at"),
        "ageMinutes": feed_age_minutes,
    }


def feed_snapshot_id(feed):
    checked_at = str(feed.get("checked_at") or "")
    history_key = str(feed.get("history_key") or "")
    if checked_at or history_key:
        return checked_at + "|" + history_key
    return None


def batch_assessment(package, now, feed):
    profitability = package.get("profitability") or {}
    history = package.get("history_trend") or {}
    edge = package.get("edge_shadow") or {}
    math_shadow = package.get("math_consistency_shadow") or {}

    signal = str(package.get("final_signal") or "UNKNOWN")
    priority = purchase_priority(package)
    expected_return = profitability.get("expected_return_percent")
    q24 = history.get("expected_blocks_per_btc_vs_24h_percent")
    q7 = history.get("expected_blocks_per_btc_vs_7d_percent")
    edge_score = edge.get("edge_score")
    market = public_market_context(package, now)
    freshness = feed_freshness(feed, now)

    checks = {
        "primaryPackage": priority == "PRIMARY",
        "strongCurrentSignal": signal in BATCH_SIGNALS,
        "highEdge": (
            isinstance(edge_score, (int, float))
            and float(edge_score) >= BATCH_MIN_EDGE_SCORE
        ),
        "expectedReturn": (
            isinstance(expected_return, (int, float))
            and float(expected_return) >= BATCH_MIN_EXPECTED_RETURN_PERCENT
        ),
        "q24": (
            isinstance(q24, (int, float))
            and float(q24) >= BATCH_MIN_Q24_PERCENT
        ),
        "q7": (
            isinstance(q7, (int, float))
            and float(q7) >= BATCH_MIN_Q7_PERCENT
        ),
        "marketReady": market.get("status") == "READY",
        "freshData": freshness.get("status") == "FRESH",
        "uniqueFeedSnapshot": feed_snapshot_id(feed) is not None,
        "mathConsistency": (
            math_shadow.get("status") in {"PASS", "NOT_APPLICABLE"}
        ),
    }

    return {
        "candidate": all(checks.values()),
        "checks": checks,
        "confirmationSnapshotsRequired": BATCH_CONFIRMATION_SNAPSHOTS,
        "thresholds": {
            "signals": sorted(BATCH_SIGNALS),
            "minEdgeScore": BATCH_MIN_EDGE_SCORE,
            "minExpectedReturnPercent": BATCH_MIN_EXPECTED_RETURN_PERCENT,
            "minQ24Percent": BATCH_MIN_Q24_PERCENT,
            "minQ7Percent": BATCH_MIN_Q7_PERCENT,
        },
        "policy": (
            "Shadow/manual-only. BATCH OPPORTUNITY never buys or cancels tickets "
            "and never changes CURRENT final_signal."
        ),
    }


def package_alert_payload(package, previous_signal, now, feed):
    profitability = package.get("profitability") or {}
    history = package.get("history_trend") or {}
    primary = package.get("primary_chain") or {}
    nicehash_odds = package.get("nicehash_odds") or {}
    edge = package.get("edge_shadow") or {}
    math_shadow = package.get("math_consistency_shadow") or {}
    market = public_market_context(package, now)

    freshness = feed_freshness(feed, now)

    signal = str(package.get("final_signal") or "UNKNOWN")
    reason = (
        "ENTERED_ACTIONABLE_SET"
        if previous_signal not in ACTIONABLE_SIGNALS
        else "ACTIONABLE_SIGNAL_CHANGED"
    )

    return {
        "createdAt": now,
        "reason": reason,
        "package": package.get("name"),
        "size": package.get("size"),
        "signal": signal,
        "purchasePriority": purchase_priority(package),
        "previousSignal": previous_signal,
        "coin": primary.get("currency"),
        "currencyMarket": package.get("currency_market"),
        "priceBtc": compact_number(package.get("price_btc"), 8),
        "priceBtcEquivalent": compact_number(package.get("price_btc_equiv"), 8),
        "packageCostEur": compact_number(
            (package.get("economics") or {}).get("package_cost_eur"),
            4,
        ),
        "expectedReturnPercent": compact_number(
            profitability.get("expected_return_percent"),
            4,
        ),
        "qualityVs24hPercent": compact_number(
            history.get("expected_blocks_per_btc_vs_24h_percent"),
            4,
        ),
        "qualityVs7dPercent": compact_number(
            history.get("expected_blocks_per_btc_vs_7d_percent"),
            4,
        ),
        "nicehashOdds": nicehash_odds.get("display"),
        "breakEvenRisk": (profitability.get("break_even_risk") or {}).get("status"),
        "edgeShadow": {
            "status": edge.get("status"),
            "label": edge.get("edge_label"),
            "score": compact_number(edge.get("edge_score"), 4),
            "productionOverride": edge.get("production_override") is True,
        },
        "publicMarket": market,
        "mathConsistency": {
            "status": math_shadow.get("status"),
            "productionOverride": (
                math_shadow.get("production_override") is True
            ),
        },
        "freshness": freshness,
        "relayVersion": feed.get("relay_version"),
        "decisionEngine": feed.get("decision_engine"),
        "sourceSignalField": "final_signal",
        "automaticPurchase": False,
    }


feed = load_json(BUY_FEED)
if not isinstance(feed, dict):
    raise SystemExit(f"BUY feed missing or invalid: {BUY_FEED}")

if feed.get("status") != "BUY FEED OK" or feed.get("ok") is not True:
    raise SystemExit("Refusing to alert from an unhealthy BUY feed")

packages = feed.get("packages")
if not isinstance(packages, list) or not packages:
    raise SystemExit("BUY feed packages missing or empty")

state = load_json(STATE_FILE, default={"version": 1, "packages": {}})
if not isinstance(state, dict):
    raise SystemExit("Alert state is invalid")

state.setdefault("version", 1)
state.setdefault("packages", {})
if not isinstance(state["packages"], dict):
    raise SystemExit("Alert state packages map is invalid")

now_dt = datetime.now(timezone.utc)
now = now_dt.isoformat()
events = []

for package in packages:
    if not isinstance(package, dict):
        continue

    name = package.get("name")
    if not name:
        continue

    current_signal = str(package.get("final_signal") or "UNKNOWN")
    package_state = state["packages"].get(name) or {}
    previous_signal = package_state.get("lastObservedSignal")

    freshness = feed_freshness(feed, now_dt)
    feed_id = feed_snapshot_id(feed)

    # A stale, delayed, future-dated or unidentified feed must not mutate
    # signal dedupe state or BATCH confirmation state.
    if freshness.get("status") != "FRESH" or feed_id is None:
        continue

    changed = previous_signal != current_signal
    actionable = current_signal in ACTIONABLE_SIGNALS

    if changed and actionable:
        event = package_alert_payload(package, previous_signal, now_dt, feed)
        event["createdAt"] = now
        event["eventType"] = "SIGNAL"
        event["batchOpportunity"] = batch_assessment(
            package,
            now_dt,
            feed,
        )
        events.append(event)
        package_state["lastAlertedSignal"] = current_signal
        package_state["lastAlertedAt"] = now

    batch = batch_assessment(package, now_dt, feed)
    previous_streak = int(package_state.get("batchCandidateStreak") or 0)
    last_candidate_feed_id = package_state.get("lastBatchCandidateFeedId")

    if batch["candidate"]:
        if feed_id != last_candidate_feed_id:
            current_streak = previous_streak + 1
            package_state["lastBatchCandidateFeedId"] = feed_id
        else:
            current_streak = previous_streak
    else:
        current_streak = 0
        package_state["batchActive"] = False
        package_state["lastBatchCandidateFeedId"] = feed_id

    batch["confirmationStreak"] = current_streak
    batch["feedSnapshotId"] = feed_id
    batch_confirmed = (
        batch["candidate"]
        and current_streak >= BATCH_CONFIRMATION_SNAPSHOTS
    )

    if batch_confirmed and package_state.get("batchActive") is not True:
        event = package_alert_payload(package, previous_signal, now_dt, feed)
        event["createdAt"] = now
        event["eventType"] = "BATCH_OPPORTUNITY"
        event["reason"] = "BATCH_OPPORTUNITY_CONFIRMED"
        event["batchOpportunity"] = batch
        events.append(event)

        package_state["batchActive"] = True
        package_state["lastBatchAlertedAt"] = now

    package_state["batchCandidateStreak"] = current_streak
    package_state["lastObservedSignal"] = current_signal
    package_state["lastObservedRelayVersion"] = feed.get("relay_version")
    state["packages"][name] = package_state

STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
STATE_FILE.write_text(
    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

events.sort(
    key=lambda event: (
        0 if event.get("purchasePriority") == "PRIMARY" else 1,
        -(
            event.get("edgeShadow", {}).get("score")
            if isinstance(event.get("edgeShadow", {}).get("score"), (int, float))
            else -999999
        ),
    )
)

OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
OUT_FILE.write_text(
    json.dumps(
        {
            "generatedAt": now,
            "actionableSignals": sorted(ACTIONABLE_SIGNALS),
            "eventCount": len(events),
            "events": events,
            "policy": {
                "signalSource": "CURRENT final_signal only",
                "waitAndNoBuyAlerts": False,
                "automaticPurchase": False,
                "timeSinceLastBlockCanRaiseSignal": False,
                "edgeShadowCanRaiseSignal": False,
                "publicMarketCanRaiseSignal": False,
                "purchasePriorityCanRaiseSignal": False,
                "purchasePriorityPolicy": (
                    "PRIMARY = all S packages plus USDT sizes 5 and 20; "
                    "SECONDARY = M packages and larger USDT packages such as size 50. "
                    "Priority affects alert display/order only."
                ),
                "batchOpportunity": {
                    "shadowOnly": True,
                    "automaticPurchase": False,
                    "automaticCancel": False,
                    "confirmationSnapshots": BATCH_CONFIRMATION_SNAPSHOTS,
                    "signals": sorted(BATCH_SIGNALS),
                    "minEdgeScore": BATCH_MIN_EDGE_SCORE,
                    "minExpectedReturnPercent": BATCH_MIN_EXPECTED_RETURN_PERCENT,
                    "minQ24Percent": BATCH_MIN_Q24_PERCENT,
                    "minQ7Percent": BATCH_MIN_Q7_PERCENT,
                    "marketMustBeReady": True,
                    "marketMaxAgeMinutes": MARKET_MAX_AGE_MINUTES,
                    "dataMustBeFresh": True,
                    "uniqueFeedSnapshotsRequired": True,
                    "mathConsistencyMustPassWhenApplicable": True,
                },
            },
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n",
    encoding="utf-8",
)

if events:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )

print("BUY RADAR ALERT ENGINE")
print("Actionable signals:", ", ".join(sorted(ACTIONABLE_SIGNALS)))
print("Packages checked:", len(packages))
print("Alerts generated:", len(events))
for event in events:
    print(
        f"ALERT {event['signal']} | {event['package']} | "
        f"{event.get('coin') or 'UNKNOWN'} | "
        f"previous={event.get('previousSignal')}"
    )
