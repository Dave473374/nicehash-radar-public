# Lag episodes v1 — fixed hypothesis, forward quote research

## What is implemented

This extends the existing public research workflow, not CURRENT or the alert
engine. `build_lag_episodes.py` reads only the existing paired quote file, public
market history, a committed protocol, and its previous public episode ledger.
It has no HTTP client, private API imports, keys, order history or trading actions.
The existing twice-hourly research schedule remains unchanged.

Outputs: `research/lag-episodes.jsonl` (persistent episode ledger) and
`research/lag-report.json` (coverage and time-separated evaluation). An empty
ledger is a valid result. Failed processing must not be interpreted as no edge;
check `generatedAt`. No output is a live purchase or cancellation instruction.

## One predeclared hypothesis

The protocol JSON freezes version 1. For Palladium S, Silver S, Silver 5 and
Silver 20, observe a 5–15 minute transition with all of:

- same-currency public market price statistic increases at least 5%;
- EasyMining ticket cost per quoted hash-work increases no more than 1%;
- the multiplicative market-price / ticket-cost-per-work divergence is at least 5%.

Hypothesis: these episodes precede less work per native cost at +15/+30 minutes.
These chosen cutoffs are starting research assumptions, NOT fitted optimal rules
or confidence levels. Negative-market moves and already-repriced tickets are
not equivalent positive cases. We do not search alternative thresholds on the
same sample. Changes require an explicit new protocol and a new future cutoff.

## Availability and unit contract

The existing strict as-of pair is rechecked. Exact matching public metadata must
have been recorded at that market observation; current metadata are never copied
back into legacy rows. The explicit display-unit contract must agree with the
package's currency. Raw-price denomination and executable all-in market price
remain UNVERIFIED, so comparisons are relative within unchanged unit signatures.
A new version, currency, units, incomplete point, conflicting duplicate, or a gap
longer than 15 minutes breaks continuity. Future and late observations are rejected.
Historical eligible coverage will be smaller than the old quote archive.

All quantities remain per native BTC or USDT, never conflated. CURRENT, EV and
MATH are stored as entry-time context, not used to manufacture a successful label.
CRITICAL math can still be observed for research, but it is never promoted to
PASS or to a purchase recommendation. No comparison here resolves the underlying
mining-math disagreement or proves net profitability.

## Episode and outcome semantics

The start is frozen under a deterministic protocol/package/currency/time ID.
Further starts within 60 minutes of the previous episode are suppressed. A
separate 60-minute-spaced control series samples otherwise eligible transitions
that do not meet the trigger. Controls and episodes may overlap in time, especially
across S/USDT variants; do not count these as independent mining experiments.

Episode observation ends when divergence from the original baseline falls to
2% or less, or after a 30-minute window. The report distinguishes ticket repricing,
market reversal, both, and unknown causes. A market reversal alone is NOT successful
ticket catch-up. Gaps/invalid rows are censored, not interpolated or labeled success.

Labels use the first actual same-series quote at or after +15 and +30 minutes,
within 5 minutes of each target, with no invalid intervening points or long gaps.
Record changes in work/native cost, ticket cost/work and market price separately.
Pending and missing labels remain distinct. No hypothetical quote is assigned a
HIT, MISS, payout, refund, or realized ROI. Entry features cannot change when
future labels arrive. Previously observed labels survive archive roll-off; a
conflicting revision is flagged and excluded from evaluation.

`firstRecordedAt` is the real research execution time. `entry.availableAt` is
when the source quote was recorded, not when a phone notification was delivered.
This is time-separated replay, not live execution performance.

## Forward evaluation

All 19 September 2026 data are exploratory. The untouched evaluation period begins
20 September 2026 at 00:00 UTC (02:00 Europe/Ljubljana). The protocol is committed
before that cutoff. Until then `walkForward.status=NOT_STARTED` is correct.

For each test UTC day, package and horizon, compute a reference from the previous
7 days' control labels that were already available before a 60-minute embargo.
Then summarize the day's episode and control quote changes separately. Never train
on a label that arrived after the reference cutoff. No threshold is fitted and no
score is promoted automatically. Small or missing groups remain insufficient.

The folds are descriptive. They do not yet establish incremental value above
EV-only ranking, matched regimes, price shocks, fees, or actual notification-to-buy
latency. Formal promotion would require preregistered matched-regime comparison,
clustered uncertainty by market episode/day (not per package copy), sufficient
held-out periods, and separate authorized order-level calibration. `NO_VERIFIED_EDGE`
remains the only verdict even when a few episodes later reprice as predicted.

## Phone notifications: separate delivery path

The user's existing ChatGPT watch remains an hourly condition watch. The associated
instructions have been updated separately: concise PRIMARY/SECONDARY display,
S and USDT 5/20 primary, M/larger USDT secondary up to 100 EUR; actual feed-age check;
no stale/future actionable messages; no repetition from identical observations;
explicit MATH warning; no promotion of research episodes or BATCH into purchases.
This does not change CURRENT and does not enable automatic buys or cancellations.

GitHub notices are annotations, not proof of phone delivery. ChatGPT task push
requires the user's notification preferences and device permission. Changing a
task prompt does not enable push permission. The hourly watch cannot provide a
five-minute delivery guarantee. No new external push service, token or account
has been configured by this change.

Official task-notification guidance:
https://help.openai.com/en/articles/10291617-chatgpt-tasks

## Reproduction

`python3 -m unittest discover -s tests -p 'test_market*.py' -v`

`python3 scripts/build_lag_episodes.py --now <aware ISO time>`

The CLI fingerprints sources, prevents outputs from overwriting inputs, and keeps
first-recorded entry details immutable. Only its own ledger may be updated in
place. Tests are synthetic and offline, not evidence of profitability.
