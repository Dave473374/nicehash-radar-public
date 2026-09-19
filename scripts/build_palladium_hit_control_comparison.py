"""Compare verified Palladium M HIT pre-context with matched public market controls.

Public/offline descriptive research only. Controls are non-HIT-time market states,
not completed EasyMining MISS tickets. This script never changes BUY logic.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
import json
import math
from pathlib import Path
from statistics import median
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_verified_hit_market_context import (  # noqa: E402
    build_pre_hit_context,
    finite_number,
    parse_time,
    same_pair_series,
)

DEFAULT_HITS = Path("research/verified-hit-market-context.jsonl")
DEFAULT_PAIRS = Path("research/market-edge-pairs.jsonl")
DEFAULT_OUTPUT = Path("research/palladium-hit-control-report.json")

TARGET_PACKAGE = "Palladium M"
TARGET_EVENT_COIN = "DOGE"
CONTROL_RADIUS_HOURS = 24
CONTROL_SLOT_MINUTES = 60
MAX_CONTROLS_PER_HIT = 24
EXCLUSION_AFTER_HIT_MINUTES = 15

FEATURES = (
    "workPerNativeChangePercent",
    "ticketCostPerWorkChangePercent",
    "marketPriceRawChangePercent",
    "primaryDifficultyChangePercent",
    "mergeDifficultyChangePercent",
    "feedExpectedReturnChangePercentagePoints",
)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def iso(value):
    return value.isoformat() if value is not None else None


def hit_series_stub(hit: dict) -> dict:
    market = hit.get("marketContext") if isinstance(hit.get("marketContext"), dict) else {}
    return {
        "package": hit.get("packageName"),
        "size": market.get("size"),
        "currency": market.get("currency"),
        "coin": market.get("primaryCoin"),
        "mergeCoin": market.get("mergeCoin"),
        "marketAlgorithm": market.get("marketAlgorithm"),
        "relayVersion": market.get("relayVersion"),
        "marketUnitSignature": market.get("marketUnitSignature"),
    }


def hit_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if row.get("packageName") != TARGET_PACKAGE:
            continue
        if str(row.get("coin") or "").upper() != TARGET_EVENT_COIN:
            continue
        hit_at = parse_time(row.get("hitAt"))[0]
        pre = row.get("preHitContext") if isinstance(row.get("preHitContext"), dict) else {}
        endpoint_age = finite_number(pre.get("endpointAgeSeconds"))
        market = row.get("marketContext") if isinstance(row.get("marketContext"), dict) else {}
        if (
            hit_at is None
            or pre.get("status") != "AVAILABLE"
            or endpoint_age is None
            or endpoint_age > 15 * 60
            or market.get("status") != "MATCHED_FRESH_EXACT_PACKAGE"
        ):
            continue
        out.append({**row, "_hit": hit_at, "_endpointAge": endpoint_age})
    return sorted(out, key=lambda row: row["_hit"])


def pair_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if row.get("package") != TARGET_PACKAGE or row.get("pairStatus") != "PAIRED":
            continue
        quote = parse_time(row.get("quoteAt"))[0]
        observed = parse_time(row.get("observedAt"))[0]
        if quote is None or observed is None or observed < quote:
            continue
        out.append({**row, "_quote": quote, "_observed": observed})
    return sorted(out, key=lambda row: row["_quote"])


def event_in_exclusion(event_at, hits: list[dict], duration_seconds: float) -> bool:
    for hit in hits:
        start = hit["_hit"] - timedelta(seconds=duration_seconds)
        end = hit["_hit"] + timedelta(minutes=EXCLUSION_AFTER_HIT_MINUTES)
        if start <= event_at <= end:
            return True
    return False


def matched_controls_for_hit(hit: dict, pairs: list[dict], all_hits: list[dict]) -> list[dict]:
    stub = hit_series_stub(hit)
    same = [row for row in pairs if same_pair_series(row, stub)]
    if not same:
        return []

    low = hit["_hit"] - timedelta(hours=CONTROL_RADIUS_HOURS)
    high = hit["_hit"] + timedelta(hours=CONTROL_RADIUS_HOURS)
    age = float(hit["_endpointAge"])
    candidates = []
    for idx, endpoint in enumerate(same):
        synthetic_event = endpoint["_quote"] + timedelta(seconds=age)
        if not low <= synthetic_event <= high:
            continue
        duration = finite_number(endpoint.get("durationSeconds"), positive=True)
        if duration is None:
            continue
        if event_in_exclusion(synthetic_event, all_hits, duration):
            continue
        history = same[: idx + 1]
        context = build_pre_hit_context(history, synthetic_event)
        if context.get("status") != "AVAILABLE":
            continue
        candidates.append({
            "syntheticEventAt": iso(synthetic_event),
            "endpointQuoteAt": endpoint.get("quoteAt"),
            "matchedToHitEventId": hit.get("eventId"),
            "endpointAgeSeconds": round(age, 3),
            "context": context,
            "_event": synthetic_event,
        })

    # Reduce serial correlation: at most one control per clock-hour slot for
    # each HIT, choosing the closest slot candidate to that HIT.
    by_slot = {}
    for row in sorted(candidates, key=lambda r: (abs((r["_event"] - hit["_hit"]).total_seconds()), r["_event"])):
        slot = int(row["_event"].timestamp()) // (CONTROL_SLOT_MINUTES * 60)
        by_slot.setdefault(slot, row)
    selected = sorted(
        by_slot.values(),
        key=lambda r: (abs((r["_event"] - hit["_hit"]).total_seconds()), r["_event"]),
    )[:MAX_CONTROLS_PER_HIT]
    return sorted(selected, key=lambda r: r["_event"])


def matched_windows(context: dict, horizon: int) -> list[dict]:
    return [
        row for row in (context.get("windows") or [])
        if isinstance(row, dict)
        and row.get("status") == "MATCHED"
        and row.get("horizonMinutes") == horizon
    ]


def empirical_percentile(value: float, controls: list[float]) -> float | None:
    if not controls:
        return None
    below = sum(v < value for v in controls)
    equal = sum(v == value for v in controls)
    return round(100 * (below + 0.5 * equal) / len(controls), 3)


def summarize(hits: list[dict], controls: list[dict]) -> dict:
    horizons = (15, 30, 60)
    result = {}
    for horizon in horizons:
        hit_windows = []
        for hit in hits:
            pre = hit.get("preHitContext") if isinstance(hit.get("preHitContext"), dict) else {}
            hit_windows.extend(matched_windows(pre, horizon))

        control_windows = []
        for control in controls:
            control_windows.extend(matched_windows(control["context"], horizon))

        summary = {
            "matchedHits": len(hit_windows),
            "matchedControls": len(control_windows),
        }
        for field in FEATURES:
            hit_values = [
                float(row[field]) for row in hit_windows
                if finite_number(row.get(field)) is not None
            ]
            control_values = [
                float(row[field]) for row in control_windows
                if finite_number(row.get(field)) is not None
            ]
            hit_median = median(hit_values) if hit_values else None
            control_median = median(control_values) if control_values else None
            prefix = field[0].upper() + field[1:]
            summary[f"hitMedian{prefix}"] = round(hit_median, 6) if hit_median is not None else None
            summary[f"controlMedian{prefix}"] = round(control_median, 6) if control_median is not None else None
            summary[f"hitMinusControlMedian{prefix}"] = (
                round(hit_median - control_median, 6)
                if hit_median is not None and control_median is not None
                else None
            )
            summary[f"hitMedianPercentileAmongControls{prefix}"] = (
                empirical_percentile(hit_median, control_values)
                if hit_median is not None
                else None
            )
        result[f"{horizon}m"] = summary
    return result


def build(hit_input: list[dict], pair_input: list[dict]) -> dict:
    hits = hit_rows(hit_input)
    pairs = pair_rows(pair_input)
    controls = []
    controls_per_hit = {}
    for hit in hits:
        matched = matched_controls_for_hit(hit, pairs, hits)
        controls_per_hit[str(hit.get("eventId"))] = len(matched)
        controls.extend(matched)

    # A control may be re-used for different HIT endpoint-age matches. Keep the
    # matched-to-HIT identity because those are distinct age-aligned comparisons.
    comparison = summarize(hits, controls)
    return {
        "schemaVersion": 1,
        "role": "PALLADIUM_M_VERIFIED_HIT_VS_MATCHED_MARKET_CONTROLS",
        "targetPackage": TARGET_PACKAGE,
        "targetEventCoin": TARGET_EVENT_COIN,
        "verifiedHitCount": len(hits),
        "controlComparisonCount": len(controls),
        "controlsPerHit": controls_per_hit,
        "settings": {
            "controlRadiusHours": CONTROL_RADIUS_HOURS,
            "controlSlotMinutes": CONTROL_SLOT_MINUTES,
            "maxControlsPerHit": MAX_CONTROLS_PER_HIT,
            "hitExclusionUsesPackageDurationSeconds": True,
            "exclusionAfterHitMinutes": EXCLUSION_AFTER_HIT_MINUTES,
            "controlEndpointAgeMatchedToHit": True,
            "exactPackageAndSeriesOnly": True,
        },
        "comparisonByHorizon": comparison,
        "canRaiseSignal": False,
        "automaticPurchase": False,
        "automaticCancel": False,
        "verdict": "DESCRIPTIVE_CONTROL_COMPARISON_ONLY_NO_VERIFIED_EDGE",
        "limitations": [
            "Controls are public market states, not completed EasyMining MISS tickets.",
            "Verified HIT count is small and controls are not independent randomized trials.",
            "The same market control may appear in more than one HIT-specific age-matched comparison.",
            "Excluding the preceding package-duration window reduces winner contamination but cannot prove a control was a true MISS.",
            "Observed differences are candidate patterns only and cannot change CURRENT/final_signal.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.resolve() in {args.hits.resolve(), args.pairs.resolve()}:
        parser.error("Output must be distinct from inputs")
    result = build(load_jsonl(args.hits), load_jsonl(args.pairs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("PALLADIUM HIT/CONTROL COMPARISON COMPLETE; research-only")
    print(json.dumps({
        "verifiedHitCount": result["verifiedHitCount"],
        "controlComparisonCount": result["controlComparisonCount"],
        "verdict": result["verdict"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
