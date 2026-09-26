# Palladium shadow evaluator v1

## Boundary

This reads the already running PR #64 experiment. It does not change its protocol,
collector, model code hash, activation/expiry, state, raw history, source requests,
BUY decisions, alerts or purchases. The original 72-hour window remains fixed.

The evaluation plan was registered AFTER trial activation and after inspecting
the first 68 persisted attempts. It is not described as pre-trial preregistration.
This adds fixed descriptive metrics, not new trading gates, minimum-event targets
or automatic threshold tuning. The 10% work/BTC rule and the three-observation
confirmation remain those in the original frozen protocol.

No existing evaluator for this trial was found in the inspected tree. The new
reader reuses the lossless archive from PR #63 and the unchanged PR #64 state
machine; it does not recreate either one. Original trial code is imported only
for deterministic calculation, never its collect/main functions.

## Verify before interpreting

The evaluator validates the compressed archive and its logical SHA256, then finds
each attempt's exact `publicSnapshotHash` in existing public market history. It
replays every attempt through the frozen code, in original order, requiring
byte-equivalent canonical JSON for every archived evaluation and the final state.
It cross-checks the collector report's counters, episodes, model/protocol hashes,
archive identity and timestamps. Unknown/missing/mixed inputs or changed model
code fail explicitly. There is no substitution of a newer or more favorable
quote, no skipping malformed/losing/invalid rows and no reinterpretation of PR
smoke data as prospective evidence.

All physical source files and relevant code are hashed before and after the
calculation. A concurrent change fails the evaluation. Output includes the exact
checkout commit, hashes, evaluation cutoff and last observed receipt. The result
is a statement about THAT saved history, not a claim to know uncommitted data.
An invalid run does not overwrite a previous valid report with a zero result;
its failed workflow and the older report's timestamp expose the limitation.

Public market source retention remains the existing 30 days. Later replay after
source expiry may therefore fail. Preserve the source archive before retention
expiry for long-term reproducibility; do not treat the report alone as the raw
source. This evaluator does not silently change the collector's retention policy.

## Coverage is not availability

Report separately:

- acquisition attempts and matched public responses;
- VALID, distinct, accepted per-package observations versus invalid and duplicate
  source observations;
- exact upstream/enrichment reason counts (reasons can overlap);
- source/receipt gaps and maximal streaks satisfying the already frozen
  same-series/consecutive-sample rules;
- occupancy of completed 60-second slots measured from activation to the stated
  cutoff, with both overall and usable-per-package occupancy;
- the initial and trailing time with no recorded observations.

A fixed slot can contain two jittered observations; it still counts once. A final
partial slot is excluded from the coverage denominator. Occupied slots and spans
between endpoints do NOT establish continuous coverage, seconds of availability,
or conditions between samples. A trailing gap may simply be a batch still being
collected or waiting for publication; it is not by itself an outage.

Most importantly, `MISSING_OR_DUPLICATE_PACKAGE` does not prove
`available:false`. The frozen shadow input does not retain a complete raw public
package inventory for every minute. Therefore exact unavailable counts and
available minutes remain **null**, not invented zeroes or inferred durations.
The earlier one-off direct public availability observation is separate evidence
for its specific timestamp; it cannot label the rest of the trial.

A VALID observation means the numerical quote input is usable for analysis. It
can still have MATH WARNING/CRITICAL or conditional returns below 100%. Usable
observations, math-gate observations, candidates and repeat confirmations are
reported separately. S stays PRIMARY and M SECONDARY.

## Candidate/confirmation evaluation

Every replayed candidate is included, confirmed or not. Episode records contain
the count of supporting observations, endpoint span, confirmation delay, end
reason and right-censoring. An observed gate/work failure is distinguished from a
missing/invalid/gapped observation. Neither is a mining MISS or realized loss.
A last supported observation with no later end observation is right-censored,
not a claim that the condition is still present now.

The time between last support and the next failed observation is reported as a
bracket only. The exact end time and continuous condition duration are unknown.
Three matching observations do not verify execution, delivery or profitability.
No order-level hit rate, P/L or validated edge is derived from quote records.

## Publication delay

For the collector's most recent batch, the evaluator measures observation-to-
collector-report-generation delay using the reported batch count. This describes
batching, not actual GitHub publication, phone notification or execution latency.
Those unmeasured quantities remain null. Minute sampling is not minute delivery.

## Automation and safety

`Evaluate Palladium Shadow` has no schedule and no NiceHash/relay requests. It runs
on reviewed-code changes, manual invocation, or completion of the existing
`Collect Public Market History` workflow on main. It checks out repository code;
it does not download/execute upstream workflow artifacts or untrusted PR code
with a write token. PR runs are read-only. The separately scoped publish job
writes only:

- `research/palladium-shadow-evaluation.json`
- `research/palladium-shadow-evaluation.md`

The active collector, protocol, state and archives remain untouched. Ordinary
Git races use bounded fetch/rebase, never force push. The evaluator's own output
commits do not trigger another evaluator or change the collector model hash.
The existing final-review automation can read this report; no new phone task is
created by this change.

GitHub workflow_run reference (consulted 2026-09-26):
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run

## Reproduction

From the exact recorded checkout, Python 3.12:

```sh
python3 -m unittest tests.test_palladium_shadow_evaluation -v
python3 scripts/evaluate_palladium_shadow.py --now <aware-ISO-cutoff>
```

The cutoff must not precede a saved attempt or its collector report; evidence is
not selectively truncated to force a convenient result. Omit `--now` for current
UTC. During the trial, status is INTERIM_TRIAL_RUNNING. After the clock expires,
the report indicates elapsed time, but does not independently certify that the
collector stopped or all remaining data was published. Review actual workflows
and source freshness separately at the scheduled final assessment.
