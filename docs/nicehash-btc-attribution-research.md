# NiceHash BTC attribution research

Purpose: continuously separate **NiceHash pool blocks** from **confirmed
EasyMining blocks** without changing BUY Radar decisions.

## Public sources

1. **mempool.space NiceHash mining-pool history**
   - public GET only;
   - identifies BTC blocks attributed to the NiceHash mining pool;
   - does not identify which NiceHash product created the work.

2. **NiceHash public EasyMining `singleReward`**
   - primary public product-level EasyMining success source already archived by
     this project;
   - contains package identity and block identity for exposed EasyMining hits.

Pool attribution and product attribution are deliberately separate.

## Collection

`scripts/collect_mempool_nicehash_btc_blocks.py` stores:

- BTC block height and hash;
- timestamp and public reward/fee context when present;
- public coinbase output addresses and values from mempool.space;
- explicit safety flags showing the data cannot raise a BUY signal or supply a
  MISS denominator.

Output:

`research/mempool-nicehash-btc-blocks.jsonl`

Coinbase payout addresses are **context only**. No address is hard-coded as an
EasyMining or NON-EasyMining classifier. Addresses can rotate, be reused, or
serve multiple operational purposes.

## Product attribution

`scripts/classify_nicehash_btc_blocks.py` compares the NiceHash pool inventory
with:

`research/public-easymining-hit-history.jsonl`

Classification rules:

- **EASYMINING_CONFIRMED** — exact BTC `blockHeight + blockHash` match exists
  in the public NiceHash `singleReward` archive.
- **NICEHASH_UNKNOWN** — mempool.space attributes the block to NiceHash, but no
  exact EasyMining product-source match is available.
- **CONFLICTING_EVIDENCE** — source evidence disagrees at the same block height
  or exact identity.
- **NON_EASY_CONFIRMED** — reserved for explicit product-level NON-EasyMining
  evidence. It is never inferred merely because a block is absent from
  `singleReward`.

Outputs:

- `research/nicehash-btc-attribution.jsonl`
- `research/nicehash-btc-attribution-report.json`

The report also counts public coinbase output addresses by attribution class.
This lets the project test address patterns empirically without turning an
address into a classification rule.

## 21 Sep 2026 regression examples

The unit suite locks the distinction illustrated by two BTC blocks:

- block **967915** is EasyMining-confirmed when its exact block identity is
  present in `singleReward` with Gold L package identity;
- block **967930** remains `NICEHASH_UNKNOWN` in the public-only classifier
  unless an explicit NON-EasyMining product source is supplied.

This is intentional. A screenshot or other explicit evidence may establish a
NON-Easy event for investigation, but the public scheduled collector must not
convert source absence into a false positive.

## Safety

This research:

- uses no account credentials;
- uses no Admin API;
- uses no private NiceHash API;
- never creates or changes orders;
- never changes CURRENT, final_signal, BUY thresholds, alerts or package math;
- never supplies a MISS denominator;
- cannot raise a BUY signal.

The scheduled workflow runs every six hours with the existing public on-chain
research job and persists only research outputs.
