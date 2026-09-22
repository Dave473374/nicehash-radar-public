# Reward events versus winning orders

This document defines a correctness invariant for BUY Radar calibration.

## Frozen unit distinction

A **completed order** is the HIT/MISS exposure unit.

- one completed order with at least one reward is one **HIT order**;
- one completed order with no reward is one **MISS order**;
- one order is never counted as multiple HIT orders merely because it produced
  multiple rewards.

A **reward count** is a separate source/intensity metric. Depending on the
source, it may represent reward records, payout legs, or successful mining
events. It is not a count of winning orders.

This distinction matters especially for high-block-frequency chains such as
KAS/Titanium, where one winning order can produce many reward records/events.

## Required guardrails

1. Reward/event counts must never supply the HIT/MISS denominator.
2. A binary HIT must not synthesize `rewardCount=1` when reward multiplicity is
   unknown.
3. Missing reward multiplicity remains unavailable; it is not silently replaced
   with zero or one.
4. Event-level public evidence remains separate from order-level outcome
   calibration.
5. Reward intensity may be researched separately from HIT rate only when the
   source unit is explicit and complete.
6. ROI still requires explicit complete payout and cost data; neither HIT/MISS
   nor reward-count intensity alone proves profitability.

## Source-specific uncertainty

The exact meaning of a source field named `rewardCount`, reward-list length, or
dashboard reward counter must be verified before comparing intensity across
sources. Merged-mining payout legs and successful block events may be different
units.

Non-public aggregate observations that motivated this guardrail are deliberately
not persisted in this public repository.

## Implementation

- `scripts/match_global_orders.py` keeps order-level HIT/MISS separate from
  reward-record multiplicity.
- `scripts/collect_completed_orders.py` preserves whether reward multiplicity
  was actually present in the source.
- `scripts/build_daily_package_stats.py` labels successful-event counts as
  intensity/numerator evidence only.
- `tests/test_reward_order_semantics.py` prevents a future regression that
  turns a HIT into an invented reward count of one.
