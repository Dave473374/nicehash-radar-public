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
    cutoff = now - timedelta(hours=24)
    recent = [(ts, data) for ts, data in samples if ts >= cutoff]

    price_values = [data.get("priceRaw") for _, data in recent]
    order_values = [data.get("orders") for _, data in recent]
    speed_values = [data.get("speedRaw") for _, data in recent]

    coverage_hours = 0.0
    if len(recent) >= 2:
        coverage_hours = (
            recent[-1][0] - recent[0][0]
        ).total_seconds() / 3600

    ready = len(recent) >= 48 and coverage_hours >= 10

    return {
        "status": "READY" if ready else "WARMING_UP",
        "role": "INFORMATIONAL_ONLY",
        "algorithm": algorithm,
        "collectedAt": latest_ts.isoformat(),
        "ageMinutes": round((now - latest_ts).total_seconds() / 60, 1),
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
    cost = (package.get("economics") or {}).get("package_cost_eur")

    if isinstance(cost, (int, float)) and float(cost) <= 100:
        return "PRIMARY"

    return "SECONDARY"


def package_alert_payload(package, previous_signal, now, feed):
    profitability = package.get("profitability") or {}
    history = package.get("history_trend") or {}
    primary = package.get("primary_chain") or {}
    nicehash_odds = package.get("nicehash_odds") or {}
    edge = package.get("edge_shadow") or {}
    market = public_market_context(package, now)

    feed_checked_at = parse_time(feed.get("checked_at"))
    feed_age_minutes = None
    if feed_checked_at is not None:
        feed_age_minutes = round(
            max(0.0, (now - feed_checked_at).total_seconds() / 60),
            1,
        )

    if feed_age_minutes is None:
        freshness_status = "UNKNOWN"
    elif feed_age_minutes <= 7:
        freshness_status = "FRESH"
    elif feed_age_minutes <= 15:
        freshness_status = "DELAYED"
    else:
        freshness_status = "STALE"

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
        "freshness": {
            "status": freshness_status,
            "feedCheckedAt": feed.get("checked_at"),
            "ageMinutes": feed_age_minutes,
        },
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

    changed = previous_signal != current_signal
    actionable = current_signal in ACTIONABLE_SIGNALS

    if changed and actionable:
        event = package_alert_payload(package, previous_signal, now_dt, feed)
        event["createdAt"] = now
        events.append(event)
        package_state["lastAlertedSignal"] = current_signal
        package_state["lastAlertedAt"] = now

    package_state["lastObservedSignal"] = current_signal
    package_state["lastObservedRelayVersion"] = feed.get("relay_version")
    state["packages"][name] = package_state

STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
STATE_FILE.write_text(
    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
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
                    "PRIMARY = any package costing <= EUR 100; "
                    "SECONDARY = packages above EUR 100. Priority affects alert display/order only."
                ),
            },
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n",
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
