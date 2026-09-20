# SoloBlocks BTC HIT research

Purpose: fill the BTC success-event history gap without changing CURRENT,
BATCH, alerts, package scoring, or order behavior.

## Why this source exists

The existing NiceHash public `singleReward` archive is the primary public
EasyMining success-event source used by the project. The stored archive
currently contains no BTC records, so it cannot provide a historical Gold/BTC
HIT series.

SoloBlocks.io exposes a public NiceHash EasyMining BTC block history. This is
used only as a secondary research source.

## Collection

`scripts/collect_soloblocks_btc_hits.py`:

- performs public GET requests only;
- uses the NiceHash-filtered SoloBlocks history pages;
- stores BTC block heights and source URLs;
- does not use credentials, private NiceHash APIs, or Admin APIs;
- deliberately leaves `packageName`, `packageId`, and block hash unknown;
- marks the EasyMining family as Gold/BTC but does **not** infer S/M/L.

The source file is:

`research/soloblocks-btc-hits.jsonl`

## Independent verification

The SoloBlocks records are passed to the existing
`scripts/verify_onchain_hits.py` verifier with a separate ledger and report:

- `research/soloblocks-btc-onchain-verification.jsonl`
- `research/soloblocks-btc-onchain-report.json`

For BTC the existing verifier resolves the block by height through
mempool.space, verifies the chain block, and inspects the coinbase scriptsig.
`/NiceHash/`, `/NiceHashMining/`, and `/NiceHashSolo/` remain distinct
evidence classes.

SoloBlocks attribution by itself is therefore not silently treated as an
independent on-chain verification.

## A/B/C source comparison

`scripts/compare_soloblocks_nicehash.py` compares BTC block heights:

- **A** — present in both SoloBlocks and NiceHash public `singleReward`
  history;
- **B** — present only in NiceHash public `singleReward`;
- **C** — present only in SoloBlocks.

Output:

`research/soloblocks-btc-abc-report.json`

The comparison also reports how many SoloBlocks heights have already received
independent on-chain verification.

## Critical limitations

This remains a **HIT-only** dataset.

It cannot reveal failed EasyMining tickets and therefore cannot supply a MISS
denominator, estimate a package hit rate by itself, or justify a BUY signal.

SoloBlocks also does not identify which EasyMining Gold package variant
(S/M/L) produced a BTC block. The block history must never be used to invent
package-level outcomes.

All outputs are research/audit evidence only and have:

- `canRaiseBuySignal = false`
- `canSupplyMissDenominator = false`
- `currentProductionModelChanged = false`
