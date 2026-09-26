"""Daily Palladium M quote-side comparison from existing public research pairs.

Research-only. No network, no Admin/private data, no order outcomes and no signal changes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

PACKAGE = "Palladium M"
DEFAULT_DATES = ("2026-09-07", "2026-09-20", "2026-09-23", "2026-09-24")
TH = 1e12
DAY = 86400.0


def number(value, positive=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(x) or (positive and x <= 0):
        return None
    return x


def ts(value):
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if x.tzinfo is None or x.utcoffset() is None:
        return None
    return x.astimezone(timezone.utc)


def q(values, frac):
    vals = sorted(values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * frac
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def stats(values, digits=6):
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "count": len(vals),
        "min": round(min(vals), digits),
        "p25": round(q(vals, .25), digits),
        "median": round(median(vals), digits),
        "p75": round(q(vals, .75), digits),
        "max": round(max(vals), digits),
    }


def load(path):
    rows, malformed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            malformed += 1
    return rows, malformed


def exact_palladium_m(row):
    return (
        row.get("package") == PACKAGE
        and str(row.get("currency") or "").upper() == "BTC"
        and str(row.get("coin") or "").upper() == "LTC"
        and str(row.get("mergeCoin") or "").upper() == "DOGE"
    )


def series_key(row):
    sig = row.get("marketUnitSignature")
    sig = tuple(sig) if isinstance(sig, list) else None
    return (str(row.get("relayVersion") or ""), sig)


def derive(row):
    price = number(row.get("priceNative"), True)
    h = number(row.get("hashrateHps"), True)
    duration = number(row.get("durationSeconds"), True)
    work_per_native = number(row.get("workPerNative"), True)
    if None in (price, h, duration, work_per_native):
        return None
    actual_work_thh = h * duration / TH / 3600.0
    implied_cost = TH * DAY / work_per_native
    return {
        **row,
        "_price": price,
        "_hashrateThs": h / TH,
        "_durationHours": duration / 3600.0,
        "_actualWorkThHours": actual_work_thh,
        "_workAt001ThHours": work_per_native * 0.001 / TH / 3600.0,
        "_impliedCostBtcPerThDay": implied_cost,
        "_ev": number(row.get("feedExpectedReturnPercent")),
        "_primaryDifficulty": number(row.get("primaryDifficulty"), True),
        "_mergeDifficulty": number(row.get("mergeDifficulty"), True),
        "_marketRaw": number(row.get("marketPriceRaw"), True) if row.get("pairStatus") == "PAIRED" else None,
    }


def day_summary(day, rows, tz, version_cost_medians, version_ev_medians):
    items = []
    for row in rows:
        at = ts(row.get("quoteAt"))
        d = derive(row)
        if at is None or d is None or at.astimezone(tz).date().isoformat() != day:
            continue
        d["_localAt"] = at.astimezone(tz)
        key = series_key(d)
        base_cost = version_cost_medians.get(key)
        base_ev = version_ev_medians.get(key)
        d["_costVsSeriesMedianPct"] = (
            (d["_impliedCostBtcPerThDay"] / base_cost - 1) * 100
            if base_cost else None
        )
        d["_evVsSeriesMedianPp"] = d["_ev"] - base_ev if d["_ev"] is not None and base_ev is not None else None
        items.append(d)

    if not items:
        return {"date": day, "status": "NO_DATA", "quoteCount": 0}

    paired = [r for r in items if r.get("pairStatus") == "PAIRED"]
    first = min(r["_localAt"] for r in items)
    last = max(r["_localAt"] for r in items)
    signals = Counter(str(r.get("currentSignal") or "UNKNOWN") for r in items)
    math_status = Counter(str(r.get("mathStatus") or "UNKNOWN") for r in items)
    versions = sorted({str(r.get("relayVersion") or "UNKNOWN") for r in items})
    signatures = sorted({json.dumps(r.get("marketUnitSignature"), sort_keys=True) for r in paired})
    return {
        "date": day,
        "status": "OK",
        "quoteCount": len(items),
        "pairedQuoteCount": len(paired),
        "firstLocalQuote": first.isoformat(),
        "lastLocalQuote": last.isoformat(),
        "observedSpanHours": round((last - first).total_seconds() / 3600.0, 3),
        "relayVersions": versions,
        "pairedMarketUnitSignatureCount": len(signatures),
        "priceNativeBtc": stats([r["_price"] for r in items], 8),
        "hashrateThs": stats([r["_hashrateThs"] for r in items], 6),
        "durationHours": stats([r["_durationHours"] for r in items], 6),
        "actualQuotedWorkThHours": stats([r["_actualWorkThHours"] for r in items], 6),
        "workFor001BtcThHours": stats([r["_workAt001ThHours"] for r in items], 6),
        "impliedCostBtcPerThDay": stats([r["_impliedCostBtcPerThDay"] for r in items], 8),
        "feedExpectedReturnPercent": stats([r["_ev"] for r in items], 4),
        "primaryLtcDifficulty": stats([r["_primaryDifficulty"] for r in items], 3),
        "mergeDogeDifficulty": stats([r["_mergeDifficulty"] for r in items], 3),
        "pairedPublicMarketPriceRaw": stats([r["_marketRaw"] for r in paired], 12),
        "costVsSameSeriesMedianPercent": stats([r["_costVsSeriesMedianPct"] for r in items], 4),
        "expectedReturnVsSameSeriesMedianPercentagePoints": stats([r["_evVsSeriesMedianPp"] for r in items], 4),
        "signals": dict(signals),
        "mathStatus": dict(math_status),
        "interpretation": "QUOTE_SIDE_DESCRIPTIVE_ONLY_NO_ORDER_OUTCOMES",
    }


def build(rows, focus_dates, tz_name):
    tz = ZoneInfo(tz_name)
    exact = [d for r in rows if exact_palladium_m(r) and (d := derive(r)) is not None]
    by_series_cost = defaultdict(list)
    by_series_ev = defaultdict(list)
    for row in exact:
        key = series_key(row)
        by_series_cost[key].append(row["_impliedCostBtcPerThDay"])
        if row["_ev"] is not None:
            by_series_ev[key].append(row["_ev"])
    cost_medians = {k: median(v) for k, v in by_series_cost.items() if v}
    ev_medians = {k: median(v) for k, v in by_series_ev.items() if v}
    days = [day_summary(day, exact, tz, cost_medians, ev_medians) for day in focus_dates]
    present = [d for d in days if d["status"] == "OK"]
    comparable = len(present) >= 2 and len({tuple(d["relayVersions"]) for d in present}) == 1
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "role": "PALLADIUM_M_QUOTE_SIDE_DAY_COMPARISON_RESEARCH_ONLY",
        "package": PACKAGE,
        "timezone": tz_name,
        "focusDates": list(focus_dates),
        "sourcePairRowsForPackage": len(exact),
        "days": days,
        "crossDayRelayVersionComparable": comparable,
        "guardrails": {
            "usesAdminData": False,
            "usesPrivateApi": False,
            "usesOrderOutcomes": False,
            "canEstimateHitRate": False,
            "canRaiseSignal": False,
            "currentProductionModelChanged": False,
            "rawMarketPriceIsExecutableQuote": False,
            "sameSeriesNormalizationUsesRelayVersionAndMarketUnitSignature": True,
        },
        "notes": [
            "workFor001BtcThHours normalizes quoted work to 0.001 BTC; it is not measured delivered work.",
            "impliedCostBtcPerThDay is derived from package quote work, not Admin PRICE.",
            "feedExpectedReturnPercent is the historical feed model output, not verified realized ROI.",
            "Raw public market price is summarized only when a causal PAIR existed and is not normalized to an executable purchase price.",
            "Cross-day interpretation must respect relay-version/unit-signature changes and sparse daily coverage.",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", type=Path, default=Path("research/market-edge-pairs.jsonl"))
    ap.add_argument("--output", type=Path, default=Path("research/palladium-m-day-comparison.json"))
    ap.add_argument("--timezone", default="Europe/Ljubljana")
    ap.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    args = ap.parse_args()
    if args.pairs.resolve() == args.output.resolve():
        ap.error("input and output must differ")
    rows, malformed = load(args.pairs)
    report = build(rows, tuple(args.dates), args.timezone)
    report["malformedInputRowsSkipped"] = malformed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("PALLADIUM M DAY COMPARISON")
    for d in report["days"]:
        med_work = (d.get("workFor001BtcThHours") or {}).get("median")
        med_cost = (d.get("impliedCostBtcPerThDay") or {}).get("median")
        med_ev = (d.get("feedExpectedReturnPercent") or {}).get("median")
        print(d["date"], d["status"], "quotes", d.get("quoteCount"), "work", med_work, "cost", med_cost, "EV%", med_ev)
    print("CURRENT unchanged; no Admin/private/order outcome access")


if __name__ == "__main__":
    main()
