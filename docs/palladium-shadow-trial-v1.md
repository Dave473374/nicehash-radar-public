# Palladium S/M forward shadow trial v1

## Scope and deployment

This is a **72-hour development/integration test**, not permanent application
hosting or a verified mining strategy. It runs inside the EXISTING
`Collect Public Market History` workflow and reuses the public sampler from
PR #61, conditional economics from PR #60, and lossless archive I/O from PR #63.
No second scheduled collector, account key, Admin request, order purchase,
notification or CURRENT/BUY change is introduced.

At its first main-branch invocation, the trial records `startedAt` and
`expiresAt = startedAt + 72h`. Restarting a batch cannot reset or extend that
window. During the test, the existing workflow takes up to 31 public samples
60 seconds apart (30 minutes between the first and last) and commits one batch.
The initial push-triggered run takes six samples to verify deployment sooner.
After expiry, the existing single-snapshot collection/enrichment path resumes.
There are no self-dispatch loops and no attempt to claim GitHub supports a
one-minute cron.

GitHub Actions scheduling/queue/checkout/publish gaps remain possible between
batches. This is explicitly **not guaranteed uninterrupted 60-second coverage**.
Every receipt gap is reported; a gap above 90 seconds resets candidate baselines
and confirmation. A permanent minute service should use a suitable runtime
rather than treating a successful GitHub test batch as a deployment guarantee.
The Cloudflare deployment connector was searched and is not available here;
no Cloudflare worker deployment has been claimed or modified.

Primary platform references checked 2026-09-26:
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule
- https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features

## Frozen candidate definition

The protocol is `research/palladium-shadow-protocol-v1.json`; its canonical hash
and the calculation/sampler code hash are retained with every observation and
state. V1 numeric rules are also checked against fixed code constants. A rule or
model change cannot silently continue an existing trial. A changed experiment
needs a separately reviewed version and its own evaluation.

Palladium **S is PRIMARY**, **M is SECONDARY**. Each has independent baseline,
confirmation and one-hour cooldown state. Native BTC package cost and quoted
hashrate/duration determine work/BTC and implied BTC per TH-day.

The baseline is the immediately preceding DISTINCT valid quote for the same
package, relay version, cost, duration, reward basis and calculation code. Both
receipt-time and quote-source-time gaps must be 30-90 seconds. This is deliberately
more specific than the earlier sparse retrospective <=20-minute comparison;
those historical event counts cannot be treated as validation of this minute
protocol.

A candidate requires:

1. At least 10% more quoted work per BTC than that baseline.
2. Difficulty-based AND hashrate/target-based conditional return strictly above
   100% (100% exactly is not sufficient).
3. Both existing LTC and DOGE consistency checks PASS.

A stronger *repeat-observed* candidate requires **two additional consecutive
minute observations after the entry**, i.e. THREE distinct observations total.
Both economic/consistency gates must still hold, and each later quote must retain
at least 95% of the entry work/BTC. This records support at sampled instants,
NOT proof of uninterrupted conditions between samples.

Duplicates, older source timestamps, errors, missing/invalid values and gaps
interrupt confirmation and clear the previous baseline. They are not HIT/MISS or
trade-loss labels. Candidates and confirmations never raise production signals.
All data, including rejected/ordinary observations, is retained; the archive is
not winners-only. A one-hour cooldown reduces repeated event counting but does
not establish independent mining trials.

## Source and chronology

Only newly fetched samples after activation enter the production trial. PR smoke
runs are labelled `PR_SMOKE_EXCLUDED_FROM_TRIAL`, use temporary outputs, never
activate the production clock and are not imported into its archive.

Both conditional models are reconstructed from the SAME quoted work and retained
chain/reward/FX fields. Quote and FX reported ages must be <=120 seconds. Quote
source time cannot exceed receipt time. These checks do not independently verify
component refreshes in the upstream relay. Registry metadata is reused within a
bounded batch; raw public market price remains unnormalized and is not an
executable purchase quote. No extra fee is blindly deducted from potentially net
reward fields.

A candidate may disappear well before the quoted ticket's full duration. Nothing
here measures accepted work, delivery, execution slippage, network conditions
through the ticket, real rewards or profit. The locked public-market lag protocol
is a separate hypothesis and remains unchanged.

Detection timestamps describe computation INSIDE the sampling runner. GitHub
publication occurs only after a batch (up to about 30 minutes plus scheduling or
push delays). The code sends no alerts and cannot claim minute-latency phone
notifications or execution from a minute sampling cadence.

## Storage, consistency and limits

Normal public market samples continue into the same 30-day
`calibration/public-market-history.jsonl` with an 85 MiB pre-publication cap. If
capacity is reached, the trial fails visibly instead of silently dropping rows to
make room. The finite three-day test is not unlimited-volume storage.

Whitelisted S/M observations, reasons and evaluations are also stored through the
EXISTING lossless archive adapter at the logical path
`calibration/palladium-shadow-history.jsonl` (physical adjacent manifest/chunks).
The public-snapshot hash binds each trial record to its market/economics source.
The canonical full Radar snapshot archive is untouched by this trial.

`research/palladium-shadow-state.json` contains the protocol/code hashes, immutable
activation window and verified archive digest. A lost state or digest mismatch
cannot quietly restart the experiment. `research/palladium-shadow-report.json`
reports counts, gaps, candidates, confirmations and the latest batch. Git commits
publish state/archive/public samples atomically together. A failed run retains
its per-sample checkpoint and available outputs as a short-lived Actions artifact;
it is not automatically imported later as though it had been available on time.

HTTP 401, 403 or 429 stops that batch and suspends trial requests rather than
repeating forbidden/rate-limited calls every minute. Other acquisition failures
are recorded as unavailable, not zero prices. A healthy state is never fabricated
from missing input. Publishing and ordinary fetch/rebase retry remain bounded;
there is no force push.

## Review after the test

First review coverage and publication gaps, then candidate counts per package,
number of distinct supported observations, interruptions, observed changes in
work/cost and differences between the two conditional models. Zero candidates
with poor coverage does not show that opportunities do not exist. A large candidate
count does not show profit. No thresholds are to be adjusted using these results
and then relabelled as the same validation.

The test automatically stops dense sampling after 72 hours. Extending it,
introducing phone notifications or buying a test ticket is a separate decision.
