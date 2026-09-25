# Scrypt public economics: enrich, do not duplicate

## Integration

`collect-public-market-history.yml` already obtains public NiceHash aggregate
prices and display-unit metadata on a nominal five-minute schedule. The existing
BUY feed already obtains public package quotes, reported chain conditions and
Kraken/CoinPaprika FX. Neither collector is replaced or duplicated.

Immediately after the existing market collection,
`enrich_scrypt_market_history.py` reads the already-saved `buy-feed.json` and
adds `scryptEconomics` only to the newest row of
`calibration/public-market-history.jsonl`. It also writes
`research/scrypt-economics-latest.json`. It makes **zero network requests**, uses
no account credentials, and never reads order details. An enrichment failure
cannot prevent the base public market snapshot from being persisted.

There is no change to CURRENT, BATCH, existing alerts, thresholds or purchases.
Public five-minute collection already existed; this change does not claim it
started here. The existing history retention remains 30 days.

## Three different prices

1. **Public aggregate `priceRaw`**: retained unchanged. Registry display metadata
   alone is not proof of the denomination/fees or an executable all-in offer.
   `verifiedMarketPremiumPercent` is therefore always null. No ad hoc multiplier
   is fitted to make a historical Admin order price appear to agree.
2. **Quoted package cost per work**: native BTC package price divided by quoted
   TH-days. This is an implied package rate, NOT a market order quote or actual
   delivered-work measurement. S and M remain separate (S PRIMARY, M SECONDARY).
3. **Conditional chain value per TH-day**: the existing difficulty helper,
   `1e12 * 86400 / (difficulty * 2^32)`, times the exact upstream `block_reward`
   field and same-feed coin/BTC FX, added for LTC and DOGE. Linearity of expected
   value does not require treating the two merged chains as independent HITs.

`conditionalQuotePremiumPercent = 100 * (quotedRate / conditionalValue - 1)`.
`conditionalExpectedReturnPercent = 100 * conditionalValue / quotedRate`.
A 25% premium therefore implies an 80% conditional return, NOT a -25% ROI.
A smaller positive premium does not by itself mean positive EV.

The module REUSES `apply_math_consistency_shadow` for the difficulty calculation
and discrepancy checks. It preserves warnings/critical disagreements and never
calls a PASS check independent validation. All numeric economic outputs are
explicitly conditional. Upstream difficulty convention, component timestamps,
reward fee basis and future delivered work remain unverified. No additional 3%
pool fee is applied to a potentially net upstream reward; verified net return
remains null. Expected value uses expected block counts, not probability of at
least one block multiplied by a single reward.

This is **not** a reconstruction of the Admin PRICE table's 24h calculation.
Its exact data provider/formula and rolling reward/hashrate series have not been
verified. Do not compare a 24 September rental quote with 25 September fair
value and label the result an actual historical premium. Screenshots and daily
reward totals are not order-level HIT/MISS labels or a verified causal backtest.

## Time, source and failure contracts

- Market receipt must be <=120 seconds old when enrichment runs. Only the row
  just collected is eligible; historical rows are left unchanged.
- Feed `checked_at` must be aware, no later than that market receipt, and at most
  420 seconds earlier. No older favourable fallback is allowed.
- All three FX inputs (BTC/LTC/DOGE) must declare fresh finite positive values;
  their reported age PLUS age of the saved quote must be <=420 seconds.
- A receipt time is not independent proof that upstream chain data was refreshed.
- Both Scrypt chains, an available native-BTC package and matching native/BTC cost
  fields are mandatory. Duplicate package names, missing data, non-finite values,
  unhealthy feeds and future/stale inputs produce UNAVAILABLE, not an invented 0.
- Only whitelisted aggregate public fields are copied. Quote fingerprints allow
  downstream consumers to deduplicate repeated source observations; a repeated
  quote is not an independent mining trial. Do not compare across relay versions.
- `cadence` reports actual timestamp gaps over the last 24h. GitHub Actions may
  delay/drop scheduled jobs. A five-minute cron does not establish one-minute
  coverage or a real-time notification/purchase path.
- `NO_VERIFIED_EDGE` and `canRaiseSignal: false` are unconditional, including
  when a hypothetical conditional return exceeds 100%.

## Verification

`python3 -m unittest discover -s tests -p test_scrypt_market_enrichment.py -v`

The fixtures are synthetic. These tests verify arithmetic and safety contracts,
not profitability. The workflow also has a non-blocking public-source smoke
step; inspect its logs separately from the offline-test result. The main-branch
push trigger runs the existing collection after this integration is merged.
Check `generatedAt` and status before relying on any saved report.

## Primary references

- Existing project boundary: `docs/market-edge-research.md` and
  `docs/lag-evidence-integrity.md`.
- NiceHash public API client:
  https://github.com/nicehash/rest-clients-demo/blob/master/python/nicehash.py
- NiceHash fee description (read 2026-09-25; does not prove the relay field's fee basis):
  https://www.nicehash.com/support/general-help/service-fees/other-fees
- GitHub schedule limitations:
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
