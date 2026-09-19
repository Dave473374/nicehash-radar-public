# On-chain HIT verification

This layer verifies public NiceHash EasyMining HIT records against public blockchain explorer data. It is research-only and never changes CURRENT, BATCH, alerts, orders or the user's account state.

## Source and scope

Input comes from `calibration/realized-blocks.jsonl`, which is populated from the public NiceHash `singleReward` recent-blocks endpoint. This is a public successful-block stream. It is not the user's private order history and it does not contain misses.

Supported coins in v1 are BTC, BCH, LTC, DOGE and ZEC. The verifier uses Blockchair block dashboards by height/hash-compatible chain names. For BCH records it also stores a human Blockchain.com explorer URL because that explorer visibly shows examples such as `Coinbase Message: /NiceHash/`.

## What is verified

For each new public NiceHash block record, the verifier checks whether the referenced block exists on-chain and whether height/hash agree when both sides provide a hash. It stores block time, difficulty, transaction count, first transaction hash when provided, and block reward when the explorer exposes it.

If a native NiceHash payout and an on-chain block reward are both available, the report calculates `payoutVsBlockRewardPercent`. This can help audit the relationship between public NiceHash payout and full block reward. It is not a profitability model.

Coinbase tags are optional evidence. If the explorer block payload exposes a field containing `NiceHash`, the verifier records it as observed. If the field is absent, the verifier records `NOT_AVAILABLE_IN_BLOCK_DASHBOARD`; absence from the API payload is not proof that the block was not mined by NiceHash.

## What is not proven

A public NiceHash HIT is not automatically the user's HIT. Personal HIT/MISS evidence still requires the user's completed-order history or the user's screenshot/result. Public successful blocks also cannot supply a global MISS denominator.

This layer must not be used to infer that a particular time, pool tag, block gap, or chain state causes the next block to be more likely. Without the set of all tickets that failed, public HIT-only data has survivor bias.

## Outputs

- `calibration/onchain-block-verifications.jsonl`
- `research/onchain-verification-report.json`

Both outputs explicitly state:

- `canSupplyMissDenominator=false`
- `canRaiseBuySignal=false`
- `automaticPurchase=false`
- `privateApiUsed=false`
- `adminApiUsed=false`

## Operation

The workflow runs offline tests on pull requests and uses public explorer calls only in the publish job on `main`, schedule or manual dispatch. No NiceHash API key, login cookie or Admin access is used. Existing evidence is preserved; conflicting verification rows are recorded separately rather than overwriting earlier evidence.
