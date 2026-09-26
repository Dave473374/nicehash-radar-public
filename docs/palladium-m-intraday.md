# Palladium M intraday audit (retrospective only)

## Purpose and existing components

This extends PR #61's daily quote comparison. It reuses `git_commit_index` and
`load_feed_at_commit` from `match_private_orders_git_feed_context.py`, but never
calls that module's order reader, matching functions or main. Those two helpers
read only local Git history of the public `buy-feed.json`. It reuses the public
market module's explicit/legacy BTC currency contract and the existing math
consistency helper. No second collector, private request or Admin request is
introduced.

`palladium_m_intraday.py` inspects every reachable BUY-feed revision in the
requested local-calendar windows, including 2h before and 1h after each day for
baseline/forward context. This is not a first-100 GitHub search sample. A shallow
checkout is rejected. Rejected feeds and missing intervals remain explicit.
Available quotes are not orders, purchases, winners or independent trials.

The one-off audit job is part of the existing market-edge workflow, but full Git
history scanning is NOT added to the twice-hourly schedule. It runs on PR,
relevant pushes or explicit manual execution. Outputs are artifacts; production
BUY data and the locked lag protocol are not modified.

## Time and correctness

- Calendar days use Europe/Ljubljana, not UTC-date search shortcuts.
- `checked_at` must be aware and at most 420 seconds older than the Git commit.
- Git committer time has whole-second resolution. A source timestamp up to one
  second later is accepted only by moving availability FORWARD to checked_at;
  bigger future discrepancies are rejected. A commit timestamp is still only a
  proxy for remote availability, not independent proof of data freshness.
- Duplicated quote timestamps are deduplicated; conflicting economics at the
  same timestamp/version are excluded. No repeat observation becomes a new trial.
- BTC/LTC/DOGE FX must be finite, positive, declared fresh, and pass combined
  source-age/receipt-age checks. Missing data never becomes an assumed zero.
- Both LTC/DOGE Scrypt chains and a single available BTC Palladium M quote are
  required. Invalid/duplicate package entries and contradictory BTC prices fail
  closed. Legacy 2.8.2 handling uses the already-frozen BTC-only schema bridge.
- Windows cannot cross relay-version, cost, duration or reward-field changes.
  No gap exceeding 20 minutes is bridged. Gaps within that bound do not establish
  unobserved minute-by-minute continuity.

## Two conditional models

Per TH-day, difficulty-based value is the sum over LTC and DOGE of:

`1e12 * 86400 / (D * 2^32) * upstream_block_reward * coinBTC`.

The hashrate/target-time comparison is:

`1e12 / reported_network_hashrate * (86400 / target_seconds) * reward * coinBTC`.

Both use reported data; neither is an independent oracle. Return is model value
divided by package quote cost per TH-day, not realized return or verified net EV.
No extra fee is deducted from a possibly already-net reward field. Math PASS is
consistency, not validation of upstream inputs. One model exceeding 100% must not
be promoted merely because the other model disagrees.

Dogecoin Core `src/pow.cpp` uses a one-block adjustment interval after height
145000 (GetNextWorkRequired). Consequently a momentarily low DOGE difficulty
cannot be assumed to last for a two-hour package. Checking sparse observations
15/30 minutes later does not identify precisely when the condition ended.

Likewise, a speed shown in an order detail multiplied by its wall-clock duration
is NOT verified integrated delivered work. Delivery requires accepted-work or
properly integrated hashrate records, not one displayed speed.

## Exploratory screen and forward labels

For description only, compare endpoints 15, 30 and 60 minutes apart; the baseline
must be at or before the target. Tolerance is 5 minutes for 15m and 10 minutes for
30/60m. The 15m screen uses >=5% difficulty-model value increase, <=1% quote cost
increase, and >=5% relative increase in conditional return. First qualifying
endpoints are retained at least 60 minutes apart to reduce overlapping examples.
That spacing does not establish independence.

These thresholds resemble the existing lag vocabulary, but the predictor here
is chain-model value and the target is M: this is NOT the frozen prospective
public-market protocol and cannot be merged into its validation counts.

At +15/+30 minutes use the first available observation no more than 5 minutes
late, without crossing a series change or large gap. Report future quote cost,
model value and model return separately. Missing labels remain missing. They are
not features, executed purchases, measured mining outcomes or a profit backtest.

The dates (7,20,23,24 September) were selected after observing rewards. There is
no held-out test or causal claim. Daily medians are observation-weighted and are
not volume-weighted, complete-time-weighted or order-outcome-weighted results.

## Reproduction

Use a full repository checkout, Python 3.12 and the exact source commit recorded
in the artifact:

```
python3 -m unittest tests.test_palladium_m_intraday -v
python3 scripts/palladium_m_intraday.py
```

Outputs: `/tmp/palladium-m-intraday-report.json` and
`/tmp/palladium-m-git-quotes.jsonl`. The report records source-code SHA256s, Git
HEAD and the candidate-commit-manifest SHA256; rows retain actual source commit
IDs. `NO_VERIFIED_EDGE`, `canRaiseSignal=false` and a null verified net return
remain unconditional.

Primary protocol source: https://github.com/dogecoin/dogecoin/blob/master/src/pow.cpp
Project source: `research/lag-protocol-v1.json` (unchanged).
