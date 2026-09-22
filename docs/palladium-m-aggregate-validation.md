# Palladium M aggregate validation protocol

This protocol defines how daily or monthly Palladium M aggregate outcomes may be used alongside BUY Radar history without pretending that aggregate data are order-level causal labels.

## Goal

Test whether periods with more favorable Palladium M Radar states show better global outcome rates than less favorable periods.

This is supporting validation only. It does not replace order-level entry-time HIT/MISS or realized ROI evidence.

## Required outcome input

For every aggregate period used in analysis, preserve at minimum:

- period/date and timezone;
- package = Palladium M;
- lifecycle scope = COMPLETED only;
- completed order count;
- winning-order count, if the source directly provides it;
- otherwise reward-record count with a separately verified source contract explaining how reward records map to winning orders;
- order amount and reward amount only when their units and lifecycle scope are verified.

Explicit CANCELLED, EXPIRED, pending, error or other non-COMPLETED tickets are censored and must not be added to the MISS denominator.

## Radar exposure reconstruction

`scripts/build_palladium_m_signal_exposure.py` reconstructs the signal that was actually available from saved BUY-feed snapshots.

Rules:

1. exact package = Palladium M, primary coin = LTC, market = BTC;
2. both source time and local receipt time must be present;
3. source time may not be later than receipt time;
4. source data may be at most 15 minutes old at receipt;
5. signal exposure begins at receipt time, not the earlier source timestamp;
6. a snapshot remains usable for at most 15 minutes unless replaced earlier;
7. conflicting same-receipt snapshots are discarded;
8. missing coverage remains missing rather than being forward-filled.

The script reports per-day signal minutes and fractions in an explicit timezone.

## Aggregate comparison

Daily aggregates are not assigned directly to GOOD/WAIT merely because one signal appeared during that day. A day is primary-comparison eligible only when:

- at least 75% of that local day has valid Radar coverage; and
- one signal accounts for at least 80% of observed Palladium M signal time.

Those thresholds are conservative defaults and must be frozen before examining outcomes. Mixed days stay descriptive only.

For eligible days, compare at least:

- completed orders;
- winning orders;
- HIT rate = winning orders / completed orders;
- model-expected HIT rate from the same Radar regime when available;
- reward intensity separately from winning-order count;
- reward/order value ratio only if amount units are verified.

The primary comparison is GOOD-dominant versus WAIT-dominant periods. NO BUY, BUY NOW and STRONG BUY are reported separately when enough exposure exists.

## Important limitation: ecological evidence

Daily and monthly aggregates do **not** identify which exact order saw which entry signal. Order arrivals may also be uneven within a day.

Therefore:

- aggregate comparisons can support or contradict the Radar hypothesis;
- they cannot establish order-level causality;
- they cannot justify production promotion by themselves;
- a production claim still requires lifecycle-verified order-level outcomes or an equivalent authorized causal dataset.

## Reward timing

The analysis must verify how the aggregate source assigns rewards to periods. If rewards are bucketed by reward timestamp rather than order start/completion cohort, order durations can move outcomes across day boundaries. Until that semantics is verified, daily reward counts are supporting context only.

Monthly totals are less sensitive to day-boundary leakage but are also less useful for separating Radar regimes.

## Public-repository boundary

No Admin endpoints, credentials, screenshots, organization-internal aggregate counts, or individual order rows are stored by this protocol. Only generic methodology, public Radar history and synthetic tests belong in the public repo.
