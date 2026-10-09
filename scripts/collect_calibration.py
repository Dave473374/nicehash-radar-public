"""Append eligible observations without discarding a successful Git backfill.

The existing workflow may explicitly skip an unavailable CURRENT source. Archive
integrity/write failures remain fatal. No network requests or decision changes.
"""
try:
    from radar_snapshot_archive import history_exists, read_history_text, append_history, history_digest
except ModuleNotFoundError:  # Also support package/spec imports from repository root.
    from scripts.radar_snapshot_archive import history_exists, read_history_text, append_history, history_digest
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SOURCE_FILE = Path("buy-feed.json")
OUTPUT_DIR = Path("calibration")
OUTPUT_FILE = OUTPUT_DIR / "radar-snapshots.jsonl"
MAX_LIVE_FEED_AGE_SECONDS = 15 * 60


def validate_feed(feed, now=None):
    if not isinstance(feed, dict):
        raise AssertionError("Feed must be a JSON object")
    errors = []
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(str(feed.get("checked_at")).replace("Z", "+00:00"))
        if checked.tzinfo is None or checked.utcoffset() is None:
            raise ValueError("Timezone required")
        age = (now - checked).total_seconds()
        if not 0 <= age <= MAX_LIVE_FEED_AGE_SECONDS:
            errors.append("Feed source time is future-dated or older than 15 minutes")
    except (TypeError, ValueError, OverflowError):
        errors.append("Missing or invalid timezone-aware checked_at")
    if feed.get("status") != "BUY FEED OK":
        errors.append(f"Bad status: {feed.get('status')}")
    if feed.get("ok") is not True:
        errors.append("Feed ok is not true")
    if feed.get("upstream_status") != 200:
        errors.append(f"Bad upstream_status: {feed.get('upstream_status')}")
    if feed.get("market_status") != "MARKET OK":
        errors.append(f"Bad market_status: {feed.get('market_status')}")
    if not feed.get("relay_version"):
        errors.append("relay_version missing")
    if (not isinstance(feed.get("packages"), list) or not feed.get("packages")
            or any(not isinstance(p, dict) for p in feed["packages"])):
        errors.append("packages missing or empty")
    if errors:
        raise AssertionError("; ".join(errors))


def canonical_feed_sha256(feed):
    payload = json.dumps(feed, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def existing_feed_hashes():
    hashes = set()
    if not history_exists(OUTPUT_FILE):
        return hashes
    for line in read_history_text(OUTPUT_FILE, encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        saved_hash = row.get("feed_sha256")
        if saved_hash:
            hashes.add(str(saved_hash))
            continue
        historical_feed = row.get("feed")
        if isinstance(historical_feed, dict):
            hashes.add(canonical_feed_sha256(historical_feed))
    return hashes


def collect_snapshot(source_file=SOURCE_FILE, *, now=None, skip_unavailable=False,
                     source_kind="LIVE_WORKFLOW"):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Collection time must be timezone-aware")
    result = {
        "schemaVersion": 1, "role": "RADAR_ARCHIVE_COLLECTION_STATUS",
        "checkedAt": now.isoformat(), "sourceKind": source_kind,
        "sourceCheckedAt": None, "sourceStatus": None, "sourceEligible": False,
        "snapshotAppended": False, "automaticPurchase": False, "canRaiseSignal": False,
    }
    # Only source acquisition/validation errors may be skipped. In particular,
    # no archive reads/writes are inside this exception handler.
    try:
        feed = json.loads(Path(source_file).read_text(encoding="utf-8"))
        if isinstance(feed, dict):
            result["sourceCheckedAt"] = feed.get("checked_at")
            result["sourceStatus"] = feed.get("status")
        validate_feed(feed, now)
        feed_hash = canonical_feed_sha256(feed)  # Reject NaN/Infinity too.
    except (OSError, UnicodeError, ValueError, AssertionError) as exc:
        if not skip_unavailable:
            raise
        result.update(status="CURRENT_INPUT_SKIPPED", reason=str(exc))
        return result

    result["sourceEligible"] = True
    base_history_sha = history_digest(OUTPUT_FILE) if history_exists(OUTPUT_FILE) else None
    if feed_hash in existing_feed_hashes():
        result.update(status="SNAPSHOT_ALREADY_PRESENT", feedSha256=feed_hash)
        return result
    snapshot = {
        "collected_at": now.isoformat(), "source": source_kind,
        "source_commit": os.getenv("GITHUB_SHA"), "feed_sha256": feed_hash,
        "feed_generated_at": feed["checked_at"], "relay_version": feed.get("relay_version"),
        "decision_engine": feed.get("decision_engine"), "feed": feed,
    }
    append_history(OUTPUT_FILE, [snapshot], expected_sha256=base_history_sha)
    result.update(status="SNAPSHOT_APPENDED", snapshotAppended=True, feedSha256=feed_hash)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_FILE)
    parser.add_argument("--source-kind", choices=("LIVE_WORKFLOW", "SCHEDULED_RELAY_READ"),
                        default="LIVE_WORKFLOW")
    parser.add_argument("--skip-unavailable", action="store_true",
                        help="Skip invalid current input, not archive corruption/write errors")
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args(argv)
    result = collect_snapshot(args.source, skip_unavailable=args.skip_unavailable,
                              source_kind=args.source_kind)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.status_file:
        args.status_file.parent.mkdir(parents=True, exist_ok=True)
        args.status_file.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if not result["sourceEligible"]:
        print("::warning::Current feed unavailable; valid Git-history backfill is retained. See collection status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
