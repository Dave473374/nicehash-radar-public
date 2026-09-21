# On-chain HIT verification (research only)

Purpose: independently verify public EasyMining **success events** without
changing CURRENT 2.9.0, BATCH, alerts, or any order behavior.

## Package / chain coverage

- Gold → BTC
- Silver → BCH
- Bronze → ZEC
- Titanium → KAS
- Palladium → LTC + DOGE merged mining

Palladium is represented per chain. A public LTC success event is verified
against Litecoin and a public DOGE success event is verified against Dogecoin.
A match on one chain is explicitly **not** treated as proof that the paired chain
also produced a reward.

## EasyMining attribution is separate from NiceHash pool attribution

A Bitcoin or Bitcoin Cash coinbase tag such as `/NiceHash/`, `/NiceHashMining/`
or `/NiceHashSolo/` proves only NiceHash pool/miner evidence. It does **not**
by itself prove that the block came from EasyMining.

The verifier records an explicit product-level classification:

- `EASYMINING_CONFIRMED`: the block identity comes from the public NiceHash
  EasyMining `singleReward` source and includes package identity.
- `NON_EASY_CONFIRMED`: an explicit source classifies the reward as a
  non-EasyMining order. A NiceHash coinbase tag does not override this.
- `NICEHASH_UNKNOWN`: NiceHash pool/miner evidence exists on-chain, but no
  product-level EasyMining/non-Easy source is available.
- `UNKNOWN`: neither product attribution nor NiceHash pool attribution is
  established.
- `CONFLICTING_EVIDENCE`: explicit EasyMining and explicit non-Easy source
  evidence conflict; the verifier fails closed instead of choosing one.

Concrete regression examples from 21 Sep 2026 keep this separation locked:
BTC block 967915 is classified EasyMining only because it is present in the
public EasyMining success source with Gold L package identity; BTC block 967930
is a non-Easy example when supplied with explicit `NON_EASY_ORDER` evidence.
Both may carry ordinary NiceHash pool evidence, which is a separate fact.

Known payout addresses are not hard-coded as the sole classifier. They may be
useful supporting research evidence, but addresses can rotate or be reused.
Product attribution requires an explicit source classification.

## Evidence layers

1. The existing allowlisted public NiceHash collector
   (`scripts/collect_realized_blocks.py`) reads the public
   `singleReward` endpoint and stores success events. It now supports a deeper
   pagination window with redirect refusal and bounded responses.
2. `scripts/backfill_public_easymining_hits.py` is local-only. It normalizes
   that archive for research and performs no HTTP requests.
3. Independent chain evidence:
   - BTC: mempool.space
   - BCH: bchexplorer.cash primary; Blockchair Bitcoin Cash dashboard first fallback; explorer.bch.ninja public JSON block-by-height API as a second fallback
   - ZEC: Blockchair Zcash dashboard API
   - KAS: official Kaspa Explorer REST API (`api.kaspa.org`)
   - Palladium LTC/DOGE: Blockchair Litecoin/Dogecoin block dashboards, with
     each event scoped to the chain named by the NiceHash public success record;
     if Blockchair is unavailable, a keyless read-only ORDnet height/hash lookup
     is used as a strict hash-only fallback

BTC and BCH on the primary mempool-style path additionally inspect the coinbase `scriptsig` and keep
`/NiceHash/`, `/NiceHashMining/`, and `/NiceHashSolo/` distinct. A block
with another NiceHash tag is not silently relabeled as an EasyMining-specific
tag.

For ZEC phase 1, independent verification is block-height/hash plus explorer
metadata (timestamp/difficulty/reward/miner label where available). For KAS,
the official API verifies the exact block hash and records timestamp,
difficulty, blue/DAA score and miner info when available.

For Palladium, the ledger stores the merged-mining family, the event's chain,
and whether that chain is the Litecoin parent Scrypt chain or Dogecoin AuxPoW
child chain. The field `pairedChainEvidenceClaimed` remains false: verifying a
DOGE block never manufactures an LTC HIT, and verifying an LTC block never
manufactures a DOGE HIT. Public `singleReward.payoutReward` values for LTC and
DOGE are normalized from 1e-8 native units before any payout/reward comparison.

The Blockchair path may also record timestamp, difficulty, reward and miner label
when present. The ORDnet fallback intentionally claims less: it confirms only
the requested height/hash (plus non-critical metadata if present), records no
coinbase reward, and never upgrades the paired chain. A real Blockchair hash
conflict is returned directly and is never hidden by fallback.

Previously stored Palladium events with status `UNSUPPORTED_COIN` are eligible
for one-time re-verification now that LTC/DOGE are supported. Existing verified
records are not reprocessed unnecessarily.


For UTXO-chain public `singleReward` rows, raw `payoutReward` values are
normalized from 1e-8 native units before computing
`payoutToCoinbasePercent`. This prevents a satoshi/native-unit mismatch from
turning an approximately 97% payout into a multi-billion-percent value. Existing
ledger rows are migrated locally when their source event is available; no
network re-verification is required for this schema repair.

## Critical limitation

Every record here is a **winner**. Neither a blockchain nor a public recent-block
list reveals all EasyMining tickets that failed. Therefore this dataset cannot
supply a MISS denominator, estimate ticket hit rate by itself, justify
time-since-last-block strategies, or raise a BUY signal.

The value is data integrity: confirm that public NiceHash HIT attribution points
to a real chain event and build a better timestamp/difficulty/reward history for
research.


### BCH fallback semantics

If the primary BCH explorer is unavailable, the verifier uses Blockchair only
to confirm block height/hash and collect timestamp, difficulty and block reward.
That fallback does **not** claim a NiceHash coinbase tag because the block
dashboard does not provide the same raw coinbase scriptsig evidence.

NiceHash public `singleReward.payoutReward` for BCH is stored in 1e-8 BCH
units. The fallback preserves the raw value and also writes a normalized native
BCH value before comparing it with the on-chain reward. This prevents a
303137009 raw payout from being misread as 303,137,009 BCH.

A hash conflict from the primary source is never hidden by the fallback.


### BCH second fallback

If both the primary BCH explorer and Blockchair are unavailable, the verifier
uses explorer.bch.ninja's public `/api/blocks-by-height/:height` JSON route.
It requires exactly one block object with the requested height and a 64-character
hash. Ambiguous responses fail closed. If the explorer also labels the miner as
NiceHash, that label is stored as secondary evidence; it is not treated as a raw
coinbase-script proof. A hash mismatch remains a hard conflict.
