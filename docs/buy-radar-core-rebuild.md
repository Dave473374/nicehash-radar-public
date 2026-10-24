# BUY Radar core rebuild — 14 October 2026

Status: DEVELOPMENT ONLY, in the existing repository. No production replacement,
Cron activation, real push, purchase, threshold loosening, history deletion, or
change to Crypto Alpha Watch / VMM is performed by this change.

## The bounded replacement

One Cloudflare scheduled handler calls one named Durable Object. That object owns
collection, validation, signal transitions and a PRIVATE persistent outbox.
It calls the existing public NiceHash/Kraken/CoinPaprika engine in-process once;
its existing history binding and mathematics are reused. The additional mining
consistency audit is also pinned. No CALIBRATED/EDGE research stage or GitHub
publication is required for the new decision/outbox path.

`policy.mjs` is the single notification policy for BOTH the JSON view and the
outbox. It preserves PRIMARY S/USDT 5/20, the EUR 100 display ceiling, 18-hour/
18-sample history readiness and the existing 90/95 EV and math guardrails.
The ceiling is NOT spending authority. Only an existing GOOD/BUY NOW/STRONG BUY
can become a candidate; there is no synthetic opportunity, automatic promotion,
threshold optimization or automatic purchase. Below 100% expected recovery is
explicitly negative EV. EV is not HIT probability. Math consistency is not proof
of upstream input truth or of a profitable timing edge. STRONG below 100% is
capped; UNKNOWN on a merged leg cannot be masked by WARNING on another leg.

Data freshness is recomputed on every read and send. Old/future/malformed/unknown
sources fail closed; one invalid package does not silently become NO BUY. Reads
cannot collect or write. Collection exceptions preserve the last genuine source
time and separately expose the failed attempt. No fetched source is an instruction.

## Persistence and delivery contract

The new `RADAR_CORE` Durable Object binding is REQUIRED for any activation. It is
not Workers KV locking: all writes are serialized in the named object and feed/
state are saved together. KV `RADAR_HISTORY` stays with the existing engine.
A tick is recorded before collection; a crash can lose that tick until the next
one. This deliberately does not claim exactly-once collection or delivery.

An event is durably queued before transport. The optional PRIVATE service binding
`RADAR_NOTIFY` must implement POST `/notify` and idempotency by `event_id` / the
`Idempotency-Key` header. It receives only a generated Slovenian public-market
message, event type, and expiry — never exchange keys or portfolio details.
A successful receipt must explicitly contain:

```json
{"accepted": true, "event_id": "EXACT_SENT_ID", "receipt_id": "PROVIDER_RECEIPT"}
```

A 200 alone, timeout, invalid JSON or mismatched ID is NOT acceptance. Requests
have an 8-second timeout; at most 3 attempts per event and 3 requests per tick.
Retries use the SAME immutable ID and payload. The adapter must honor the expiry
and suppress duplicate delivery. An expired ambiguous attempt is not replaced
with a fresh ID until a real exit/reentry, preventing blind duplicate alerts.
Unattempted expired messages may be recreated only from a genuinely NEW fresh
observation. There is no replay of missed historical BUY messages.

`PROVIDER_ACCEPTED` is distinct from `DEVICE_RECEIVED`, which stays UNKNOWN.
No provider has been selected/configured/verified by this change. A real adapter
and device acceptance test are still REQUIRED. Do not treat a test double as push.
The outbox and provider receipts are private DO storage, never GitHub or HTTP JSON.
Three distinct failing collection ticks queue one scoped health notice, with at
most one reminder per day while the same outage persists. Recovery
is distinct; stale/duplicate evidence cannot rearm a BUY. No routine WAIT messages.
An entirely stopped Cron still requires an independent health check: the stopped
producer cannot report its own outage. No new ChatGPT task is created here.

## Build and tests

```sh
node --test workers/core/test_core.mjs
python3 workers/core/build.py
node --test workers/core/test_engine.mjs
python3 workers/recovery/build_scheduled.py
node --test workers/recovery/test_scheduled.mjs
```

No network, credentials, third-party test packages or deployment are needed by
the tests. The build refuses changed legacy engine/math hashes. Generated files
in `workers/core/dist/` are ignored; the manifest includes exact file digests and
explicit false deployment/push fields. Entry point: `workers/core/dist/worker.mjs`.
Runtime imports `../runtime.mjs` and `../policy.mjs`; deploy with a bundler retaining
these imports, not by pasting only the entry-point file into the dashboard.

## Rollout gates — NOT completed by code tests

1. Keep the current deployment and history untouched while reviewing this branch.
   Check Cloudflare runtime/bindings with the actual account. Use SQLite-backed
   Durable Object class `RadarCore`, with binding `RADAR_CORE`; retain the existing
   `RADAR_HISTORY` binding. No new paid resource or configuration is created here.
2. No parallel scheduled collector: switch the SAME existing Worker only in an
   explicit controlled cutover. `CORE_MODE` is disabled unless set to `observe`
   or `active`. No Cron is installed by this repository change. Preserve a copy
   of the old deployment/version for rollback; never delete or reset old KV data.
3. Review the NEW `BUY_RADAR_CORE_1` view schema and route transition. `/buy-feed`
   is NOT silently compatible with the old schema-12 ChatGPT reader. Migrate the
   SAME existing monitor to read health/canonical decisions, with the exact one
   chosen notification owner; do not run two BUY senders. Old public trigger-like
   routes are deliberately absent. The archive can be adapted separately.
4. Configure the authorized notification adapter, verify one clearly marked
   NONTRADE test through provider acceptance and actual phone reception. Existing
   device permissions are not re-questioned. No live BUY is manufactured as a test.
5. Observe actual source gaps, failing/recovering collection, duplicate events,
   expiry, provider errors and restarts in Cloudflare. Code CI does not establish
   uninterrupted 24-hour coverage, actual costs, platform behavior or delivery.
6. Only then enable the selected live notification path. `productionReady` remains
   false in this development implementation; a dedicated acceptance change must
   attach real operational evidence, rather than blindly toggling an env flag.

Future strategy value must be measured against a frozen same-package/cost baseline.
No verified edge or realized profit is claimed by this operational rebuild.

## Platform references

- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/durable-objects/
- https://developers.cloudflare.com/durable-objects/api/legacy-kv-storage-api/

The first stage uses the transactional KV-style API on a Durable Object, which is
also supported by SQLite-backed objects. It does not confuse that API with the
separate eventually consistent Workers KV product.
