# Private entry-time market context

This path extends the existing sanitized completed-order calibration with the
public market-edge context that was known at the order entry time.

It is deliberately private and ephemeral.

## Data flow

1. The existing read-only NiceHash collector fetches completed EasyMining
   orders with `active=false`.
2. Only the already-sanitized fields are written to
   `/tmp/nicehash-completed-orders.json`.
3. `match_private_orders.py` performs the existing entry-time HIT/MISS/ROI
   matching against the freshest pre-order radar snapshot.
4. `match_private_orders_market_context.py` joins those private matches to:
   - `research/market-edge-pairs.jsonl`
   - `research/lag-episodes.jsonl`
   - the locked `research/lag-protocol-v1.json`
5. All private outputs remain in runner `/tmp` and are deleted at the end of
   the manual workflow.

No private order result is committed, uploaded as an artifact, or printed to
the public Actions log.

## Entry market matching

The preferred market record is the quote from the same radar snapshot already
selected by `match_private_orders.py`. Package, primary coin and market
currency must agree. A present private merge coin must also agree.

If the exact same-snapshot market pair is unavailable, a causal prior quote may
be retained as a separately labelled fallback. It is never silently presented
as the same entry snapshot.

Any market observation that arrived after the order start is excluded.

## Real-order pre-entry windows

The private analysis also builds 15/30/60-minute pre-entry trajectories for
each completed order when enough exact-package public market history exists.

The endpoint is the latest valid paired quote already observed before the
order entry and no more than 15 minutes old. Each baseline must be from the
same exact package/series, must already be observed before entry, and must be
within 10 minutes of the requested 15/30/60-minute target.

For every matched window the private result records changes in:

- work per native cost and inverse ticket cost per work;
- public market price statistic;
- primary-chain difficulty;
- merge-chain difficulty where present;
- expected-return percentage points;
- baseline and endpoint Radar signal.

The ephemeral result then summarizes these features separately for real HIT
and real MISS orders by exact package and horizon, including a descriptive
HIT-minus-MISS median difference. This is the first layer in this project that
can use an actual completed-order MISS denominator for the same feature family.

These comparisons remain descriptive because the entries were user-selected,
not randomized, and sample sizes can be very small. They never modify the
locked lag protocol or production signal.

## Lag protocol integrity

The locked public lag protocol is not modified.

Each private order is marked separately as:

- package outside the locked protocol;
- pre-validation exploratory only; or
- post-registration eligible.

A lag episode counts as a registered live exposure only when the package is
eligible, the order occurs after the registered validation start, the feature
was available before entry, and the episode itself had been recorded before
entry.

Historical episodes discovered after an order are therefore never rewritten as
live signals.

## Interpretation

Private completed orders provide a real HIT/MISS denominator and realized ROI
when the sanitized source contains enough cost/reward data. However, user-chosen
orders are not randomized trials. Joining them to market context is descriptive
evidence and does not by itself prove incremental edge or justify changing
CURRENT.

In particular, a large HIT-versus-MISS difference in merge difficulty or any
other pre-entry feature is only a candidate pattern until it survives more
orders and time-separated validation.

This path cannot raise a BUY signal, purchase/cancel an order, alter
`final_signal`, or change the locked lag thresholds.

## Logging and storage policy

The public repository workflow may log only generic success/failure messages.
Detailed private counts, HIT/MISS outcomes, ROI, timestamps and payouts are not
printed. No private analysis artifact is uploaded.

The manual workflow removes the ephemeral private JSON files before the runner
finishes.
