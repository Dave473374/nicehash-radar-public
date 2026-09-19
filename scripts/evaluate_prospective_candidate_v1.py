"""Evaluate frozen Palladium S prospective momentum candidate v1.

This is a private, aggregate-only prospective evaluator. It consumes the
ephemeral causal Git buy-feed context built from completed EasyMining orders.
It never changes BUY logic, CURRENT/final_signal, or any NiceHash state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_CANDIDATE = Path("research/palladium-s-momentum-candidate-v1.json")
DEFAULT_CONTEXT = Path("/tmp/private-order-git-feed-context.json")
DEFAULT_OUTPUT = Path("/tmp/private-prospective-candidate-v1.json")

LOCKED_CANDIDATE_HASH = "48230b7b11ce54382efd129029a2d59d1fcd6a1fd1aa324913bca0472b599282"

CORE_FIELDS = (
    "packageWorkChangePercent",
    "expectedReturnChangePercentagePoints",
    "qualityVs24hChangePercentagePoints",
    "qualityVs7dChangePercentagePoints",
)


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def candidate_hash(candidate: dict) -> str:
    return hashlib.sha256(canonical(candidate).encode("utf-8")).hexdigest()


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def validate_candidate(candidate: dict) -> dict:
    if candidate_hash(candidate) != LOCKED_CANDIDATE_HASH:
        raise ValueError(
            "Prospective candidate v1 changed; create a new version instead of tuning v1"
        )

    expected = {
        "version": 1,
        "candidateId": "PALLADIUM_S_60M_MOMENTUM_V1",
        "status": "FROZEN_PROSPECTIVE_CANDIDATE",
        "package": "Palladium S",
        "currencyMarket": "BTC",
        "primaryCoin": "LTC",
        "mergeCoin": "DOGE",
        "featureSource": "CAUSAL_GIT_BUY_FEED_PRE_ENTRY",
        "endpointMaxAgeMinutes": 15,
        "horizonMinutes": 60,
        "baselineToleranceMinutes": 20,
        "sameRelayRequired": True,
        "automaticActions": False,
        "canRaiseSignal": False,
        "currentProductionModelChanged": False,
    }
    for key, value in expected.items():
        if candidate.get(key) != value:
            raise ValueError(f"Locked candidate field changed: {key}")

    locked = parse_time(candidate.get("lockedAt"))
    validation = parse_time(candidate.get("validationStart"))
    cutoff = parse_time((candidate.get("registrationEvidence") or {}).get("sourceCutoff"))
    if (
        locked is None
        or validation is None
        or cutoff is None
        or not cutoff <= locked < validation
        or validation.utcoffset().total_seconds() != 0
        or any((validation.hour, validation.minute, validation.second, validation.microsecond))
    ):
        raise ValueError("Candidate needs a future UTC-day holdout after registration evidence")

    rules = candidate.get("coreMomentumRules")
    if not isinstance(rules, dict) or tuple(rules.keys()) != CORE_FIELDS:
        raise ValueError("Locked core feature set changed")
    for field in CORE_FIELDS:
        rule = rules.get(field)
        if rule != {"operator": "GT", "value": 0.0}:
            raise ValueError(f"Locked rule changed: {field}")

    guards = candidate.get("causalGuards") or {}
    required_true = (
        "gitCommitMustPrecedeOrderEntry",
        "feedCheckedAtMustPrecedeOrderEntry",
        "baselineMustBeAtOrBeforeTarget",
        "exactPackageCoinCurrencyRequired",
        "sameRelayWithinWindowRequired",
        "legacy282BtcOnlySchemaBridgeAllowed",
    )
    if any(guards.get(key) is not True for key in required_true):
        raise ValueError("Locked causal guard changed")

    prospective = candidate.get("prospectiveEvaluation") or {}
    if prospective.get("eligibleOutcomes") != ["HIT", "MISS"]:
        raise ValueError("Locked eligible outcomes changed")
    if prospective.get("primaryComparison") != "CORE_MATCH_VS_NOT_CORE_MATCH":
        raise ValueError("Locked prospective comparison changed")
    if prospective.get("minimumEligibleOrdersBeforeAnyPerformanceClaim") != 30:
        raise ValueError("Locked minimum eligible-order gate changed")
    if prospective.get("minimumHitsBeforeAnyPerformanceClaim") != 3:
        raise ValueError("Locked minimum HIT gate changed")
    if prospective.get("reportBeforeGate") != "COUNTS_ONLY_EXPLORATORY":
        raise ValueError("Locked pre-gate reporting policy changed")

    return candidate


def classify_window(candidate: dict, window: dict) -> tuple[str | None, int | None]:
    passed = 0
    for field in CORE_FIELDS:
        value = number(window.get(field))
        if value is None:
            return None, None
        if value > 0:
            passed += 1

    if passed == 4:
        return "CORE_MATCH", passed
    if passed == 3:
        return "PARTIAL_3_OF_4", passed
    return "NO_MATCH", passed


def exact_scope(candidate: dict, row: dict) -> bool:
    return (
        row.get("packageName") == candidate["package"]
        and str(row.get("currencyMarket") or "").upper() == candidate["currencyMarket"]
        and str(row.get("coin") or "").upper() == candidate["primaryCoin"]
        and str(row.get("mergeCoin") or "").upper() == candidate["mergeCoin"]
    )


def selected_window(candidate: dict, context: dict) -> dict | None:
    sensitivity = context.get("baselineToleranceSensitivity")
    if not isinstance(sensitivity, dict):
        return None
    windows = sensitivity.get(str(candidate["baselineToleranceMinutes"]))
    if not isinstance(windows, list):
        return None
    for window in windows:
        if (
            isinstance(window, dict)
            and window.get("horizonMinutes") == candidate["horizonMinutes"]
            and window.get("status") == "MATCHED"
        ):
            return window
    return None


def rate(hits: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(100 * hits / total, 4)


def evaluate(candidate: dict, context_doc: dict) -> dict:
    candidate = validate_candidate(candidate)
    validation_start = parse_time(candidate["validationStart"])
    rows = context_doc.get("orders")
    if not isinstance(rows, list):
        raise ValueError("Git-feed context missing orders list")

    classifications = Counter()
    outcome_counts = Counter()
    ineligible = Counter()

    post_validation_scope_orders = 0
    eligible_orders = 0
    eligible_hits = 0

    for row in rows:
        if not isinstance(row, dict) or not exact_scope(candidate, row):
            continue

        start = parse_time(row.get("orderStartTs"))
        if start is None:
            ineligible["INVALID_ORDER_START"] += 1
            continue
        if start < validation_start:
            # Registration-era rows are deliberately invisible to prospective counts.
            continue

        post_validation_scope_orders += 1
        outcome = str(row.get("outcome") or "UNKNOWN")
        if outcome not in {"HIT", "MISS"}:
            ineligible["UNKNOWN_OUTCOME"] += 1
            continue

        context = row.get("gitFeedPreEntry")
        if not isinstance(context, dict) or context.get("status") != "FRESH_ENDPOINT":
            ineligible["NO_FRESH_CAUSAL_ENDPOINT"] += 1
            continue

        window = selected_window(candidate, context)
        if window is None:
            ineligible["NO_MATCHED_60M_WINDOW_AT_20M_TOLERANCE"] += 1
            continue

        if candidate.get("sameRelayRequired") is True:
            endpoint_relay = context.get("relayVersion")
            window_relay = window.get("relayVersion")
            if (
                not isinstance(endpoint_relay, str)
                or not endpoint_relay
                or window_relay != endpoint_relay
            ):
                ineligible["RELAY_MISMATCH"] += 1
                continue

        classification, passed = classify_window(candidate, window)
        if classification is None or passed is None:
            ineligible["MISSING_CORE_FEATURE"] += 1
            continue

        eligible_orders += 1
        if outcome == "HIT":
            eligible_hits += 1
        classifications[classification] += 1
        outcome_counts[(classification, outcome)] += 1

    eligible_misses = eligible_orders - eligible_hits
    core_hits = outcome_counts[("CORE_MATCH", "HIT")]
    core_misses = outcome_counts[("CORE_MATCH", "MISS")]
    core_total = core_hits + core_misses

    not_core_hits = (
        outcome_counts[("PARTIAL_3_OF_4", "HIT")]
        + outcome_counts[("NO_MATCH", "HIT")]
    )
    not_core_misses = (
        outcome_counts[("PARTIAL_3_OF_4", "MISS")]
        + outcome_counts[("NO_MATCH", "MISS")]
    )
    not_core_total = not_core_hits + not_core_misses

    gates = candidate["prospectiveEvaluation"]
    gate_met = (
        eligible_orders >= gates["minimumEligibleOrdersBeforeAnyPerformanceClaim"]
        and eligible_hits >= gates["minimumHitsBeforeAnyPerformanceClaim"]
    )

    performance = {
        "status": "GATE_MET" if gate_met else "AWAITING_MINIMUM_PROSPECTIVE_SAMPLE",
        "minimumEligibleOrders": gates["minimumEligibleOrdersBeforeAnyPerformanceClaim"],
        "minimumHits": gates["minimumHitsBeforeAnyPerformanceClaim"],
        "eligibleOrders": eligible_orders,
        "eligibleHits": eligible_hits,
        "coreMatchHitRatePercent": rate(core_hits, core_total) if gate_met else None,
        "notCoreMatchHitRatePercent": rate(not_core_hits, not_core_total) if gate_met else None,
        "hitRateDifferencePercentagePoints": (
            round(rate(core_hits, core_total) - rate(not_core_hits, not_core_total), 4)
            if gate_met
            and rate(core_hits, core_total) is not None
            and rate(not_core_hits, not_core_total) is not None
            else None
        ),
        "performanceConclusionAllowed": gate_met,
    }

    status = (
        "AWAITING_PROSPECTIVE_ORDERS"
        if post_validation_scope_orders == 0
        else "PROSPECTIVE_COUNTS_ACCUMULATING"
        if not gate_met
        else "MINIMUM_EVALUATION_GATE_REACHED"
    )

    return {
        "schemaVersion": 1,
        "role": "FROZEN_PROSPECTIVE_CANDIDATE_EVALUATION",
        "candidateId": candidate["candidateId"],
        "candidateHash": LOCKED_CANDIDATE_HASH,
        "candidateLockedAt": candidate["lockedAt"],
        "validationStart": candidate["validationStart"],
        "status": status,
        "registrationEvidenceExcluded": True,
        "postValidationScopeOrdersSeen": post_validation_scope_orders,
        "eligibleOrders": eligible_orders,
        "eligibleHits": eligible_hits,
        "eligibleMisses": eligible_misses,
        "classificationCounts": {
            "CORE_MATCH": classifications["CORE_MATCH"],
            "PARTIAL_3_OF_4": classifications["PARTIAL_3_OF_4"],
            "NO_MATCH": classifications["NO_MATCH"],
        },
        "classificationOutcomeCounts": {
            "CORE_MATCH|HIT": core_hits,
            "CORE_MATCH|MISS": core_misses,
            "PARTIAL_3_OF_4|HIT": outcome_counts[("PARTIAL_3_OF_4", "HIT")],
            "PARTIAL_3_OF_4|MISS": outcome_counts[("PARTIAL_3_OF_4", "MISS")],
            "NO_MATCH|HIT": outcome_counts[("NO_MATCH", "HIT")],
            "NO_MATCH|MISS": outcome_counts[("NO_MATCH", "MISS")],
        },
        "primaryComparisonCounts": {
            "CORE_MATCH|HIT": core_hits,
            "CORE_MATCH|MISS": core_misses,
            "NOT_CORE_MATCH|HIT": not_core_hits,
            "NOT_CORE_MATCH|MISS": not_core_misses,
        },
        "ineligibleReasons": dict(sorted(ineligible.items())),
        "performanceGate": performance,
        "containsOrderIds": False,
        "containsPrivateOrderTimestamps": False,
        "containsAmountsOrRoi": False,
        "automaticActions": False,
        "canRaiseSignal": False,
        "currentProductionModelChanged": False,
        "verdict": "PROSPECTIVE_MONITOR_ONLY_NO_SIGNAL_OVERRIDE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for path in (args.candidate, args.context):
        if not path.exists():
            parser.error(f"Missing input: {path}")
    if args.output.resolve() in {args.candidate.resolve(), args.context.resolve()}:
        parser.error("Output must differ from inputs")

    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    context_doc = json.loads(args.context.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict) or not isinstance(context_doc, dict):
        parser.error("Candidate and context must be JSON objects")

    result = evaluate(candidate, context_doc)
    args.output.write_text(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print("FROZEN PROSPECTIVE CANDIDATE V1 EVALUATION OK")
    print("Only aggregate prospective counts written to /tmp; no order details printed.")


if __name__ == "__main__":
    main()
