"""Validate the persistent BUY Radar research signal register.

This is a structural/safety validator only. It does not promote signals,
calculate profitability, or change CURRENT/BUY decisions.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

DEFAULT_REGISTER = Path("research/research-signal-register.json")
ALLOWED_STATUSES = {
    "OBSERVATION",
    "TRACKING",
    "CANDIDATE",
    "VALIDATED",
    "PRODUCTION_ELIGIBLE",
    "PRODUCTION",
}
ALLOWED_CATEGORIES = {
    "BUY_FEATURE",
    "INFRASTRUCTURE",
    "DATA_QUALITY_BLOCKER",
    "ATTRIBUTION_SUPPORT",
}


def _aware_iso(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None


def validate(register_path: Path, repo_root: Path | None = None):
    repo_root = repo_root or register_path.resolve().parents[1]
    payload = json.loads(register_path.read_text(encoding="utf-8"))
    errors = []

    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if payload.get("role") != "BUY_RADAR_RESEARCH_SIGNAL_REGISTER":
        errors.append("unexpected role")
    if not _aware_iso(payload.get("generatedAt")):
        errors.append("generatedAt must be timezone-aware ISO-8601")

    status_order = payload.get("statusOrder")
    expected_order = [
        "OBSERVATION",
        "TRACKING",
        "CANDIDATE",
        "VALIDATED",
        "PRODUCTION_ELIGIBLE",
        "PRODUCTION",
    ]
    if status_order != expected_order:
        errors.append("statusOrder must use the frozen promotion sequence")

    governance = payload.get("governance")
    if not isinstance(governance, dict):
        errors.append("governance must be an object")
    else:
        required_true = (
            "humanApprovalRequiredForProduction",
            "productionChangeRequiresDedicatedPr",
            "baselineComparisonRequiredBeforeProduction",
            "causalPreEntryEvidenceRequiredForBuyFeatures",
            "missDenominatorOrAuthorizedOrderLevelOutcomesRequiredForPerformanceClaims",
            "noSignalMayAffectBuyBeforeProduction",
        )
        for key in required_true:
            if governance.get(key) is not True:
                errors.append(f"governance.{key} must be true")
        if governance.get("automaticPromotionAllowed") is not False:
            errors.append("governance.automaticPromotionAllowed must be false")

    signals = payload.get("signals")
    if not isinstance(signals, list) or not signals:
        errors.append("signals must be a non-empty list")
        signals = []

    seen_ids = set()
    for index, signal in enumerate(signals):
        prefix = f"signals[{index}]"
        if not isinstance(signal, dict):
            errors.append(f"{prefix} must be an object")
            continue
        signal_id = signal.get("id")
        if not isinstance(signal_id, str) or not signal_id:
            errors.append(f"{prefix}.id missing")
            continue
        if signal_id in seen_ids:
            errors.append(f"duplicate signal id: {signal_id}")
        seen_ids.add(signal_id)

        status = signal.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{signal_id}: invalid status {status!r}")
        category = signal.get("category")
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{signal_id}: invalid category {category!r}")

        can_affect = signal.get("canAffectBuySignal")
        production_eligible = signal.get("productionEligible")
        if status != "PRODUCTION" and can_affect is not False:
            errors.append(f"{signal_id}: non-PRODUCTION signal may not affect BUY")
        if status not in {"PRODUCTION_ELIGIBLE", "PRODUCTION"} and production_eligible is not False:
            errors.append(f"{signal_id}: productionEligible must be false before PRODUCTION_ELIGIBLE")

        snapshot = signal.get("currentEvidenceSnapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            errors.append(f"{signal_id}: currentEvidenceSnapshot must be non-empty")
        else:
            reviewed = snapshot.get("reviewedAt")
            if reviewed is not None and not _aware_iso(reviewed):
                errors.append(f"{signal_id}: reviewedAt must be timezone-aware ISO-8601")

        for key in ("blockingIssues", "reviewTriggers", "linkedEvidence"):
            value = signal.get(key)
            if not isinstance(value, list) or not value:
                errors.append(f"{signal_id}: {key} must be a non-empty list")

        promotion = signal.get("promotionCriteria")
        if not isinstance(promotion, dict) or not promotion:
            errors.append(f"{signal_id}: promotionCriteria must be a non-empty object")
        if not isinstance(signal.get("nextReviewWhen"), str) or not signal.get("nextReviewWhen"):
            errors.append(f"{signal_id}: nextReviewWhen missing")

        evidence = signal.get("linkedEvidence") or []
        for raw_path in evidence:
            if not isinstance(raw_path, str) or not raw_path:
                errors.append(f"{signal_id}: invalid linkedEvidence entry")
                continue
            path = repo_root / raw_path
            if not path.exists():
                errors.append(f"{signal_id}: linked evidence path missing: {raw_path}")

    if errors:
        raise ValueError("\n".join(errors))
    return {
        "signalCount": len(signals),
        "statusCounts": {
            status: sum(1 for signal in signals if signal.get("status") == status)
            for status in expected_order
        },
        "allNonProductionSignalsBlockedFromBuy": all(
            signal.get("status") == "PRODUCTION"
            or signal.get("canAffectBuySignal") is False
            for signal in signals
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    args = parser.parse_args()
    result = validate(args.register)
    print("RESEARCH SIGNAL REGISTER OK; no promotion performed")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
