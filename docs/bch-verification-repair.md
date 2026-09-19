# BCH/Silver public verification repair — 2026-09-19

This repairs the existing `verify_onchain_hits.py` / `onchain-hit-research.yml`.
There is no second collector or second verification ledger. CURRENT, alert rules,
BATCH, the frozen lag protocol, and all private access settings remain unchanged.

## Observed problem

The existing ledger had four BCH events, including public Silver M block 969090,
with `SOURCE_UNAVAILABLE_OR_SCHEMA_MISMATCH` / `URLError`. This is an explorer
request failure, not an invalid block or a personal MISS. The working KAS/ZEC
results did not establish that Silver verification was working.

## Repair

BCH tries a single Blockchair Bitcoin Cash block dashboard GET first, then the
existing bchexplorer.cash adapter only for availability or schema failures.
No new host, credentials, POST, broadcast or account request is introduced.
Redirects remain refused, responses bounded, and max_new still limits event
attempts. A successful primary path uses one explorer request per block; at most
four are made if the legacy fallback completes. The six-hour schedule is unchanged.

Identity conflicts do NOT trigger provider shopping. Missing source hash/height,
non-hex hashes, fractional/bool heights, mismatching returned height/hash, and an
explicit orphan indication cannot become verified. Explorer context must report
success and the exact requested entry must be present. Verification means an
independent explorer match, NOT full-node consensus or cryptographic inclusion
verification. The NiceHash package label comes only from its public source.

Only decoded coinbase_data_hex or the explicit coinbase scriptsig can establish
a NiceHash tag; `guessed_miner` is not interchangeable with that evidence.
NiceHash, NiceHashSolo and NiceHashMining tags remain distinct. A missing tag does
not erase an otherwise matched block, but evidence strength is lower.

## Reward units and the public reference

The raw normalized NiceHash singleReward stream for BTC/BCH carries payoutReward
in atomic 1e-8 units. The BCH reference input 303137009 corresponds to the user-shown
public Recent Blocks payout 3.03137009 BCH. Unknown source provenance or a native
coin decimal string is NOT guessed from magnitude and leaves the converted payout
and ratio unknown. This unit mapping is scoped to BTC/BCH, not KAS or ZEC.

Blockchair documents `reward` as total coinbase reward INCLUDING fees in satoshis.
Only that field is used; missing reward is never replaced by subsidy/generation.
For BCH 969090 the reference is 312512380 atomic = 3.12512380 BCH. A ratio near 97%
is an observed comparison for this event, NOT proof of a universal fee schedule,
realized ticket ROI, or ownership of the winning ticket. This is another user's
public Silver M example, not the user's Silver S test.

Amounts use exact integer input checks and retain the raw value; zero payout is
not silently dropped. A payout exceeding total reward is flagged, not normalized
to a convenient ratio. The shared BTC mempool adapter gains the same identity /
explicit-coinbase checks and conservative payout conversion. Other chain adapters
and their units are not reinterpreted in this patch.

## Retry and validation

Existing unavailable records are retried under their existing event IDs. Earlier
attempt time/status/provider/error type are retained in verificationAttemptHistory;
confirmed records are not queried repeatedly. All old non-BCH records remain.
A report's verificationHealth explicitly identifies remaining source errors; a
green workflow alone is not a claim that all records or chains are verified.

Thirty synthetic offline cases cover availability fallback, no fallback on
conflict, reward units, unknown amounts, retry history and non-coinbase responses.
CI also runs prior backfill/on-chain tests and Phase 0. One bounded live smoke
uses the already archived BCH 969090 event and requires its exact hash, height,
coinbase tag and atomic reward comparison before publication. It performs no
NiceHash account requests. The live result must be checked separately from tests.

## Primary documentation

Blockchair API v2.0.80:
https://github.com/Blockchair/Blockchair.Support/blob/master/API_DOCUMENTATION_EN.md
Dashboard block endpoints (link_100); Bitcoin-like blocks table (link_102),
`coinbase_data_hex`, `reward`, `generation`; UTC timestamps (link_M04).
Personal/testing and noncommercial limits (link_M05): up to 1440 request points/day,
30/minute; errors/rate limits are not bypassed. This bounded personal research
workflow must not be scaled past provider limits without revisiting access terms.

No automatic BUY/cancel, no Admin, no private API, no MISS denominator, no model
promotion. Improving public HIT evidence does not increase an individual ticket's
probability by itself.
