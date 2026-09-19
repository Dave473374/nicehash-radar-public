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

This path cannot raise a BUY signal, purchase/cancel an order, alter
`final_signal`, or change the locked lag thresholds.

## Logging and storage policy

The public repository workflow may log only generic success/failure messages.
Detailed private counts, HIT/MISS outcomes, ROI, timestamps and payouts are not
printed. No private analysis artifact is uploaded.

The manual workflow removes the ephemeral private JSON files before the runner
finishes.
