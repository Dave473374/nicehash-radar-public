# Input reconciliation - 19 September 2026

## Results and boundaries

This change extends the existing math control and public research workflow. It
is not another BUY model. CURRENT fields, the 5%/15% disagreement thresholds,
alert thresholds, priorities and purchase/cancel policy remain unchanged.
Existing WARNING/CRITICAL BATCH restrictions remain. Invalid/nonfinite math
inputs now produce UNKNOWN, not a misleading finite result.

### Mechanism of the mathematical disagreement

Let W=h*t, K=2^32, network estimate H, reported difficulty D, target interval T.
Then lambda_H=W/(H*T), lambda_D=W/(D*K), so their ratio is (D*K/H)/T.
The diagnostic calculates this identity and separately checks whether the
supplied expected-block count actually matches lambda_H (0.01% numerical
reconciliation tolerance, not a new economic threshold).

For the pinned public feed commit d7f8a236861b3dfee5822293acab9d1f3d48231a:
LTC D=88389132.60279, H=2888325039005800 and T=150 imply 131.4355 seconds
and a -12.3763% lambda_H/lambda_D difference. DOGE D=44255305.03469,
H=2531918833764449 and T=60 imply 75.0716 seconds and +25.1193%.
These are algebraic implied intervals, NOT observations or forecasts of the next
block. Their reproduction explains the formula disagreement, not the provenance
or correctness of NiceHash's underlying difficulty and network estimate.

Bitcoin Core estimates network hashrate from historical chainwork/time (default
120 blocks), not from a live census of mining machines. Combining a variable
historical estimate with a fixed target interval need not equal difficulty-based
work. At fixed own work and difficulty, merely lowering the estimated other
miners' hashrate does not increase success probability per own hash.

The conventional bdiff reference target is 0xffff shifted left 208 bits. Its
more precise work factor differs from 2^32 by about 0.001526%, far too small to
explain the observed double-digit discrepancies. This refinement is diagnostic
only; the existing approximation is retained. It is scoped to BTC/BCH with
SHA256 and LTC/DOGE with Scrypt. No 2^32 conversion is asserted for KAS or ZEC.
Even for scoped chains, the meaning and freshness of upstream fields still need
independent verification. A MATH PASS is consistency, not verified truth.

`research/input-diagnostics.json` contains conditional difficulty-based EV at
same-snapshot prices, only when every rewarded chain has supported math and
fresh price inputs. It uses the existing reward field without adding another
fee deduction. It is NOT realized ROI, P(profit), a lower confidence bound, or a
recommendation. Partial merged-mining EV is never mislabeled complete.
Historical summaries deduplicate chain timestamps across S/M packages and
exclude conflicting, future or late observations. They are not HIT/MISS tests.

### Market units: confirmed and still unknown

The official public mining-algorithm registry explicitly distinguishes:

| Algorithm | Speed display / factor | Price display / factor | Market |
|---|---|---|---|
| SCRYPT | TH / 10^12 | TH / 10^12 | BTC |
| SHA256ASICBOOST | EH / 10^18 | EH / 10^18 | BTC |
| SHA256ASICBOOST_USDT | EH / 10^18 | EH / 10^18 | USDT |
| EQUIHASH | GSol / 10^9 | GSol / 10^9 | BTC |
| KHEAVYHASH | PH / 10^15 | EH / 10^18 | BTC |

The existing aggregate collector now also reads that documented public metadata
endpoint, saving only an allowlisted unit/currency projection. It does not read
orders or user information. Metadata failure leaves the existing raw collection
available but normalization explicitly unverified. Conflicting currency,
algorithm or speed-unit contracts are rejected. All three GET paths are fixed;
redirects are blocked and response size is bounded. No key, private API or
account access is used. The normal collection schedule is unchanged.

Critically, priceScale=8 is NOT accepted as proof that raw `p` is denominated in
10^-8 units. A price display factor is NOT automatically the speed display
factor (KAS differs by 1000). The raw statistic's native currency scaling,
time denominator, averaging and executable all-in buyer price remain unverified.
Therefore rawPriceDenominationVerified=false, executableMarketPriceVerified=false
and absoluteDiscountPercent=null remain enforced.

The diagnostic separately tests p*s approximately equals v. If the identity
holds, volume is algebraically redundant with price and speed; it cannot be
counted as a third independent confirmation. This identity cannot establish
whether the base money unit is BTC, satoshi, USDT, or anything else.

## Evidence sources consulted

- Bitcoin mining guide: https://developer.bitcoin.org/devguide/mining.html
- Network estimator RPC: https://developer.bitcoin.org/reference/rpc/getnetworkhashps.html
- Estimator source: https://github.com/bitcoin/bitcoin/blob/master/src/rpc/mining.cpp (GetNetworkHashPS)
- Difficulty definition: https://github.com/litecoin-project/litecoin/blob/master/src/rpc/blockchain.cpp (GetDifficulty)
- Difficulty definition: https://github.com/dogecoin/dogecoin/blob/master/src/rpc/blockchain.cpp (GetDifficulty)
- NiceHash public-client definitions: https://github.com/nicehash/rest-clients-demo/blob/master/python/nicehash.py
- Official API documentation: https://www.nicehash.com/docs/
- Public metadata paths: `/main/api/v2/mining/algorithms/` and `/main/api/v2/public/buy/info/`.

Registry values above were observed on 2026-09-19, not assumed to be permanent.
New snapshots retain metadata at local collection time; older snapshots are not
retroactively assigned current metadata. No server-internal pricing algorithm
or execution/refund guarantee is inferred from these public sources.

## Tests and operation

`python3 -m unittest discover -s tests -p 'test_market*.py' -v` includes 32 existing
research tests plus 30 new offline tests. The same CI also runs the existing 22
Phase-0 scenarios and safety checks. Synthetic fixtures validate implementation,
not profitability. Research publishes the new diagnostic JSON alongside the two
existing reports, without modifying the source files. The collector's actual
new metadata availability must be verified on a successful scheduled run;
offline tests alone are not evidence of a live response.
