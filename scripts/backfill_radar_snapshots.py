import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_FILE = Path("calibration/radar-snapshots.jsonl")
BUY_FEED_PATH = "buy-feed.json"


def parse_ts(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def validate_feed(feed):
    return (
        isinstance(feed, dict)
        and feed.get("status") == "BUY FEED OK"
        and feed.get("ok") is True
        and feed.get("upstream_status") == 200
        and feed.get("market_status") == "MARKET OK"
        and bool(feed.get("relay_version"))
        and isinstance(feed.get("packages"), list)
        and bool(feed.get("packages"))
    )


def canonical_feed_sha256(feed):
    payload = json.dumps(
        feed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_existing():
    commits = set()
    feed_hashes = set()
    latest = None

    if not OUTPUT_FILE.exists():
        return commits, feed_hashes, latest

    for line in OUTPUT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        source_commit = row.get("source_commit")
        if source_commit:
            commits.add(str(source_commit))

        saved_hash = row.get("feed_sha256")
        if saved_hash:
            feed_hashes.add(str(saved_hash))
        else:
            historical_feed = row.get("feed")
            if isinstance(historical_feed, dict):
                feed_hashes.add(canonical_feed_sha256(historical_feed))

        ts = parse_ts(row.get("collected_at"))
        if ts is not None and (latest is None or ts > latest):
            latest = ts

    return commits, feed_hashes, latest


def git_log_since(latest):
    command = [
        "git",
        "log",
        "--reverse",
        "--format=%H%x09%cI",
    ]

    if latest is not None:
        command.append(f"--since={latest.isoformat()}")

    command.extend(["--", BUY_FEED_PATH])

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )

    commits = []

    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        sha, committed_at = line.split("\t", 1)
        commits.append((sha.strip(), committed_at.strip()))

    return commits


def load_feed_from_commit(sha):
    result = subprocess.run(
        ["git", "show", f"{sha}:{BUY_FEED_PATH}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


known_commits, known_feed_hashes, latest_existing = load_existing()
candidates = git_log_since(latest_existing)

added = []
skipped_existing = 0
skipped_invalid = 0
relay_versions = {}

for sha, committed_at in candidates:
    if sha in known_commits:
        skipped_existing += 1
        continue

    try:
        feed = load_feed_from_commit(sha)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        skipped_invalid += 1
        continue

    if not validate_feed(feed):
        skipped_invalid += 1
        continue

    feed_hash = canonical_feed_sha256(feed)
    if feed_hash in known_feed_hashes:
        skipped_existing += 1
        continue

    collected_at = parse_ts(committed_at)
    if collected_at is None:
        skipped_invalid += 1
        continue

    snapshot = {
        "collected_at": collected_at.isoformat(),
        "source": "GIT_BUY_FEED_HISTORY",
        "source_commit": sha,
        "feed_sha256": feed_hash,
        "feed_generated_at": (
            feed.get("generated_at")
            or feed.get("timestamp")
            or feed.get("updated_at")
            or feed.get("checked_at")
        ),
        "relay_version": feed.get("relay_version"),
        "decision_engine": feed.get("decision_engine"),
        "feed": feed,
    }

    added.append(snapshot)
    known_commits.add(sha)
    known_feed_hashes.add(feed_hash)

    version = str(feed.get("relay_version"))
    relay_versions[version] = relay_versions.get(version, 0) + 1

if added:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("a", encoding="utf-8") as handle:
        for snapshot in added:
            handle.write(
                json.dumps(
                    snapshot,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )

print("RADAR SNAPSHOT BACKFILL")
print("Latest existing snapshot:", latest_existing.isoformat() if latest_existing else "NONE")
print("Candidate buy-feed commits:", len(candidates))
print("Added snapshots:", len(added))
print("Skipped existing:", skipped_existing)
print("Skipped invalid:", skipped_invalid)
print("Relay versions added:", relay_versions)
