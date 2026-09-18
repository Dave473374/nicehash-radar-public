import json
import os
from datetime import datetime, timezone
from pathlib import Path

BUY_FEED = Path(os.getenv("BUY_RADAR_FEED", "buy-feed.json"))
STATE_FILE = Path(os.getenv("BUY_RADAR_ALERT_STATE", "alerts/alert-state.json"))
HISTORY_FILE = Path(os.getenv("BUY_RADAR_ALERT_HISTORY", "alerts/alert-history.jsonl"))
OUT_FILE = Path(os.getenv("BUY_RADAR_ALERT_OUTPUT", "/tmp/buy-radar-alerts.json"))

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


def package_alert_payload(package, previous_signal, now, feed):
    profitability = package.get("profitability") or {}
    history = package.get("history_trend") or {}
    primary = package.get("primary_chain") or {}
    nicehash_odds = package.get("nicehash_odds") or {}

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

now = utc_now()
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
        event = package_alert_payload(package, previous_signal, now, feed)
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
