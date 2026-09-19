"""Join independently verified HITs to causal public market-edge context.

Research only. This script never creates a BUY signal, never supplies a MISS
denominator, and never treats reward-time context as entry-time evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_ONCHAIN = Path("research/onchain-hit-verification.jsonl")
DEFAULT_PAIRS = Path("research/market-edge-pairs.jsonl")
DEFAULT_EPISODES = Path("research/lag-episodes.jsonl")
DEFAULT_OUTPUT = Path("research/verified-hit-market-context.jsonl")
DEFAULT_REPORT = Path("research/verified-hit-market-context-report.json")

MAX_CONTEXT_AGE_SECONDS = 15 * 60
MAX_RECENT_LAG_MINUTES = 60


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_jsonl(path: Path, counts: Counter, prefix: str) -> list[dict]:
    rows = []
    if not path.exists():
        counts[f"{prefix}Missing"] += 1
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            counts[f"{prefix}Malformed"] += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            counts[f"{prefix}Malformed"] += 1
    return rows


def parse_time(value: Any, *, explorer_naive_utc: bool = False) -> tuple[datetime | None, str | None]:
    if value is None or isinstance(value, bool):
        return None, None

    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None, None
        if abs(number) > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc), "EPOCH_UTC"
        except (ValueError, OSError, OverflowError):
            return None, None

    text = str(value).strip()
    if not text:
        return None, None

    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and math.isfinite(number):
        if abs(number) > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc), "EPOCH_UTC"
        except (ValueError, OSError, OverflowError):
            return None, None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, None

    if dt.tzinfo is None:
        if not explorer_naive_utc:
            return None, None
        return dt.replace(tzinfo=timezone.utc), "NAIVE_EXPLORER_TIME_ASSUMED_UTC"
    return dt.astimezone(timezone.utc), "EXPLICIT_TIMEZONE"


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def event_coin_matches_pair(event_coin: str, pair: dict) -> bool:
    event_coin = event_coin.upper()
    return event_coin in {
        str(pair.get("coin") or "").upper(),
        str(pair.get("mergeCoin") or "").upper(),
    }


def episode_coin_matches(event_coin: str, episode: dict) -> bool:
    entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
    coins = {str(entry.get("coin") or "").upper()}
    signature = entry.get("signature")
    if isinstance(signature, list) and len(signature) > 4:
        coins.add(str(signature[4] or "").upper())
    return event_coin.upper() in coins


def verified_hit(row: dict) -> bool:
    return str(row.get("status") or "").startswith("VERIFIED_ON_CHAIN")


def build_context(onchain_rows: list[dict], pair_rows: list[dict], episode_rows: list[dict], now: datetime) -> tuple[list[dict], dict]:
    counts = Counter()

    pairs_by_package: dict[str, list[dict]] = defaultdict(list)
    for pair in pair_rows:
        package = pair.get("package")
        quote_at, _ = parse_time(pair.get("quoteAt"))
        observed_at, _ = parse_time(pair.get("observedAt"))
        if not isinstance(package, str) or not package or quote_at is None or observed_at is None:
            counts["invalidPairRows"] += 1
            continue
        pairs_by_package[package].append({**pair, "_quote": quote_at, "_observed": observed_at})
    for rows in pairs_by_package.values():
        rows.sort(key=lambda row: row["_quote"])

    episodes_by_package: dict[str, list[dict]] = defaultdict(list)
    for episode in episode_rows:
        entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
        package = entry.get("package")
        quote_at, _ = parse_time(entry.get("quoteAt"))
        if not isinstance(package, str) or not package or quote_at is None:
            counts["invalidEpisodeRows"] += 1
            continue
        episodes_by_package[package].append({**episode, "_entryQuote": quote_at})
    for rows in episodes_by_package.values():
        rows.sort(key=lambda row: row["_entryQuote"])

    output = []
    by_coin = defaultdict(lambda: Counter())
    by_package = defaultdict(lambda: Counter())
    signal_counts = Counter()
    lag_counts = Counter()

    for hit in onchain_rows:
        if not verified_hit(hit):
            counts["nonVerifiedLedgerRowsSkipped"] += 1
            continue

        event_id = str(hit.get("eventId") or "").strip()
        package = hit.get("packageName")
        coin = str(hit.get("coin") or "").upper()
        hit_at, time_basis = parse_time(hit.get("onchainTimestamp"), explorer_naive_utc=True)
        if not event_id or not isinstance(package, str) or not package or not coin:
            counts["invalidVerifiedHitIdentity"] += 1
            continue
        if hit_at is None:
            counts["verifiedHitMissingUsableChainTime"] += 1

        record = {
            "schemaVersion": 1,
            "eventId": event_id,
            "coin": coin,
            "blockHeight": hit.get("blockHeight"),
            "packageName": package,
            "packageId": hit.get("packageId"),
            "hitAt": iso(hit_at),
            "hitTimeBasis": time_basis,
            "onchainStatus": hit.get("status"),
            "onchainVerificationStrength": hit.get("verificationStrength"),
            "onchainExplorer": hit.get("explorer"),
            "sourceRole": "VERIFIED_SUCCESS_EVENT_REWARD_TIME_CONTEXT_ONLY",
            "entryTimeEligible": False,
            "canSupplyMissDenominator": False,
            "canRaiseSignal": False,
            "usedForBuyDecisions": False,
            "marketContext": None,
            "lagContext": None,
        }
        by_coin[coin]["verifiedHits"] += 1
        by_package[package]["verifiedHits"] += 1

        exact_rows = [
            pair for pair in pairs_by_package.get(package, [])
            if hit_at is not None
            and event_coin_matches_pair(coin, pair)
            and pair["_quote"] <= hit_at
            and pair["_observed"] <= hit_at
        ]

        if not pairs_by_package.get(package):
            context_status = "NO_EXACT_PACKAGE_SERIES"
        elif not exact_rows:
            context_status = "NO_CAUSAL_EXACT_PACKAGE_QUOTE"
        else:
            latest = exact_rows[-1]
            age = (hit_at - latest["_quote"]).total_seconds()
            context = {
                "status": None,
                "quoteAt": latest.get("quoteAt"),
                "observedAt": latest.get("observedAt"),
                "quoteAgeSeconds": round(age, 3),
                "pairStatus": latest.get("pairStatus"),
                "currency": latest.get("currency"),
                "primaryCoin": latest.get("coin"),
                "mergeCoin": latest.get("mergeCoin"),
                "marketAlgorithm": latest.get("marketAlgorithm"),
                "relayVersion": latest.get("relayVersion"),
                "priceNative": latest.get("priceNative"),
                "costEur": latest.get("costEur"),
                "durationSeconds": latest.get("durationSeconds"),
                "hashrateHps": latest.get("hashrateHps"),
                "workPerNative": latest.get("workPerNative"),
                "marketAt": latest.get("marketAt"),
                "marketAgeSeconds": latest.get("marketAgeSeconds"),
                "marketPriceRaw": latest.get("marketPriceRaw"),
                "marketOrders": latest.get("marketOrders"),
                "marketSpeedRaw": latest.get("marketSpeedRaw"),
                "relativeValueLogIndex": latest.get("relativeValueLogIndex"),
                "primaryDifficulty": latest.get("primaryDifficulty"),
                "mergeDifficulty": latest.get("mergeDifficulty"),
                "feedExpectedReturnPercent": latest.get("feedExpectedReturnPercent"),
                "mathStatus": latest.get("mathStatus"),
                "currentSignal": latest.get("currentSignal"),
            }
            if age > MAX_CONTEXT_AGE_SECONDS:
                context_status = "STALE_EXACT_PACKAGE_QUOTE"
            elif latest.get("pairStatus") != "PAIRED":
                context_status = "EXACT_PACKAGE_QUOTE_NO_VALID_MARKET_PAIR"
            else:
                context_status = "MATCHED_FRESH_EXACT_PACKAGE"
                signal_counts[str(latest.get("currentSignal") or "UNKNOWN")] += 1
                by_coin[coin]["freshExactMarketMatches"] += 1
                by_package[package]["freshExactMarketMatches"] += 1
            context["status"] = context_status
            record["marketContext"] = context

        record["marketContextStatus"] = context_status
        counts[context_status] += 1

        recent_episodes = []
        if hit_at is not None:
            for episode in episodes_by_package.get(package, []):
                if episode.get("sourceRevision") is True or not episode_coin_matches(coin, episode):
                    continue
                entry_at = episode["_entryQuote"]
                if entry_at > hit_at:
                    continue
                lead_minutes = (hit_at - entry_at).total_seconds() / 60
                if lead_minutes <= MAX_RECENT_LAG_MINUTES:
                    recent_episodes.append((entry_at, lead_minutes, episode))

        if recent_episodes:
            _, lead_minutes, episode = recent_episodes[-1]
            entry = episode.get("entry") if isinstance(episode.get("entry"), dict) else {}
            ep = episode.get("episode") if isinstance(episode.get("episode"), dict) else {}
            available_at, _ = parse_time(entry.get("availableAt"))
            first_recorded_at, _ = parse_time(episode.get("firstRecordedAt"))
            last_observed_at, _ = parse_time(ep.get("lastObservedAt"))
            within_observed = bool(
                hit_at is not None
                and last_observed_at is not None
                and episode["_entryQuote"] <= hit_at <= last_observed_at
            )
            feature_available_before_hit = bool(available_at is not None and available_at <= hit_at)
            recorded_before_hit = bool(first_recorded_at is not None and first_recorded_at <= hit_at)
            record["lagContext"] = {
                "status": "RECENT_EXACT_PACKAGE_LAG_EPISODE",
                "episodeId": episode.get("id"),
                "cohort": episode.get("cohort"),
                "entryQuoteAt": entry.get("quoteAt"),
                "availableAt": entry.get("availableAt"),
                "firstRecordedAt": episode.get("firstRecordedAt"),
                "minutesAfterEntry": round(lead_minutes, 3),
                "entryFeatures": entry.get("features"),
                "episodeStatus": ep.get("status"),
                "episodeLastObservedAt": ep.get("lastObservedAt"),
                "featureAvailableBeforeHit": feature_available_before_hit,
                "episodeRecordedBeforeHit": recorded_before_hit,
                "hitWithinObservedEpisodeWindow": within_observed,
                "interpretation": "SUCCESS_EVENT_CONTEXT_ONLY_NOT_CAUSAL_OR_HIT_RATE_EVIDENCE",
            }
            lag_counts["recentExactPackageEpisode"] += 1
            if feature_available_before_hit:
                lag_counts["featureAvailableBeforeHit"] += 1
            if recorded_before_hit:
                lag_counts["episodeRecordedBeforeHit"] += 1
            if within_observed:
                lag_counts["hitWithinObservedEpisodeWindow"] += 1
        else:
            record["lagContext"] = {
                "status": "NO_RECENT_EXACT_PACKAGE_LAG_EPISODE",
                "lookbackMinutes": MAX_RECENT_LAG_MINUTES,
            }

        output.append(record)

    output.sort(key=lambda row: (row.get("hitAt") or "", row.get("eventId") or ""))

    report = {
        "schemaVersion": 1,
        "generatedAt": iso(now),
        "role": "VERIFIED_HIT_MARKET_CONTEXT_RESEARCH_ONLY",
        "currentProductionModelChanged": False,
        "canRaiseSignal": False,
        "automaticPurchase": False,
        "automaticCancel": False,
        "privateApiUsed": False,
        "networkRequestsMade": 0,
        "entryTimeEligible": False,
        "canSupplyMissDenominator": False,
        "verifiedHitCount": len(output),
        "freshExactMarketMatchCount": counts["MATCHED_FRESH_EXACT_PACKAGE"],
        "marketContextStatusCounts": {
            key: counts[key]
            for key in (
                "MATCHED_FRESH_EXACT_PACKAGE",
                "STALE_EXACT_PACKAGE_QUOTE",
                "EXACT_PACKAGE_QUOTE_NO_VALID_MARKET_PAIR",
                "NO_CAUSAL_EXACT_PACKAGE_QUOTE",
                "NO_EXACT_PACKAGE_SERIES",
            )
            if counts[key]
        },
        "signalAtFreshHitContextCounts": dict(signal_counts),
        "lagContextCounts": dict(lag_counts),
        "byCoin": {key: dict(value) for key, value in sorted(by_coin.items())},
        "byPackage": {key: dict(value) for key, value in sorted(by_package.items())},
        "settings": {
            "maxExactPackageContextAgeSeconds": MAX_CONTEXT_AGE_SECONDS,
            "recentLagEpisodeLookbackMinutes": MAX_RECENT_LAG_MINUTES,
            "exactPackageOnly": True,
            "eventCoinMayMatchPrimaryOrMergeCoin": True,
            "quoteAndObservationMustPrecedeHit": True,
        },
        "verdict": "DESCRIPTIVE_SUCCESS_CONTEXT_ONLY_NO_VERIFIED_INCREMENTAL_EDGE",
        "limitations": [
            "Every on-chain input row is a success event; this report has no MISS denominator and cannot estimate hit rate.",
            "The matched quote is reward-time context, not the ticket purchase/entry state.",
            "A HIT near a lag episode does not establish causality, profitability, or a useful predictor.",
            "Package names are matched exactly; M/L/Team/S are never substituted for one another.",
            "Only quotes already observed before the block are eligible; future observations are excluded.",
            "A market PAIR is a relative public statistic, not a verified executable EasyMining purchase price.",
        ],
        "counts": dict(counts),
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onchain", type=Path, default=DEFAULT_ONCHAIN)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--episodes", type=Path, default=DEFAULT_EPISODES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--now", help="Aware ISO timestamp for reproducible offline analysis")
    args = parser.parse_args()

    source_paths = [args.onchain, args.pairs, args.episodes]
    output_paths = [args.output, args.report]
    if len({path.resolve() for path in source_paths + output_paths}) != 5:
        parser.error("Inputs and outputs must be distinct files")
    for path in source_paths:
        if not path.exists():
            parser.error(f"Missing input: {path}")

    now, _ = parse_time(args.now) if args.now else (datetime.now(timezone.utc), "SYSTEM_UTC")
    if now is None:
        parser.error("--now must include a timezone or be an epoch timestamp")

    before = {str(path): digest(path) for path in source_paths}
    counts = Counter()
    onchain_rows = load_jsonl(args.onchain, counts, "onchain")
    pair_rows = load_jsonl(args.pairs, counts, "pairs")
    episode_rows = load_jsonl(args.episodes, counts, "episodes")

    rows, report = build_context(onchain_rows, pair_rows, episode_rows, now)
    report["inputReadWarnings"] = dict(counts)
    report["inputSha256"] = before

    after = {str(path): digest(path) for path in source_paths}
    if before != after:
        raise RuntimeError("Input changed during analysis; output refused")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    print("VERIFIED HIT MARKET CONTEXT COMPLETE; research-only; no account access")
    print(json.dumps({
        "verifiedHitCount": report["verifiedHitCount"],
        "freshExactMarketMatchCount": report["freshExactMarketMatchCount"],
        "marketContextStatusCounts": report["marketContextStatusCounts"],
        "signalAtFreshHitContextCounts": report["signalAtFreshHitContextCounts"],
        "lagContextCounts": report["lagContextCounts"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
