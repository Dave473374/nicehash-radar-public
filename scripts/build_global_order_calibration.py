import json
import os
from datetime import datetime, timezone
from pathlib import Path

INPUT = Path(
    os.getenv(
        "GLOBAL_MATCH_INPUT",
        "/tmp/global-order-matches.json",
    )
)
OUTPUT = Path(
    os.getenv(
        "GLOBAL_CALIBRATION_REPORT",
        "calibration/global-order-calibration.json",
    )
)


def confidence_for(orders, hits):
    if orders >= 2000 and hits >= 50:
        return "HIGH"

    if orders >= 500 and hits >= 20:
        return "MEDIUM"

    return "LOW"


if not INPUT.exists():
    raise SystemExit(
        f"Global match input missing: {INPUT}"
    )

source = json.loads(
    INPUT.read_text(encoding="utf-8")
)

overall = source.get("overall") or {}
orders = int(overall.get("orders") or 0)
hits = int(overall.get("hits") or 0)
lifecycle = source.get("lifecycleSemantics") or {}
confidence_orders = int(
    lifecycle.get("verifiedCompletedKnownOutcomeOrders") or 0
)
confidence_hits = int(
    lifecycle.get("verifiedCompletedHits") or 0
)

report = {
    "reportVersion": 1,
    "generatedAt": datetime.now(
        timezone.utc
    ).isoformat(),
    "source": "SANITIZED_GLOBAL_COMPLETED_EASY_ORDERS",
    "modelUse": "NEW_CALIBRATED_SHADOW_ONLY",
    "currentProductionModelChanged": False,
    "confidence": confidence_for(
        confidence_orders,
        confidence_hits,
    ),
    "confidenceBasis": {
        "orders": confidence_orders,
        "hits": confidence_hits,
        "rule": "VERIFIED_COMPLETED_KNOWN_OUTCOMES_ONLY",
        "legacyUnknownLifecycleExcludedFromPromotion": True,
    },
    "confidenceRule": {
        "LOW": "<500 matched orders or <20 HITs",
        "MEDIUM": ">=500 matched orders and >=20 HITs",
        "HIGH": ">=2000 matched orders and >=50 HITs",
    },
    "privacy": {
        "rawAdminOrdersStoredInPublicRepo": False,
        "individualMatchedOrdersStoredInReport": False,
        "reportContainsAggregatesOnly": True,
    },
    "scope": {
        "inputOrders": source.get("inputOrders"),
        "uniqueOrders": source.get("uniqueOrders"),
        "duplicateOrdersRemoved": source.get(
            "duplicateOrdersRemoved"
        ),
        "matchedCurrentRadarOrders": source.get(
            "validMatchedOrders"
        ),
        "skippedOrders": source.get(
            "skippedOrders"
        ),
        "skipReasons": source.get(
            "skipReasons"
        ),
    },
    "overall": overall,
    "roi": source.get("roi"),
    "lifecycleSemantics": lifecycle,
    "rewardSemantics": source.get("rewardSemantics") or {
        "hitMissUnit": "COMPLETED_ORDER",
        "hitDefinition": "ONE_ORDER_WITH_AT_LEAST_ONE_REWARD",
        "rewardCountUnit": "SOURCE_REWARD_RECORDS_NOT_WINNING_ORDERS",
        "rewardCountMayExceedOnePerOrder": True,
        "rewardCountCanSupplyHitMissDenominator": False,
        "missingRewardCountSynthesizedFromHit": False,
    },
    "signalStats": source.get(
        "signalStats",
        [],
    ),
    "packageStats": source.get(
        "packageStats",
        [],
    ),
    "coinStats": source.get(
        "coinStats",
        [],
    ),
    "currencyStats": source.get(
        "currencyStats",
        [],
    ),
    "guardrails": [
        "CURRENT remains the production decision model.",
        "NEW/CALIBRATED is shadow-only while confidence is LOW.",
        "Do not use soloMiningSharesMaxPercent as a live predictive feature.",
        "Do not infer ROI from HIT/MISS-only admin rows without explicit payout amounts.",
        "Global order calibration remains separate from event-level block calibration.",
        "Reward/event counts are intensity evidence, not counts of winning orders; one winning order may produce many reward records.",
        "Explicit CANCELLED, EXPIRED or other non-COMPLETED lifecycle rows are censored and must not be counted as MISS.",
        "Legacy rows without preserved lifecycle status may remain descriptive evidence but cannot promote confidence.",
    ],
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
    ) + "\n",
    encoding="utf-8",
)

print("GLOBAL CALIBRATION REPORT")
print("Matched orders:", orders)
print("Hits:", hits)
print("Confidence:", report["confidence"])
print("Aggregate-only report written:", OUTPUT)
