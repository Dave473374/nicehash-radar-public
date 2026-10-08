# BUY Radar scheduled collection recovery — 8 October 2026

Scope: the existing nicehash-easymining-relay and Dave473374/nicehash-radar-public only. No new radar, no VMM/Kraken changes, no real orders, no private NiceHash API or Admin access. Backup branch: backup/buy-radar-20261008-before-scheduler at 78b8989d30d2dad63f3a087a21c03c5cb3982598.

## Why this repair

The post-deployment observation on 8 October at 19:45 UTC restored 102 hourly observations and numeric Q24/Q7, but only four or five hourly package samples were in the last 24 hours. Non-null Q24/Q7 does not imply a mature baseline. Recovery.1 still collected as a side effect of an HTTP request; it had no scheduled handler. The latest reviewed publisher required BUY FEED OK before its health annotation step, so an honest Worker WARNING could prevent publishing the actual diagnostic state.

## One producer, read-only consumers

Deploy workers/recovery/worker-scheduled.js into the SAME existing Cloudflare Worker, preserving RADAR_HISTORY, and configure exactly one Cron Trigger: `*/5 * * * *`. The Cloudflare scheduled handler, not GitHub timing and not the browser, acquires quotes, invokes the unchanged CURRENT engine, saves an hourly historical snapshot, and writes one latest result at runtime:buy-feed:scheduled. The existing /buy-feed URL serves that latest result; HTTP requests no longer write history or trigger upstream acquisition. /buy-feed-shadow stays a live no-KV research endpoint. /health is a one-read diagnostic endpoint.

The code alone does not install the Cron Trigger. Before the first actual Cron invocation, /buy-feed reports NOT_STARTED with no invented checked_at. An old cached result is rejected if older than seven minutes or from another source revision. checked_at remains the original observation time; served_at is explicitly separate. The runtime record and history use eight-day retention. Legacy snapshot keys are neither deleted nor reset.

The CURRENT profitability, quality, Poisson and break-even functions and their thresholds are retained from the reviewed recovery.1 build (SHA256 d151375be73e45b51294dca1706a5b88dcffb70952762fe295cbe32f87d5c1f2). A JavaScript port of the existing Python math-consistency v2 audit attaches the same per-package advisory checks on the exact acquired snapshot. It never overrides the model. Differential tests compare its output with the original Python implementation; numerical rounding is compared with tolerance. Calibrated/EDGE research are not promoted.

## Publisher and alerts

The existing GitHub publisher remains a best-effort mirror, not the authoritative scheduler. It can now publish explicitly typed non-actionable warnings, including WARMING_UP, NOT_STARTED and stale diagnostic records, without pretending the quote time advanced. Unknown/malformed input and future timestamps remain rejected. BUY processing retains the seven-minute age gate and the original 18-sample/18-hour 24-hour readiness threshold. Seven-day readiness thresholds are not relaxed.

The existing alert workflow checks decision readiness before invoking its unchanged alert builder, notice renderer and state writer. It records a clear health summary and skips all three stages while data is unready. Dedupe/history files are not reset. This is not proof of phone push delivery; ChatGPT hourly polling, transport accessibility and notification permissions remain separate acceptance checks.

## Resource budget and limitations

For 288 successful five-minute invocations/day, normal producer writes are 576/day (one history put plus one runtime put per invocation). This arithmetic excludes duplicate invocations and other workloads. Public /buy-feed and /health calls perform one KV get, no list or write. Legacy pagination can still temporarily increase collector list/read costs; actual plan-wide usage and CPU/latency must be observed after deployment. No purchased service or plan upgrade is assumed.

Same-isolate overlap is prevented and already visible duplicate/older ticks are skipped. KV is eventually consistent, not an atomic distributed lock. Exactly-once execution is explicitly not guaranteed. No real trading action depends on acquiring such a lock; UTC-hour bucketing prevents duplicate snapshots becoming independent historical hours.

Cloudflare documents scheduled handlers and dashboard configuration at https://developers.cloudflare.com/workers/configuration/cron-triggers/ . Trigger changes can take up to 15 minutes to propagate. KV consistency: https://developers.cloudflare.com/kv/concepts/how-kv-works/ . Quotas: https://developers.cloudflare.com/kv/platform/limits/ . GitHub best-effort schedules: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule .

## Tests and deployment boundary

Run build_scheduled.py, node --test workers/recovery/test_scheduled.mjs, remove the generated test-only worker-scheduled.mjs, then run all Python regression tests and existing access guards. The dedicated CI captures actual logs. Twenty scheduler tests include a SYNTHETIC 24-hour, 288-tick replay: it is not 24 hours of live evidence. Thirteen transport tests cover diagnostic mirroring and false-BUY suppression, alongside the updated twelve publisher tests and prior repository tests.

The deterministic build manifest records input/output hashes and a content-derived source revision. It is generated without embedding each CI run SHA, so rebuilding does not rewrite the deployment source unnecessarily. Original recovery.1 source remains available for rollback. CI commits only the tested generated module and manifest on the repair branch; it never deploys Cloudflare or configures a trigger.

Owner deployment: Edit code -> replace existing worker.js contents with worker-scheduled.js -> Deploy; then existing Worker Settings -> Triggers -> Cron Triggers -> add `*/5 * * * *`. Retain RADAR_HISTORY and the previous deployment. Do not create another Worker or another monitor. Rollback must disable the new Cron Trigger before restoring recovery.1, because that old version has no scheduled handler.

Acceptance still requires actual Cron receipts with increasing started_at, a fresh /buy-feed without visiting it to create observations, a sustained healthy collection cadence, maturation of real Q24/Q7 evidence, fresh consumer transport and an actual alert delivery test. The large telemetry archive has not been migrated out of Git by this patch. Do not claim the entire radar or phone delivery is fixed merely because these tests passed.
