# BUY Radar recovery — 5 October 2026

## Scope and safety

Repair of the existing Dave473374/nicehash-radar-public project. No new radar, no VMM or Kraken changes, no NiceHash Admin access, no purchases/cancellations, no private credentials accessed. Mining quality, expected-return and break-even decision thresholds are unchanged. The existing alert-state/history files are not reset. No Git history rewrite or KV deletion.

Backup: branch `backup/buy-radar-20261005-before-recovery`, commit `d46038d7c3b57dcd915b7eda858665eabcf95e96`. Recovery work: `recovery/buy-radar-20261005`.

## Verified incident evidence

GitHub Actions run 37352724861, observed 2026-10-05T18:00:52.451Z:

- `/history` returned 177 keys and `list_complete=false`.
- The newest returned key was `snapshot:1790574385179`, or 2026-09-28T05:46:25.179Z. Even that key was older than the 7-day decision window.
- `/buy-feed` simultaneously reported BUY FEED OK, HISTORY SAVED, 177 history_samples_loaded, zero hourly samples, zero coverage, and all 12 packages with null Q24/Q7 and WAIT.
- `history_samples_loaded` in the archived source actually counts listed key names, not successfully loaded observations.
- The archived v2.9.0 source calls KV list once and ignores cursor/list_complete. It therefore ignores later pages containing newer keys. Cloudflare documents ascending lexicographic key order and continuation even after an empty incomplete page: https://developers.cloudflare.com/kv/api/list-keys/ .

Evidence run: https://github.com/Dave473374/nicehash-radar-public/actions/runs/37352724861 . Public diagnostic artifacts contain the actual responses; no private account data was collected.

This is a demonstrated code defect and a strongly corroborated live failure mechanism. Exact byte equality between the Library source and currently deployed Cloudflare script has not been verified because Cloudflare deployment/export access is unavailable in this session. Do not present a successful offline replay as restoration of production KV.

The original Worker already has `decision.rationale`. The new `final_signal_reason` is a machine-readable supplement, not the first existing explanation.

## Repair implemented

`workers/recovery/build_worker.py` deterministically patches the archived source, validating original SHA256 `f150fdcca108302f84362224dec88dafa25e78ec9f868bca25165a5a2d41be57` first. Original source provenance: user Library `worker_v2.9.0_no_kv_shadow.js`, generated 29 September 2026. Output version is `2.9.0-history-recovery.1`; this is a repair revision of the same model.

The patch follows all KV pages, bounds pagination, rejects cursor loops, preserves the existing 7-day window/24-hour and 7-day readiness thresholds, and chooses one actual observation per UTC hour. It supports both existing timestamp keys and new rounded hourly keys, selecting by actual payload time. It never fabricates missing history. Invalid, missing, future or stale observations fail closed. `/history` becomes a read-only list diagnostic rather than a misleading heartbeat write test.

New writes reuse one key per UTC hour; legacy records remain readable and untouched until their original expiry. This limits KEY COUNT growth, NOT write operation count: requests still save observations. Full pagination can temporarily increase read/list operations; live quota and latency under actual load still need validation. No universal claim of quota remediation is made.

Read-analysis health and write status are separate. A successful KV put cannot hide a failed history read. Responses include production_health, history_diagnostics, final_signal_reason and source_revision. The no-KV shadow route remains no-KV and does not become production.

The publisher performs a history/decision readiness check before shadow analysis. Broken history is published as BUY FEED WARNING / ok=false / DEGRADED, and actionable labels are suppressed without resetting existing dedupe. Shadow scripts run only for ready data; they are not modified or promoted. A degraded-data publication can therefore succeed operationally without representing a healthy decision engine.

The recursive `sleep 240` / workflow_dispatch chain is removed. The existing single five-minute cron, manual trigger and safe main-branch publication remain. GitHub cron is best effort, not a guaranteed five-minute service level. Existing high-frequency JSON storage in main has NOT been fully migrated; removing recursive dispatch does not by itself solve repository growth.

## Reproducible tests

Run:

```sh
python3 workers/recovery/build_worker.py
node --test workers/recovery/test_history.mjs
python3 -m unittest discover -s tests -p 'test_recovery_health.py' -v
python3 -m unittest discover -s tests -p 'test_buy_feed_continuation.py' -v
```

28 Node test cases cover pagination (including empty incomplete pages and >1000 keys), invalid/repeated cursors and limits, timestamps/UTC bucketing, 24-hour and 7-day readiness, duplicate hourly observations, persistence/cold module restart, partial/missing/stale data, explicit write/read errors, unchanged signal combinations, no-KV shadow operation, and the complete Worker fetch route with mock public inputs. The regression reproduces zero usable hours in old code and 168 in patched code ON A SYNTHETIC FIXTURE, not in production.

12 Python health/alert test cases cover the actual existing production alert script: WAIT -> GOOD creates one event; repetition creates none; GOOD -> BUY NOW creates one; stale data neither sends nor mutates dedupe; WAIT then GOOD rearms. These are event-generation tests, not phone delivery proof.

12 pipeline tests cover freshness, future/tz errors, bounded fetching, single scheduler/no recursive dispatch, and publication of degraded health while skipping incompatible shadow stages. Total planned suite: 52 tests; consult the actual CI logs for PASS/FAIL, not this document alone.

## Existing workflow classification

Classification is an audit recommendation; no research workflow was disabled or deleted in this patch.

| Class | Existing workflow files | Treatment |
|---|---|---|
| PRODUCTION | update-buy-feed.yml; phase3-buy-radar-alerts.yml | Publisher repaired; existing event engine retained. |
| CALIBRATION/EVIDENCE | recent-blocks.yml; build-mining-events.yml; collect-calibration.yml; build_calibration-report.yml; collect-realized-blocks.yml; collect-shared-packages.yml; match-mining-events.yml; match-radar.blocks.yml; validate-buy-radar-evidence.yml; test-nicehash-private.yml | Retain records. Private workflows remain governed by existing read-only restrictions; not executed by this repair. |
| RESEARCH/SHADOW | collect-public-market-history.yml; market-edge-research.yml; onchain-hit-research.yml; export-radar-feature-window.yml; palladium-shadow-evaluation.yml; palladium-shadow-no-kv-smoke.yml; palladium-shadow-repeat-v1.yml; palladium-shadow-repeat2-v1.yml; palladium-shadow-repeat2-v1-watchdog.yml | Review active trial expiry and consumers before retiring anything; no unproved duplicate classification. Some sampling uses the no-KV route and must not be blamed for KV writes without evidence. |
| PRODUCTION SAFETY / EVIDENCE INTEGRITY | enforce-private-api-readonly.yml; forbid-admin-access.yml; phase0-correctness-regression.yml; radar-archive-integrity.yml | Preserve. |
| OBSOLETE/REDUNDANT | No entire workflow conclusively established as obsolete in this repair | The recursive publisher dispatch step was redundant and is removed; that does not justify deleting other workflows. |

The new Recovery Verification workflow is a bounded repair/test utility with no periodic schedule. It builds and tests the Worker, records source provenance on the recovery branch only, and collects one live diagnostic pair. It does not deploy Cloudflare.

## Deployment boundary and rollback

GitHub source, merge and Cloudflare deployment are separate facts. CI never deploys Cloudflare and does not have Cloudflare credentials. The generated `worker.js`, not `worker.mjs`, is the deployable module. `build-manifest.json` records its exact SHA256 and input source commit. The CI also records readable original and patched source in Git.

Required owner action: in the EXISTING `nicehash-easymining-relay` Cloudflare Worker, retain the current version for rollback, replace the code with the tested `workers/recovery/worker.js` and Deploy. Keep the existing RADAR_HISTORY binding and all other settings. Do not create another Worker, clear KV, or add a competing cron. A genuinely newer production script than the archived base should first be compared, not overwritten blindly.

After deployment, verify actual relay_version/source_revision, full list completion, real hourly history and coverage, per-package Q24/Q7/readiness/reasons, actual feed freshness, persistence across repeated requests and quota/latency. Missing original observations cannot be recreated by simply waiting for a green CI. The patch first attempts to read existing KV; no speculative backfill has been performed.

Rollback is the prior Cloudflare version for the Worker and a normal Git revert for the publisher changes. The backup branch preserves pre-repair main. Do not force-push or delete live histories to roll back.

## Outstanding acceptance criteria

Cloudflare deployment, real KV restoration, real long-running continuity/load validation, phone push delivery, and storage migration are NOT established by this repair branch. The existing GitHub alert workflow emits notices; the separate ChatGPT task polls hourly and has additional delivery filters. Do not claim these are the same five-minute push pipeline or that a phone received a notification. The current task metadata reports notifications_enabled=false; this is not a diagnosis of the user's OS notification settings and the available task update action has no setter for that flag.

A full repository cleanup should archive and migrate telemetry with consumer changes and checksums before stopping writers; do not remove the sole current history source or rewrite Git during P0 recovery. A live history repair may reveal legitimate WAIT due to quality/EV; generating a BUY is not an acceptance criterion.
