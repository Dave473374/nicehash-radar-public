# Public Market Edge Research v1

## Boundary

This is an offline measurement layer, not a new decision engine. It reads only
`buy-feed.json`, `calibration/radar-snapshots.jsonl`, and
`calibration/public-market-history.jsonl`. It performs no HTTP requests and does
not use account credentials or change CURRENT, alert thresholds, BATCH, buys,
or cancellations. Existing public collectors are reused, not duplicated.

A separate workflow updates `research/market-edge-report.json` and
`research/market-edge-pairs.jsonl` nominally twice hourly. This research schedule
is not the quote-collection frequency or a phone notification system. Failed
research cannot stop the production BUY feed. Check `generatedAt` before using
any report; a failed update leaves the previous output in place.

## What is measured

For each package/version/currency, work = quoted hashrate times duration.
Work per native cost is W/C. Native BTC and native USDT are never interchanged.
USDT SHA256 quotes use the explicitly separate USDT market series; no fallback
to BTC or a different algorithm is allowed.

The available public market `priceRaw` is not yet sufficiently documented as an
executable all-in purchase quote. Therefore `absoluteMarketDiscountPercent`
remains null. The logarithm of `priceRaw * W/C` is used ONLY as a relative index
within an unchanged unit signature, currency, package, chain, and relay version.
An unknown fixed conversion cancels in within-series changes, but this does not
verify executable prices or prove an advantage. This index does not by itself
control for reward-price, difficulty, supply, region, fees, or delivery risk.

Comparable transitions report the separate market-price change, ticket cost
per work change, and their log divergence. No direction is called BUY. Changes
in order counts or rigs are not treated as independent mining probability.

A trailing median/percentile is descriptive only: prior 24h, strictly past
observations available by the quote time, one record per 15-minute bucket, at
least 24 buckets spanning 12h. These are coverage checks, NOT confidence levels,
statistical significance, or empirically optimized thresholds. Gaps and sample
selection remain visible and can still bias descriptive statistics.

## Time and data contract

- An aware upstream `checked_at` and local `collected_at` are mandatory.
- Observed time must not precede the quote or be in the future.
- Quotes first observed more than 15 minutes late are excluded, not backdated.
- Declared feed-generated time must agree with upstream checked time.
- Identical per-package quote timestamps are deduplicated. Conflicting quote
  contents at the same timestamp are rejected. Cosmetic annotations do not
  create a new quote.
- Select the latest market observation at or before the quote, never after it;
  its age must be <=10 minutes. An invalid latest row does not fall back to an
  older good row. Market time measures local receipt, not a verified upstream
  price timestamp.
- Numeric values must be finite; costs, speed and work must be positive.
- Only quotes explicitly marked available are compared. Units and model-version
  boundaries break comparisons. Max transition gap is 20 minutes and repeated
  use of the same market observation does not count as a new market transition.
- Retain 30 days. Historical opportunity reports are not live purchase advice.
- MATH status is carried from the source, not retroactively invented. Missing or
  NOT_APPLICABLE does not count as math cleared; PASS is consistency, not proof
  that the external input is correct.

## Forward labels

The report separately calculates retrospective 15-minute future quote changes,
using an actual future observation between +15 and +25 minutes of the same
series. Missing data stays missing. These labels are NOT features, realized
returns, HIT/MISS outcomes, or evidence that a hypothetical ticket found a block.
They are currently aggregated descriptively and are not used to choose rules.

## Validation required before any promotion

1. Independently resolve priceRaw units, currency, fees, region, executable
   capacity and quote lifetime. Do not infer conversion merely by finding a
   multiplier that makes prices look plausible.
2. Resolve material difficulty-versus-hashrate inconsistencies in the underlying
   math. This module does not solve that issue.
3. Pre-register ONE lag hypothesis and a simple baseline; do not optimize many
   lags/thresholds on this small history. Use contiguous time splits, embargo
   overlapping quote/ticket horizons, and compare against the existing EV model.
4. Measure actual source-to-user-to-purchase latency separately. Current GitHub
   notices and hourly ChatGPT tasks are not an established immediate push path.
5. Validate incremental quote value out-of-sample, then separately calibrate on
   authorized order-level outcomes when available. Public rewards cannot supply
   a global MISS denominator. Do not buy tickets just to populate this dataset.
6. Use clustered uncertainty by date/market episode and matched package/price
   exposure. Report losses, costs and uncertainty, not just the number of HITs.

Until those steps succeed every package remains `NO_VERIFIED_EDGE`; no fitted
predictor or significance claim is produced. Preserve S plus USDT 5/20 as PRIMARY,
M plus larger USDT as SECONDARY; the <=100 EUR flag is informational, not a new
permission to buy or an alteration to the existing alert engine.

## Tests and reproduction

Run `python3 -m unittest discover -s tests -p test_market_edge_research.py -v`.
The tests are synthetic and offline; they validate implementation boundaries,
not profitability. `--now` permits a reproducible aware UTC cutoff.
Source hashes in the report identify the input files and are checked again after
analysis. Output paths may not alias any source path.
