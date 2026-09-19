# Verified HIT market context

This research layer joins independently verified EasyMining success events to
public market-edge context that was already known before the block timestamp.

It is deliberately separate from CURRENT, the locked lag protocol and all
production BUY decisions.

## Inputs

- `research/onchain-hit-verification.jsonl`
- `research/market-edge-pairs.jsonl`
- `research/lag-episodes.jsonl`

No account credentials, private NiceHash API, Admin endpoint or write action is
used. The script itself performs no network requests.

## Matching rules

A verified HIT may receive market context only when:

1. the package name matches exactly;
2. the HIT coin is either the package primary coin or its merge coin;
3. both quote time and observation time are at or before the block timestamp;
4. the latest exact-package quote is no more than 15 minutes old;
5. that latest quote has a valid `PAIRED` public-market context.

Package sizes are never substituted. For example, Palladium L or Team Palladium
is not silently mapped to Palladium M or S.

For merged mining, a DOGE HIT may match a Palladium quote whose primary coin is
LTC and whose merge coin is DOGE. This does not manufacture an LTC HIT.

## Lag episode context

The report may also attach the most recent exact-package lag episode whose entry
preceded the HIT by at most 60 minutes. It records separately whether:

- the feature was available before the HIT;
- the episode had actually been recorded before the HIT;
- the HIT fell inside the episode's observed window.

These distinctions prevent late retrospective episode capture from being
misrepresented as a live signal.

## Critical interpretation limits

Every on-chain input row is a success event. There is no MISS denominator here,
so this dataset cannot estimate HIT rate, prove profitability or validate a BUY
threshold by itself.

The matched quote is reward-time context near the block. It is not the user's
ticket purchase time. True entry-time HIT/MISS and realized ROI validation
remains the role of the existing private completed-order calibration path.

A HIT that occurs near a pricing-lag episode is descriptive evidence only. It
does not prove that the lag caused the HIT or that buying the package at that
time had positive expected value.

## Outputs

- `research/verified-hit-market-context.jsonl`
- `research/verified-hit-market-context-report.json`

Both outputs remain research-only and cannot raise, lower or replace
`final_signal`.
