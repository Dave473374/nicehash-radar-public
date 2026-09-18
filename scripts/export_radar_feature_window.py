import argparse
import json
from datetime import datetime
from pathlib import Path

RADAR_HISTORY = Path("calibration/radar-snapshots.jsonl")
OUTPUT = Path("/tmp/radar-feature-window.json")


def parse_ts(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


def is_current_scope_package(package):
    name = str(package.get("name") or "")
    currency = str(
        package.get("currency_market") or ""
    ).upper()

    if name.startswith("Team "):
        return False

    if currency == "BTC":
        return name.endswith(" S") or name.endswith(" M")

    if currency == "USDT":
        family, _, size = name.partition(" ")
        return (
            family in {"Silver", "Gold"}
            and size.replace(".", "", 1).isdigit()
        )

    return False


parser = argparse.ArgumentParser(
    description=(
        "Export public Radar package features for a UTC time window. "
        "No private/admin order data is read or written."
    )
)
parser.add_argument("--start", required=True)
parser.add_argument("--end", required=True)
args = parser.parse_args()

start = parse_ts(args.start)
end = parse_ts(args.end)

if start is None or end is None or end <= start:
    raise SystemExit("Invalid --start/--end UTC window")

if not RADAR_HISTORY.exists():
    raise SystemExit(
        f"Radar history missing: {RADAR_HISTORY}"
    )

snapshots = []

for line in RADAR_HISTORY.read_text(
    encoding="utf-8"
).splitlines():
    if not line.strip():
        continue

    try:
        snapshot = json.loads(line)
    except json.JSONDecodeError:
        continue

    ts = parse_ts(snapshot.get("collected_at"))

    if ts is None or ts < start or ts > end:
        continue

    packages_out = []

    for package in (
        snapshot.get("feed", {}).get("packages")
        or []
    ):
        if not is_current_scope_package(package):
            continue

        packages_out.append(
            {
                "name": package.get("name"),
                "currencyMarket": package.get(
                    "currency_market"
                ),
                "coin": (
                    package.get("primary_chain") or {}
                ).get("currency"),
                "mergeCoin": (
                    package.get("merge_chain") or {}
                ).get("currency"),
                "miningSignal": package.get(
                    "mining_signal"
                ),
                "economicSignal": package.get(
                    "economic_signal"
                ),
                "finalSignal": package.get(
                    "final_signal"
                ),
                "expectedReturnPercent": (
                    package.get("profitability") or {}
                ).get("expected_return_percent"),
                "profitabilityMarginPercent": (
                    package.get("profitability") or {}
                ).get("profitability_margin_percent"),
                "qualityVs24hPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_24h_percent"
                ),
                "qualityVs7dPercent": (
                    package.get("history_trend") or {}
                ).get(
                    "expected_blocks_per_btc_vs_7d_percent"
                ),
            }
        )

    snapshots.append(
        {
            "collectedAt": snapshot.get(
                "collected_at"
            ),
            "relayVersion": snapshot.get(
                "relay_version"
            ),
            "packages": packages_out,
        }
    )

OUTPUT.write_text(
    json.dumps(
        {
            "source": "PUBLIC_RADAR_HISTORY",
            "start": args.start,
            "end": args.end,
            "snapshotCount": len(snapshots),
            "snapshots": snapshots,
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n",
    encoding="utf-8",
)

print("RADAR FEATURE WINDOW")
print("Start:", args.start)
print("End:", args.end)
print("Snapshots:", len(snapshots))
print("Public feature artifact written:", OUTPUT)
