# On-chain HIT verification (research only)

Purpose: independently verify public EasyMining **success events** without
changing CURRENT 2.9.0, BATCH, alerts, or any order behavior.

## Package / chain coverage

- Gold → BTC
- Silver → BCH
- Bronze → ZEC
- Titanium → KAS

Palladium (LTC/DOGE merged mining) is intentionally deferred until the verifier
can represent the two chains correctly rather than pretending one block/tag is a
complete merged-mining proof.

## Evidence layers

1. The existing allowlisted public NiceHash collector
   (`scripts/collect_realized_blocks.py`) reads the public
   `singleReward` endpoint and stores success events. It now supports a deeper
   pagination window with redirect refusal and bounded responses.
2. `scripts/backfill_public_easymining_hits.py` is local-only. It normalizes
   that archive for research and performs no HTTP requests.
3. Independent chain evidence:
   - BTC: mempool.space
   - BCH: bchexplorer.cash primary; Blockchair Bitcoin Cash dashboard first fallback; public FullStack.cash full-node hash-by-height API as a second fallback
   - ZEC: Blockchair Zcash dashboard API
   - KAS: official Kaspa Explorer REST API (`api.kaspa.org`)

BTC and BCH on the primary mempool-style path additionally inspect the coinbase `scriptsig` and keep
`/NiceHash/`, `/NiceHashMining/`, and `/NiceHashSolo/` distinct. A block
with another NiceHash tag is not silently relabeled as an EasyMining-specific
tag.

For ZEC phase 1, independent verification is block-height/hash plus explorer
metadata (timestamp/difficulty/reward/miner label where available). For KAS,
the official API verifies the exact block hash and records timestamp,
difficulty, blue/DAA score and miner info when available.

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
uses the public FullStack.cash `getBlockHash/:height` full-node endpoint.
This final fallback verifies only block height/hash. It intentionally leaves
timestamp, difficulty, block reward and coinbase-tag fields unconfirmed rather
than inventing them. A hash mismatch remains a hard conflict.
