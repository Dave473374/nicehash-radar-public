# Lag evidence integrity — 2026-09-19

This is a correction to the existing PR18 recorder/evaluator, not a second lag
model. No trigger, horizon, start date, CURRENT field, BATCH rule, alert delivery,
NiceHash access or collection schedule changes.

## Reproduced before editing

The existing 98 research tests passed, yet targeted cases demonstrated that:

1. `validationStart` and `lockedAt` could be moved together without rejection.
2. A work-per-cost value inconsistent with hashrate, duration and price could
   manufacture an episode.
3. A previously observed label later censored by corrected data remained
   evaluable without `sourceRevision`.
4. Conflicting old records sharing one ID silently overwrote one another.

The new tests preserve these counterexamples. Green CI is a correctness check
against known scenarios, not proof that every defect has been eliminated or that
any ticket will find a block.

## Corrections

- The SHA256 of the exact registered protocol is fixed in the validator. It is
  still `ba500589e4908319e07d57ca0466498b6318368b7b9dbee86ca77c4e94d79129`.
  The original file, hypothesis, numeric cutoffs and 2026-09-20 00:00 UTC holdout
  are unchanged. A future research revision must be explicit, never retroactive.
- Work/cost must agree with h * duration / native price within a numerical
  tolerance of 1e-9. This is a consistency tolerance, not an economic threshold.
- Conflicting ledger IDs, wrong IDs, duplicate horizons, malformed observed
  labels and nonfinite evidence cause processing to fail before output writing.
- Entry features and observed labels are retained verbatim. A label withdrawal,
  downgrade to missing/censored or a source-value revision quarantines the record
  from evaluation. Its old value remains in the ledger for audit. Aged-out records
  absent from a new input window are retained, not automatically invalidated.
- Merge operations do not mutate caller-owned input structures. First-recorded
  times cannot be silently reset on a rerun.
- Non-episode control anchors now have their own persistent ledger,
  `research/lag-controls.jsonl`, with the same integrity rules as episodes.
  Previous controls therefore do not disappear merely because the input archive
  rolls off. Reconstructed controls are marked with their real first recording
  time; they are not backdated to their quote time.
- Daily comparisons are separated by full package/currency/chain/version/unit
  signature. A different relay version or unit contract cannot silently become
  the comparison baseline.
- Folds separately count replay outcomes and anchors first recorded before the
  target horizon. Detection-delay statistics describe research execution, NOT
  phone receipt or purchase latency. Even early-recorded features are not live
  trading or realized-profit evidence.

Reports use schemaVersion 2. The public source files and episode identities are
unchanged. `NO_VERIFIED_EDGE`, `canRaiseSignal=false`, and no-auto-transaction
policy remain in force. Existing old records without capture timestamps are
not treated as timely observations.

## Live facts motivating the change

At pinned repository commit 7063125ae797b394d8f5de8045d24874cfce55c7,
there was one exploratory Palladium S episode. Its quote time was
2026-09-19 07:49:32.737 UTC, CURRENT was WAIT, MATH was WARNING, and it was first
recorded at 08:31:02.416 UTC (about 41.5 minutes later). Its +15-minute label was
MISSING and its +30-minute label CENSORED. It is NOT a successful prediction, a
BUY alert, or a found block. No result has been fabricated to fill the gap.

Neither this patch nor the source archive confirms delivery of the separate
phone-push test. The existing hourly task is not a five-minute notification path.

## Validation

Run `python3 -m unittest discover -s tests -p 'test_market*.py' -v` and
`python3 scripts/test_phase0_correctness.py`.

New scenarios cover locked registration, arithmetic consistency, retracted
labels, conflicting ledger records, capture timing, version isolation, and
control persistence. CLI output paths are checked against sources and against
one another. Public research CI runs these checks before processing existing
public data, and only its own research outputs are committed.

The remaining promotion blockers are unchanged: independently verified mining
inputs, executable price meaning, forward quote evidence versus a fair baseline,
and measured source-to-phone-to-purchase latency. Do not buy extra tickets to
populate statistics. Quote changes must never be relabeled HIT/MISS.
