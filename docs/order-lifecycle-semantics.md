# Order lifecycle and HIT/MISS censoring

BUY Radar treats lifecycle state separately from mining outcome.

## Denominator rule

The preferred HIT/MISS denominator is **verified COMPLETED orders with known
outcomes**.

- `COMPLETED` + reward -> HIT.
- `COMPLETED` + no reward -> MISS.
- `CANCELLED`, `EXPIRED`, pending/error states, and any other explicit
  non-COMPLETED lifecycle state are **censored**, not MISS.

A cancelled ticket may have received only part of the planned duration/hash
work. Counting it as a normal MISS would bias package and signal calibration.

## Legacy data

Some earlier sanitized batches did not preserve lifecycle status. Those rows may
remain as provisional/descriptive historical evidence, but they are not
lifecycle-verified and must not by themselves advance calibration confidence.

New sources should preserve a normalized lifecycle status whenever the source
provides one. Missing status remains explicitly unknown; it is never invented.

## Interaction with reward semantics

Lifecycle and reward multiplicity are independent dimensions:

1. lifecycle says whether an exposure was completed or censored;
2. HIT/MISS says whether a completed exposure produced at least one reward;
3. reward/event count measures intensity and may be greater than one per
   winning order;
4. ROI requires complete cost/refund/payout semantics.

## Public-repo boundary

This document records only the generic correctness rule. Non-public dashboard
counts or organization-internal aggregates that motivated the rule are not
persisted in this repository.
