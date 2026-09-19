"""Attach public market-edge context to private EasyMining entry-time matches.

Private analysis only. Inputs derived from private completed orders stay in /tmp.
The script performs no network requests, does not change CURRENT/final_signal,
and never uploads or commits order-level results.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from statistics import median
from pathlib import Path
from typing import Any

DEFAULT_MATCHES = Path("/tmp/private-order-matches.json")
DEFAULT_PAIRS = Path("research/market-edge-pairs.jsonl")
DEFAULT_EPISODES = Path("research/lag-episodes.jsonl")
DEFAULT_PROTOCOL = Path("research/lag-protocol-v1.json")
DEFAULT_OUTPUT = Path("/tmp/private-order-market-context.json")

MAX_MARKET_CONTEXT_AGE_SECONDS = 15 * 60
MAX_SAME_SNAPSHOT_DELTA_SECONDS = 2
MAX_RECENT_LAG_MINUTES = 60
PRE_ENTRY_WINDOWS_MINUTES = (15, 30, 60)
PRE_ENTRY_BASELINE_TOLERANCE_SECONDS = 10 * 60


def parse_time(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        if abs(number) > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def coin_signature_match(match: dict, pair: dict) -> bool:
    if str(match.get("coin") or "").upper() != str(pair.get("coin") or "").upper():
        return False
    if str(match.get("currencyMarket") or "").upper() != str(pair.get("currency") or "").upper():
        return False

    private_merge = str(match.get("mergeCoin") or "").upper()
    pair_merge = str(pair.get("mergeCoin") or "").upper()
    # If the private API omitted mergeCoin, exact package + primary coin +
    # currency is still sufficient. A present value must agree.
    if private_merge and private_merge != pair_merge:
        return False
    return True


def episode_coin_match(match: dict, episode: dict) -> bool:
    entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
    primary = str(entry.get("coin") or "").upper()
    merge = ""
    signature = entry.get("signature")
    if isinstance(signature, list) and len(signature) > 4:
        merge = str(signature[4] or "").upper()

    private_coin = str(match.get("coin") or "").upper()
    private_merge = str(match.get("mergeCoin") or "").upper()
    if private_coin and private_coin not in {primary, merge}:
        return False
    if private_merge and private_merge not in {primary, merge}:
        return False
    return True


def stats(rows: list[dict]) -> dict:
    known = [row for row in rows if row.get("outcome") in {"HIT", "MISS"}]
    hits = [row for row in known if row.get("outcome") == "HIT"]
    misses = [row for row in known if row.get("outcome") == "MISS"]
    roi_rows = [
        row for row in rows
        if row.get("roiAvailable") is True
        and isinstance(row.get("actualCostBtcEquivalent"), (int, float))
        and isinstance(row.get("realizedReturnBtc"), (int, float))
        and row.get("actualCostBtcEquivalent") > 0
    ]
    total_cost = sum(float(row["actualCostBtcEquivalent"]) for row in roi_rows)
    total_return = sum(float(row["realizedReturnBtc"]) for row in roi_rows)
    expected = [
        float(row["expectedReturnPercent"])
        for row in rows
        if isinstance(row.get("expectedReturnPercent"), (int, float))
        and math.isfinite(float(row["expectedReturnPercent"]))
    ]
    return {
        "orders": len(rows),
        "knownOutcomes": len(known),
        "hits": len(hits),
        "misses": len(misses),
        "unknownOutcomes": len(rows) - len(known),
        "hitRatePercent": round(100 * len(hits) / len(known), 4) if known else None,
        "roiOrders": len(roi_rows),
        "totalCostBtcEquivalent": round(total_cost, 12),
        "totalReturnBtc": round(total_return, 12),
        "realizedReturnMultiple": round(total_return / total_cost, 6) if total_cost > 0 else None,
        "realizedRoiPercent": round((total_return / total_cost - 1) * 100, 4) if total_cost > 0 else None,
        "averageEntryExpectedReturnPercent": round(sum(expected) / len(expected), 4) if expected else None,
    }


def finite_number(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number


def percent_change(end: Any, start: Any, *, positive: bool = True) -> float | None:
    a = finite_number(start, positive=positive)
    b = finite_number(end, positive=positive)
    if a is None or b is None or a == 0:
        return None
    return round((b / a - 1) * 100, 6)


def same_pair_series(a: dict, b: dict) -> bool:
    keys = (
        "package",
        "currency",
        "coin",
        "mergeCoin",
        "marketAlgorithm",
        "relayVersion",
        "marketUnitSignature",
    )
    return all(a.get(key) == b.get(key) for key in keys)


def build_pre_entry_context(order_start: datetime | None, eligible_pairs: list[dict]) -> dict:
    """Describe exact-package public market movement before a real private order.

    This uses only quotes/observations already known before order entry. It is
    descriptive HIT/MISS calibration evidence and never changes final_signal.
    """
    if order_start is None:
        return {"status": "NO_USABLE_ORDER_TIME", "windows": []}

    paired = [
        row for row in eligible_pairs
        if row.get("pairStatus") == "PAIRED"
        and row["_quote"] <= order_start
        and row["_observed"] <= order_start
    ]
    if not paired:
        return {"status": "NO_CAUSAL_PAIRED_EXACT_PACKAGE_QUOTES", "windows": []}

    endpoint = paired[-1]
    endpoint_age = (order_start - endpoint["_quote"]).total_seconds()
    if endpoint_age > MAX_MARKET_CONTEXT_AGE_SECONDS:
        return {
            "status": "NO_FRESH_PAIRED_ENDPOINT",
            "endpointQuoteAt": endpoint.get("quoteAt"),
            "endpointAgeSeconds": round(endpoint_age, 3),
            "windows": [],
        }

    series = [row for row in paired if same_pair_series(row, endpoint)]
    windows = []
    for horizon in PRE_ENTRY_WINDOWS_MINUTES:
        target = order_start - timedelta(minutes=horizon)
        candidates = [
            row for row in series
            if row["_quote"] <= target
            and row["_observed"] <= order_start
        ]
        if not candidates:
            windows.append({
                "horizonMinutes": horizon,
                "status": "NO_BASELINE_AT_OR_BEFORE_TARGET",
                "targetAt": iso(target),
            })
            continue

        baseline = candidates[-1]
        distance = (target - baseline["_quote"]).total_seconds()
        if distance > PRE_ENTRY_BASELINE_TOLERANCE_SECONDS:
            windows.append({
                "horizonMinutes": horizon,
                "status": "BASELINE_TOO_FAR_FROM_TARGET",
                "targetAt": iso(target),
                "baselineQuoteAt": baseline.get("quoteAt"),
                "baselineDistanceSeconds": round(distance, 3),
            })
            continue

        work_change = percent_change(endpoint.get("workPerNative"), baseline.get("workPerNative"))
        ticket_cost_change = None
        if work_change is not None and work_change > -100:
            ticket_cost_change = round((1 / (1 + work_change / 100) - 1) * 100, 6)

        start_expected = finite_number(baseline.get("feedExpectedReturnPercent"))
        end_expected = finite_number(endpoint.get("feedExpectedReturnPercent"))
        expected_delta = None
        if start_expected is not None and end_expected is not None:
            expected_delta = round(end_expected - start_expected, 6)

        windows.append({
            "horizonMinutes": horizon,
            "status": "MATCHED",
            "targetAt": iso(target),
            "baselineQuoteAt": baseline.get("quoteAt"),
            "baselineObservedAt": baseline.get("observedAt"),
            "baselineDistanceSeconds": round(distance, 3),
            "endpointQuoteAt": endpoint.get("quoteAt"),
            "endpointObservedAt": endpoint.get("observedAt"),
            "endpointAgeSeconds": round(endpoint_age, 3),
            "workPerNativeChangePercent": work_change,
            "ticketCostPerWorkChangePercent": ticket_cost_change,
            "marketPriceRawChangePercent": percent_change(endpoint.get("marketPriceRaw"), baseline.get("marketPriceRaw")),
            "primaryDifficultyChangePercent": percent_change(endpoint.get("primaryDifficulty"), baseline.get("primaryDifficulty")),
            "mergeDifficultyChangePercent": percent_change(endpoint.get("mergeDifficulty"), baseline.get("mergeDifficulty")),
            "feedExpectedReturnChangePercentagePoints": expected_delta,
            "baselineSignal": baseline.get("currentSignal"),
            "endpointSignal": endpoint.get("currentSignal"),
            "interpretation": "PRIVATE_REAL_ORDER_PRE_ENTRY_CONTEXT_NOT_CAUSAL_PROOF",
        })

    return {
        "status": "AVAILABLE" if any(row.get("status") == "MATCHED" for row in windows) else "NO_MATCHED_WINDOWS",
        "endpointQuoteAt": endpoint.get("quoteAt"),
        "endpointObservedAt": endpoint.get("observedAt"),
        "endpointAgeSeconds": round(endpoint_age, 3),
        "windows": windows,
    }


def summarize_pre_entry_by_outcome(rows: list[dict]) -> dict:
    feature_fields = (
        "workPerNativeChangePercent",
        "ticketCostPerWorkChangePercent",
        "marketPriceRawChangePercent",
        "primaryDifficultyChangePercent",
        "mergeDifficultyChangePercent",
        "feedExpectedReturnChangePercentagePoints",
    )
    grouped: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for row in rows:
        outcome = str(row.get("outcome") or "UNKNOWN")
        package = str(row.get("packageName") or "")
        context = row.get("entryPreContext") if isinstance(row.get("entryPreContext"), dict) else {}
        for window in context.get("windows") or []:
            if not isinstance(window, dict) or window.get("status") != "MATCHED":
                continue
            horizon = window.get("horizonMinutes")
            if not isinstance(horizon, int):
                continue
            grouped[(package, horizon, outcome)].append(window)

    by_group = {}
    package_horizon = defaultdict(dict)
    for (package, horizon, outcome), items in sorted(grouped.items()):
        key = f"{package}|{horizon}m|{outcome}"
        summary = {"matchedOrders": len(items)}
        for field in feature_fields:
            values = [
                float(item[field])
                for item in items
                if finite_number(item.get(field)) is not None
            ]
            summary[f"median{field[0].upper()}{field[1:]}"] = (
                round(median(values), 6) if values else None
            )
        by_group[key] = summary
        package_horizon[(package, horizon)][outcome] = summary

    comparisons = {}
    for (package, horizon), outcomes in sorted(package_horizon.items()):
        hit_summary = outcomes.get("HIT")
        miss_summary = outcomes.get("MISS")
        if not hit_summary or not miss_summary:
            continue
        comparison = {
            "hitOrders": hit_summary["matchedOrders"],
            "missOrders": miss_summary["matchedOrders"],
        }
        for field in feature_fields:
            metric = f"median{field[0].upper()}{field[1:]}"
            hit_value = hit_summary.get(metric)
            miss_value = miss_summary.get(metric)
            comparison[f"hitMinusMiss{field[0].upper()}{field[1:]}"] = (
                round(hit_value - miss_value, 6)
                if isinstance(hit_value, (int, float)) and isinstance(miss_value, (int, float))
                else None
            )
        comparisons[f"{package}|{horizon}m"] = comparison

    return {
        "role": "PRIVATE_REAL_ORDER_HIT_MISS_DESCRIPTIVE_COMPARISON",
        "byPackageHorizonOutcome": by_group,
        "hitMinusMiss": comparisons,
        "canRaiseSignal": False,
    }


def build(private_report: dict, pair_rows: list[dict], episode_rows: list[dict], protocol: dict) -> dict:
    matches = private_report.get("matches")
    if not isinstance(matches, list):
        raise ValueError("Private entry-match report missing matches list")

    pairs_by_package: dict[str, list[dict]] = defaultdict(list)
    for pair in pair_rows:
        package = pair.get("package")
        quote_at = parse_time(pair.get("quoteAt"))
        observed_at = parse_time(pair.get("observedAt"))
        if not isinstance(package, str) or not package or quote_at is None or observed_at is None:
            continue
        pairs_by_package[package].append({**pair, "_quote": quote_at, "_observed": observed_at})
    for rows in pairs_by_package.values():
        rows.sort(key=lambda row: row["_quote"])

    episodes_by_package: dict[str, list[dict]] = defaultdict(list)
    for episode in episode_rows:
        entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
        package = entry.get("package")
        quote_at = parse_time(entry.get("quoteAt"))
        if not isinstance(package, str) or not package or quote_at is None:
            continue
        episodes_by_package[package].append({**episode, "_entryQuote": quote_at})
    for rows in episodes_by_package.values():
        rows.sort(key=lambda row: row["_entryQuote"])

    validation_start = parse_time(protocol.get("validationStart"))
    protocol_packages = {
        str(value)
        for value in (protocol.get("packages") or [])
        if isinstance(value, str) and value
    }

    out = []
    context_counts = Counter()
    lag_counts = Counter()
    protocol_counts = Counter()

    for match in matches:
        if not isinstance(match, dict):
            continue
        order_start = parse_time(match.get("orderStartTs"))
        snapshot_time = parse_time(match.get("snapshotFeedTime"))
        package = match.get("packageName")
        if order_start is None or snapshot_time is None or not isinstance(package, str) or not package:
            context_status = "INVALID_ENTRY_MATCH_TIME_OR_PACKAGE"
            context_counts[context_status] += 1
            out.append({
                **match,
                "entryMarketContextStatus": context_status,
                "entryMarketContext": None,
                "entryLagContext": None,
                "entryPreContext": {"status": "NO_USABLE_ORDER_TIME", "windows": []},
                "privateEdgeResearchRole": "ENTRY_TIME_DESCRIPTIVE_ONLY",
            })
            continue

        eligible_pairs = [
            pair for pair in pairs_by_package.get(package, [])
            if coin_signature_match(match, pair)
            and pair["_quote"] <= order_start
            and pair["_observed"] <= order_start
        ]

        pre_entry_context = build_pre_entry_context(order_start, eligible_pairs)
        context_counts[f"PRE_ENTRY_{pre_entry_context['status']}"] += 1

        same_snapshot = [
            pair for pair in eligible_pairs
            if abs((pair["_quote"] - snapshot_time).total_seconds())
            <= MAX_SAME_SNAPSHOT_DELTA_SECONDS
        ]

        chosen = None
        alignment = None
        if same_snapshot:
            chosen = min(
                same_snapshot,
                key=lambda pair: abs((pair["_quote"] - snapshot_time).total_seconds()),
            )
            alignment = "SAME_ENTRY_RADAR_QUOTE"
        elif eligible_pairs:
            chosen = eligible_pairs[-1]
            alignment = "LATEST_CAUSAL_FALLBACK"

        market_context = None
        if chosen is None:
            if not pairs_by_package.get(package):
                context_status = "NO_EXACT_PACKAGE_MARKET_SERIES"
            else:
                context_status = "NO_CAUSAL_MARKET_QUOTE_FOR_ENTRY"
        else:
            quote_age = (order_start - chosen["_quote"]).total_seconds()
            snapshot_delta = abs((chosen["_quote"] - snapshot_time).total_seconds())
            if quote_age > MAX_MARKET_CONTEXT_AGE_SECONDS:
                context_status = "STALE_MARKET_QUOTE_AT_ENTRY"
            elif chosen.get("pairStatus") != "PAIRED":
                context_status = "ENTRY_QUOTE_NO_VALID_MARKET_PAIR"
            elif alignment == "SAME_ENTRY_RADAR_QUOTE":
                context_status = "MATCHED_SAME_ENTRY_QUOTE"
            else:
                context_status = "MATCHED_CAUSAL_FALLBACK_QUOTE"

            market_context = {
                "status": context_status,
                "alignment": alignment,
                "quoteAt": chosen.get("quoteAt"),
                "observedAt": chosen.get("observedAt"),
                "quoteAgeSeconds": round(quote_age, 3),
                "snapshotFeedTime": match.get("snapshotFeedTime"),
                "snapshotQuoteDeltaSeconds": round(snapshot_delta, 3),
                "pairStatus": chosen.get("pairStatus"),
                "currency": chosen.get("currency"),
                "primaryCoin": chosen.get("coin"),
                "mergeCoin": chosen.get("mergeCoin"),
                "marketAlgorithm": chosen.get("marketAlgorithm"),
                "relayVersion": chosen.get("relayVersion"),
                "priceNative": chosen.get("priceNative"),
                "costEur": chosen.get("costEur"),
                "durationSeconds": chosen.get("durationSeconds"),
                "hashrateHps": chosen.get("hashrateHps"),
                "workPerNative": chosen.get("workPerNative"),
                "marketAt": chosen.get("marketAt"),
                "marketAgeSeconds": chosen.get("marketAgeSeconds"),
                "marketPriceRaw": chosen.get("marketPriceRaw"),
                "marketOrders": chosen.get("marketOrders"),
                "marketSpeedRaw": chosen.get("marketSpeedRaw"),
                "relativeValueLogIndex": chosen.get("relativeValueLogIndex"),
                "primaryDifficulty": chosen.get("primaryDifficulty"),
                "mergeDifficulty": chosen.get("mergeDifficulty"),
                "marketFeedExpectedReturnPercent": chosen.get("feedExpectedReturnPercent"),
                "marketMathStatus": chosen.get("mathStatus"),
                "marketCurrentSignal": chosen.get("currentSignal"),
                "entrySignalConsistent": (
                    chosen.get("currentSignal") == match.get("finalSignal")
                    if chosen.get("currentSignal") is not None and match.get("finalSignal") is not None
                    else None
                ),
            }
        context_counts[context_status] += 1

        package_protocol_eligible = package in protocol_packages
        after_validation_start = bool(
            validation_start is not None and order_start >= validation_start
        )
        protocol_eligible = package_protocol_eligible and after_validation_start
        if not package_protocol_eligible:
            protocol_status = "PACKAGE_NOT_IN_LOCKED_LAG_PROTOCOL"
        elif not after_validation_start:
            protocol_status = "PRE_VALIDATION_START_EXPLORATORY_ONLY"
        else:
            protocol_status = "POST_REGISTRATION_ELIGIBLE"
        protocol_counts[protocol_status] += 1

        recent = []
        for episode in episodes_by_package.get(package, []):
            if episode.get("sourceRevision") is True or not episode_coin_match(match, episode):
                continue
            entry_at = episode["_entryQuote"]
            if entry_at > order_start:
                continue
            lead_minutes = (order_start - entry_at).total_seconds() / 60
            if lead_minutes <= MAX_RECENT_LAG_MINUTES:
                recent.append((entry_at, lead_minutes, episode))

        lag_context = {
            "status": "NO_RECENT_EXACT_PACKAGE_LAG_EPISODE",
            "lookbackMinutes": MAX_RECENT_LAG_MINUTES,
            "protocolStatus": protocol_status,
            "registeredLiveExposure": False,
        }
        if recent:
            _, lead_minutes, episode = recent[-1]
            entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
            episode_state = episode.get("episode") if isinstance(episode.get("episode"), dict) else {}
            available_at = parse_time(entry.get("availableAt"))
            recorded_at = parse_time(episode.get("firstRecordedAt"))
            last_observed_at = parse_time(episode_state.get("lastObservedAt"))
            feature_available = bool(available_at is not None and available_at <= order_start)
            recorded_before_entry = bool(recorded_at is not None and recorded_at <= order_start)
            within_observed_window = bool(
                last_observed_at is not None
                and episode["_entryQuote"] <= order_start <= last_observed_at
            )
            registered_live = bool(
                protocol_eligible
                and feature_available
                and recorded_before_entry
            )
            lag_context = {
                "status": "RECENT_EXACT_PACKAGE_LAG_EPISODE",
                "protocolStatus": protocol_status,
                "episodeId": episode.get("id"),
                "cohort": episode.get("cohort"),
                "entryQuoteAt": entry.get("quoteAt"),
                "availableAt": entry.get("availableAt"),
                "firstRecordedAt": episode.get("firstRecordedAt"),
                "minutesAfterEpisodeEntry": round(lead_minutes, 3),
                "entryFeatures": entry.get("features"),
                "episodeStatus": episode_state.get("status"),
                "episodeLastObservedAt": episode_state.get("lastObservedAt"),
                "featureAvailableBeforeOrderEntry": feature_available,
                "episodeRecordedBeforeOrderEntry": recorded_before_entry,
                "orderInsideObservedEpisodeWindow": within_observed_window,
                "registeredLiveExposure": registered_live,
            }
            lag_counts["recentExactPackageEpisode"] += 1
            if feature_available:
                lag_counts["featureAvailableBeforeOrderEntry"] += 1
            if recorded_before_entry:
                lag_counts["episodeRecordedBeforeOrderEntry"] += 1
            if registered_live:
                lag_counts["registeredLiveExposure"] += 1

        out.append({
            **match,
            "entryMarketContextStatus": context_status,
            "entryMarketContext": market_context,
            "entryLagContext": lag_context,
            "entryPreContext": pre_entry_context,
            "protocolPackageEligible": package_protocol_eligible,
            "protocolValidationEligible": protocol_eligible,
            "privateEdgeResearchRole": "ENTRY_TIME_DESCRIPTIVE_ONLY",
        })

    by_context = {}
    for status in sorted(context_counts):
        rows = [row for row in out if row.get("entryMarketContextStatus") == status]
        by_context[status] = stats(rows)

    by_lag = {
        "REGISTERED_LIVE_EXPOSURE": stats([
            row for row in out
            if (row.get("entryLagContext") or {}).get("registeredLiveExposure") is True
        ]),
        "NO_REGISTERED_LIVE_EXPOSURE": stats([
            row for row in out
            if (row.get("entryLagContext") or {}).get("registeredLiveExposure") is not True
        ]),
    }

    post_registration = [
        row for row in out if row.get("protocolValidationEligible") is True
    ]
    pre_entry_feature_summary = summarize_pre_entry_by_outcome(out)

    return {
        "schemaVersion": 1,
        "source": "PRIVATE_EASYMINING_ENTRY_MARKET_CONTEXT_EPHEMERAL",
        "privateDataPersistedToRepository": False,
        "privateDataUploadedAsArtifact": False,
        "networkRequestsMadeByContextJoin": 0,
        "currentProductionModelChanged": False,
        "canRaiseSignal": False,
        "automaticPurchase": False,
        "automaticCancel": False,
        "lockedLagProtocolChanged": False,
        "protocol": {
            "version": protocol.get("version"),
            "validationStart": protocol.get("validationStart"),
            "packages": sorted(protocol_packages),
        },
        "overall": stats(out),
        "marketContextStatusCounts": dict(context_counts),
        "protocolStatusCounts": dict(protocol_counts),
        "lagContextCounts": dict(lag_counts),
        "descriptiveByMarketContext": by_context,
        "descriptiveByRegisteredLagExposure": by_lag,
        "postRegistrationProtocolEligible": stats(post_registration),
        "preEntryFeatureSummary": pre_entry_feature_summary,
        "preEntrySettings": {
            "windowsMinutes": list(PRE_ENTRY_WINDOWS_MINUTES),
            "baselineToleranceSeconds": PRE_ENTRY_BASELINE_TOLERANCE_SECONDS,
            "requiresFreshPairedEndpoint": True,
            "exactPackageOnly": True,
            "quoteAndObservationMustPrecedeOrderEntry": True,
        },
        "matches": out,
        "verdict": "PRIVATE_DESCRIPTIVE_ENTRY_CONTEXT_ONLY_NO_AUTOMATIC_EDGE_CLAIM",
        "limitations": [
            "Private completed orders are user-selected entries, not randomized trials.",
            "Historical pre-registration orders are exploratory and cannot validate the locked lag protocol.",
            "A recorded lag episode near an entry does not prove incremental profitability or causality.",
            "Market PAIR data are public relative statistics and not independently verified executable EasyMining prices.",
            "Detailed order-level data remain ephemeral in /tmp and are not printed, uploaded or committed by the workflow.",
            "Pre-entry HIT/MISS differences are descriptive and user-selected; small samples and confounding prevent causal or automatic BUY conclusions.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=Path, default=DEFAULT_MATCHES)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--episodes", type=Path, default=DEFAULT_EPISODES)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print aggregate private metrics. Do not use this on a public CI log.",
    )
    args = parser.parse_args()

    for path in (args.matches, args.pairs, args.episodes, args.protocol):
        if not path.exists():
            parser.error(f"Missing input: {path}")
    if args.output.resolve() in {
        args.matches.resolve(),
        args.pairs.resolve(),
        args.episodes.resolve(),
        args.protocol.resolve(),
    }:
        parser.error("Output must be distinct from all inputs")

    private_report = json.loads(args.matches.read_text(encoding="utf-8"))
    if not isinstance(private_report, dict):
        parser.error("Private match report must be a JSON object")
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    if not isinstance(protocol, dict):
        parser.error("Lag protocol must be a JSON object")

    result = build(
        private_report,
        load_jsonl(args.pairs),
        load_jsonl(args.episodes),
        protocol,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )

    print("PRIVATE ENTRY MARKET CONTEXT OK")
    print("Detailed private order/outcome metrics retained only in runner /tmp.")
    print("No private analysis artifact or repository output was created.")
    if args.print_summary:
        print(json.dumps({
            "overall": result["overall"],
            "marketContextStatusCounts": result["marketContextStatusCounts"],
            "protocolStatusCounts": result["protocolStatusCounts"],
            "lagContextCounts": result["lagContextCounts"],
            "postRegistrationProtocolEligible": result["postRegistrationProtocolEligible"],
            "preEntryFeatureSummary": result["preEntryFeatureSummary"],
            "verdict": result["verdict"],
        }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
