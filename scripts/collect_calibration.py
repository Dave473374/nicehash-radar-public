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
    payload = json.dumps(
        feed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def existing_feed_hashes():
    hashes = set()

    if not OUTPUT_FILE.exists():
        return hashes

    for line in OUTPUT_FILE.read_text(encoding="utf-8").splitlines():
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


feed = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
collected_at = datetime.now(timezone.utc)
feed_hash = canonical_feed_sha256(feed)

if feed_hash in existing_feed_hashes():
    print("Calibration snapshot already present for current feed")
    print("Relay version:", feed.get("relay_version"))
    raise SystemExit(0)

validate_feed(feed, collected_at)

snapshot = {
    "collected_at": collected_at.isoformat(),
    "source": "LIVE_WORKFLOW",
    "source_commit": os.getenv("GITHUB_SHA"),
    "feed_sha256": feed_hash,
    "feed_generated_at": feed["checked_at"],
    "relay_version": feed.get("relay_version"),
    "decision_engine": feed.get("decision_engine"),
    "feed": feed,
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with OUTPUT_FILE.open("a", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            snapshot,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )

print("Calibration snapshot created successfully")
print("Relay version:", feed.get("relay_version"))
print("Source commit:", snapshot["source_commit"] or "UNKNOWN")
