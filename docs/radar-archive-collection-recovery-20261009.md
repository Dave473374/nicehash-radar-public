# Existing Radar archive collection recovery — 9 October 2026

## Observed failure

The real scheduled collection run 37938396455 (collect job 113846401098)
verified the 3,283-row sharded archive, reconstructed 37 eligible Git snapshots,
then failed when `collect_calibration.py` rejected the current checkout's stale
`BUY FEED WARNING`. Verification, staging and push were skipped; reconstructed
records never reached main. This explains why a newly generated market research
report continued to use input quotes no newer than 5 October.

## Minimal recovery

The existing calibration workflow and its `7,37 * * * *` schedule remain.
Git backfill and the lossless archive implementation are unchanged. After
backfill, the same workflow reads the existing Cloudflare scheduled `/buy-feed`
result to a temporary file. It is a public, bounded, no-redirect HTTP read, not
a NiceHash upstream acquisition, second producer or new Worker. It does not
overwrite the production `buy-feed.json`, use private APIs or alter Cron.

Current-input failure is explicit and non-fatal only with `--skip-unavailable`.
Strict source validation remains: warning, stale, future, missing and malformed
quotes do not become healthy training samples. The original checked_at and
signals remain untouched. An unavailable live read does not fall back to an
older favourable quote. Archive corruption, integrity failure and write failure
still fail the job. Verified Git backfill can now be committed independently
of current-input eligibility, with the existing guarded push-race handling.

`research/radar-collection-status.json` distinguishes the current consumer
check time from sourceCheckedAt and sourceEligible. A green archival job alone
is neither fresh source evidence nor proof of a healthy signal engine. Read it
together with `research/radar-archive-status.json`, and inspect actual
latestQuoteAt values in the research report, not only its generatedAt.

## Boundaries and acceptance

The added tests are offline/synthetic and do not establish profitability.
Acceptance requires a real main-branch run that persists new archive records,
followed by the existing research job showing newer input quote timestamps.
All historical bytes and observed gaps are retained; unavailable observations
are not recreated. The comparison against random timing is not automatically
implemented or validated by this repair. Model EV is not realized ROI, and
success-only public block records do not supply a MISS denominator.

No production signals/thresholds, Worker code, schedules, private data, order
behaviour, VMM or Crypto Alpha files are changed.
