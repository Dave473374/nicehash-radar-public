# BUY Radar Research Signal Register

The project keeps a persistent research promotion ledger in:

`research/research-signal-register.json`

The goal is simple: useful research must not disappear into old chats, and an
interesting correlation must not silently become a BUY rule.

## Status ladder

Every tracked idea uses one of these states:

1. **OBSERVATION** — interesting fact or pattern, not yet collected reliably.
2. **TRACKING** — evidence is being collected with defined source boundaries.
3. **CANDIDATE** — hypothesis/rule is frozen enough for prospective or held-out
   evaluation.
4. **VALIDATED** — the registered validation criteria have been met.
5. **PRODUCTION_ELIGIBLE** — shadow/baseline comparison and safety gates are
   complete; a production change may be proposed.
6. **PRODUCTION** — only after a dedicated reviewed PR and explicit human
   approval.

Status is not a score. Moving forward requires the signal's own registered
criteria; moving backward is allowed when evidence breaks.

## Mandatory safety rules

- No non-PRODUCTION signal may affect CURRENT, `final_signal`, BUY thresholds,
  automatic purchases or cancellations.
- Promotion is never automatic.
- A BUY feature must use causal information available before entry.
- Performance claims require an appropriate denominator: preferably authorized
  order-level HIT/MISS/ROI outcomes. Public HIT-only data cannot manufacture
  MISSes.
- A production proposal requires a frozen baseline comparison, safety/regression
  checks, a rollback plan and explicit human approval.
- Research data-quality findings can block promotion even when a candidate looks
  promising.

The validator `scripts/validate_research_signal_register.py` enforces these
structural invariants and checks that every linked evidence file exists.

## Review responsibility

When doing substantial BUY Radar research or changing a linked evidence source:

1. read the register;
2. inspect the current linked reports rather than trusting the stored snapshot;
3. check whether any `reviewTriggers` have fired;
4. if they have, surface that to the user and propose the registered next
   validation step;
5. do not silently change status or production logic.

The `currentEvidenceSnapshot` field is deliberately dated. It is a checkpoint,
not a live truth source. Current linked reports always win.

## Initial registered signals

### BTC NON-Easy payout-address pattern — TRACKING

The repeated BTC payout address
`bc1q85z0lxe86pkcg2fmppfnykat2375s0pnxjx9sc` is tracked as supporting
attribution evidence. One explicit 21 Sep 2026 NiceHash LOTTERY UI example
(block 967930) identified a NON easy order with this payout address. A public
mempool-space smoke sample also saw the address repeatedly among NiceHash pool
blocks that were not confirmed by the public EasyMining source.

This is **not** enough to classify a block as NON-Easy from its address alone.
The register requires multiple explicit product-level confirmations and held-out
false-positive testing before promotion.

### Public market pricing lag — TRACKING

The market-edge and pre-registered lag research remain `NO_VERIFIED_EDGE`.
Current blockers include unverified executable price semantics, insufficient
walk-forward labels, and unresolved math/input provenance issues. A change in
those linked reports is a review trigger.

### Palladium S 60-minute momentum v1 — CANDIDATE

This is already a frozen prospective candidate. Its existing registration
requires at least **30 eligible prospective orders and 3 prospective HITs**
before any performance claim. The hypothesis-origin history is excluded from
that prospective validation.

### Five-minute phone delivery path — TRACKING

The alert-latency policy currently has two resolved strict windows and requires
at least three before its registered recommendation can turn on. This is an
infrastructure decision only; it does not alter BUY logic.

### Difficulty/hashrate input reconciliation — TRACKING

This remains a data-quality blocker. Feed-vs-difficulty disagreement is
classified as model disagreement, not a verified source error. Upstream field
semantics and chain-specific conventions must be independently verified before
difficulty-derived estimates can be promoted.

### Reward-event versus winning-order semantics — TRACKING

Reward/event multiplicity is tracked as a data-quality guardrail. HIT/MISS is
always order-level: one completed order with at least one reward is one winning
order. Reward records/events are a separate intensity unit and may be numerous
for one winning order, especially on high-block-frequency chains such as
KAS/Titanium.

The project must not derive a reward count of one merely because an order is a
HIT. Missing reward multiplicity stays unavailable. Exact non-public aggregate
counts that motivated this guardrail are intentionally not persisted in the
public repository. Source-specific reward-count units still require explicit
verification before any reward-intensity analysis.

See `docs/reward-order-semantics.md`.

### Order lifecycle censoring — TRACKING

HIT/MISS calibration prefers lifecycle-verified `COMPLETED` orders. Explicit
`CANCELLED`, `EXPIRED` or other non-COMPLETED states are censored rather
than counted as MISS because they may not have received the planned exposure.

Earlier sanitized batches did not preserve lifecycle status. They remain useful
as provisional/descriptive evidence, but they cannot by themselves promote
calibration confidence. Exact non-public lifecycle counts that motivated this
guardrail are intentionally not persisted in the public repository.

See `docs/order-lifecycle-semantics.md`.

## Adding a new signal

Add a new object to the JSON register with:

- a stable `id`;
- category and current status;
- a dated evidence snapshot;
- linked evidence files;
- known blockers;
- explicit review triggers;
- promotion criteria;
- a clear next-review condition;
- `canAffectBuySignal=false` unless the item is already explicitly approved
  as PRODUCTION.

Then run:

`python3 scripts/validate_research_signal_register.py`

and the dedicated unit tests before merging.
