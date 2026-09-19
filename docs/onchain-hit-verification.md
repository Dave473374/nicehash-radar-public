# On-chain HIT verification (research only)

Purpose: independently verify public EasyMining **success events** against the
blockchain without changing CURRENT, BATCH, alerts, or any order behavior.

## Evidence layers

1. `NICEHASH_PUBLIC_SINGLE_REWARD` supplies the public EasyMining package/event
   attribution: coin, package, block height/hash and payout.
2. An independent block explorer verifies the block and coinbase data:
   - BTC: `mempool.space`
   - BCH: `bchexplorer.cash`
3. The verifier records the exact coinbase tag class. `/NiceHash/`,
   `/NiceHashMining/` and `/NiceHashSolo/` are kept distinct rather than treated
   as interchangeable.

The BCH explorer is a mempool-style open-source explorer and exposes the same
read-only block/transaction API shape used here. Its pool matching code also
parses the coinbase `scriptsig`, which is the field this audit reads.

## Historical backfill

`scripts/backfill_public_easymining_hits.py` walks the public NiceHash
`/hashpower/api/v2/public/solo/singleReward` pagination without credentials and
builds `research/public-easymining-hit-history.jsonl`. Pagination stops on an
empty or repeated page and refuses silent identity rewrites.

The backfill is deliberately separate from `calibration/realized-blocks.jsonl`:
it is a deeper research archive, not a production input.

## Critical limitation

This is **winner/numerator evidence only**. The blockchain cannot reveal all
EasyMining tickets that failed. Therefore neither the backfill nor on-chain
verification can supply a MISS denominator, estimate a user's hit rate by
itself, justify gambler's-fallacy timing, or raise a BUY signal.

## Other trackers

`wenblockniceha.sh` currently exposes a public NiceHash block view and historical
attribution, but the service is deprecated and third-party. It is useful as a
secondary manual cross-check, not as an authoritative ingestion source. BTC
NiceHash pool history on mempool.space is also useful context, but its NiceHash
pool classification includes multiple NiceHash coinbase tags; it must not be
assumed to mean EasyMining without tag/package evidence.
