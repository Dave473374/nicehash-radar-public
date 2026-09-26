# Radar snapshot archive: storage repair for #62

## Scope

The canonical logical history is still named `calibration/radar-snapshots.jsonl`
in existing CLI arguments and reports. New checkouts store it as
`calibration/radar-snapshots/manifest.json` plus immutable compressed chunks in
`calibration/radar-snapshots/chunks/`. Do not use `Path.exists()`, `Path.open()` or
`read_text()` directly to determine whether this logical archive is available.

This changes storage only. Source timestamps, feed hashes, relay versions, row
order, package models, fees, CURRENT/BUY logic and HIT/MISS policies are not
reinterpreted. No NiceHash Admin/account request is made by the storage layer.

## Lossless migration and preservation

Migration preserves every original byte (including whitespace, key ordering,
UTF-8, line endings, duplicate records and a missing final newline). Every
nonblank input line must be a valid finite JSON object. Invalid input stops the
operation; it is not silently discarded.

The first manifest records a `migrationAnchor` with the original logical byte
length, record count and SHA256. Each later verification checks that the exact
original byte prefix is still present. The SHA256 of the complete logical stream
is independent of the number of shards or compression representation.

The legacy source is removed from the current checkout only after all chunks,
logical digest/count/size and original-source equality have been verified and the
manifest has been atomically published. The old file remains in older Git
commits: no force push, Git history rewrite or evidence purge is performed.

## Bounded immutable storage

Each shard holds at most 8 MiB of uncompressed complete JSONL lines. Compressed
size is bounded too. A single row larger than 8 MiB fails explicitly. Shard names
contain their uncompressed SHA256. Append operations retain all existing shard
references and add new bounded chunks; previous shard bytes are never rewritten.
The manifest is capped at 16 MiB; every staged new file has a 95 MiB safety cap.
These limits prevent another silently growing monolith, not unlimited repository
size. Long-term storage volume and faster sampling still need capacity planning.

Small legacy fixtures remain readable/writable; large legacy files are migrated
before append. The production workflow explicitly migrates before backfill.

## Reader and writer contracts

`radar_snapshot_archive.py` exposes:

- `history_exists(path)`: understands the archive; a corrupt manifest is an error,
  not an empty/missing history.
- `open_history(path, 'r' or 'rb')` and `read_history_text(path)`: plain-file
  compatibility or a fully verified logical archived stream.
- `history_digest(path)`: SHA256 of the exact logical uncompressed bytes.
- `append_history(path, rows, expected_sha256=...)`: preserves existing source
  identity/dedup policy while rejecting a changed base history.
- `migrate_history(path)`, `verify_history(path)` and `stage_history(path)`.

Archived reads verify compressed and uncompressed hashes, lengths, record counts,
chunk boundaries and the full logical digest before exposing data. A temporary
anonymous file supplies the logical stream; no expanded monolith is created in
the repository. Symlinks, traversal paths, missing shards, gzip corruption and
bounded decompression failures are rejected. A simultaneous legacy file is only
accepted if it is exactly equivalent to the archive, as in crash recovery.

Both writers (`backfill_radar_snapshots.py`, `collect_calibration.py`) and all
known direct consumers are adapted. `diagnose_market_inputs.py` inherits the
adapter through the existing `json_lines` and `digest` helpers. Matching scripts
keep their current outcome/chronology logic. No private order fixture is published.

Writers use POSIX file locking (GitHub Linux runners and macOS). Readers are
portable. Writers fail explicitly on platforms without that locking support;
there is no silently unlocked Windows writer. Each chunk is fsynced and stored
before the manifest is atomically replaced. An interrupted append may leave
unreferenced chunks, but readers ignore them and retries verify/reuse matching
chunks. The stage command adds only manifest-referenced chunks, not orphan or
lock files.

## Workflow

The existing `Collect Radar Calibration` workflow retains its schedule and
concurrency group. It validates tests, verifies/migrates storage, restores missing
public feed revisions from Git, collects the current feed using existing dedup
rules, writes `research/radar-archive-status.json`, stages bounded files and pushes
normally. A GH001/size rejection stops immediately instead of being retried as a
push race. Ordinary races still use fetch/rebase, never force push.

`Radar Archive Integrity` runs the offline test suite plus before/after comparison
of the real public market report, paired quote history, input diagnostics, latency
report and Palladium M signal exposure. It uses a fixed cutoff so generated
outputs are directly comparable. This verifies storage equivalence, not mining
profitability or validity of previously unverified inputs.

The temporary hash-locked adapter and branch-preparation write job were removed
after creating the reviewed migration commit. The integrity workflow is read-only.

## Reproduction and operational checks

From the repository root, with Python 3.12+:

```sh
python3 -m unittest tests.test_radar_snapshot_archive -v
python3 scripts/radar_snapshot_archive.py verify
python3 scripts/verify_radar_archive_migration.py
```

The last command intentionally migrates a legacy checkout locally for its
before/after test. On an already migrated checkout it verifies idempotence and
reader parity. For approved collection:

```sh
python3 scripts/radar_snapshot_archive.py migrate
python3 scripts/backfill_radar_snapshots.py
python3 scripts/collect_calibration.py
python3 scripts/radar_snapshot_archive.py stage
python3 scripts/radar_snapshot_archive.py staged-guard
```

Inspect `lastCollectedAt`, `records`, `logicalSha256` and the original
`migrationAnchor` in the persisted status report. A green research calculation
alone is not proof that the collector successfully committed new history.

The original inspected archive contained 1,266 records / 104,622,879 bytes,
SHA256 `3990f4b3166fcb5ec543baf55783a403520a5691f361f27183da0b665c60466c`.
Its lossless initial compression produced 13 shards totaling 11,232,477 bytes.
The first prepared migration commit then recovered 435 additional snapshots:
1,701 records / 155,484,274 logical bytes / 20 chunks / 16,055,236 compressed bytes,
with last receipt 2026-09-26T06:08:46Z. The committed migration and status reports,
not this static example, identify the actual current count.

Continuous one-minute collection is not enabled by this repair. Existing public
market collection remains a separate workflow, and this change does not claim to
repair unrelated workflow failures or all external consumers outside the repo.
