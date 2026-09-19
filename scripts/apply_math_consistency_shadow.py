import json
import os
from pathlib import Path

BUY_FEED = Path(os.getenv("BUY_RADAR_FEED", "buy-feed.json"))

TWO32 = float(2 ** 32)
SUPPORTED_ALGORITHMS = {
    "SCRYPT",
    "SHA256ASICBOOST",
    "SHA256ASICBOOST_USDT",
}
PASS_MAX_DIFF_PERCENT = 5.0
WARNING_MAX_DIFF_PERCENT = 15.0


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    return None


def expected_blocks_from_difficulty(hashrate_hps, duration_s, difficulty):
    if (
        hashrate_hps is None
        or duration_s is None
        or difficulty is None
        or hashrate_hps <= 0
        or duration_s <= 0
        or difficulty <= 0
    ):
        return None

    return hashrate_hps * duration_s / (difficulty * TWO32)


def chain_check(package, chain):
    algorithm = str(chain.get("algorithm") or "").upper()

    if algorithm not in SUPPORTED_ALGORITHMS:
        return {
            "status": "NOT_APPLICABLE",
            "algorithm": algorithm or None,
            "reason": "Difficulty-to-work conversion not verified for this algorithm.",
        }

    package_hashrate = as_float(package.get("package_hashrate_hps"))
    duration = as_float(package.get("duration_seconds"))
    difficulty = as_float(chain.get("network_difficulty"))
    feed_expected = as_float(chain.get("expected_blocks"))

    difficulty_expected = expected_blocks_from_difficulty(
        package_hashrate,
        duration,
        difficulty,
    )

    if difficulty_expected is None or feed_expected is None:
        return {
            "status": "UNKNOWN",
            "algorithm": algorithm,
            "reason": "Missing inputs for independent difficulty consistency check.",
        }

    if difficulty_expected == 0:
        return {
            "status": "UNKNOWN",
            "algorithm": algorithm,
            "reason": "Independent expected blocks is zero.",
        }

    signed_diff_percent = (
        (feed_expected / difficulty_expected) - 1.0
    ) * 100.0
    abs_diff_percent = abs(signed_diff_percent)

    if abs_diff_percent <= PASS_MAX_DIFF_PERCENT:
        status = "PASS"
    elif abs_diff_percent <= WARNING_MAX_DIFF_PERCENT:
        status = "WARNING"
    else:
        status = "CRITICAL"

    return {
        "status": status,
        "algorithm": algorithm,
        "feedExpectedBlocks": round(feed_expected, 12),
        "difficultyExpectedBlocks": round(difficulty_expected, 12),
        "signedDifferencePercent": round(signed_diff_percent, 4),
        "absoluteDifferencePercent": round(abs_diff_percent, 4),
        "difficulty": difficulty,
        "formula": "hashrate_hps * duration_seconds / (difficulty * 2^32)",
    }


def overall_status(checks):
    statuses = {check.get("status") for check in checks}
    if "CRITICAL" in statuses:
        return "CRITICAL"
    if "WARNING" in statuses:
        return "WARNING"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "PASS" in statuses:
        return "PASS"
    return "NOT_APPLICABLE"


feed = json.loads(BUY_FEED.read_text(encoding="utf-8"))

if feed.get("status") != "BUY FEED OK" or feed.get("ok") is not True:
    raise SystemExit("Refusing to annotate unhealthy BUY feed")

packages = feed.get("packages") or []
summary = {
    "PASS": 0,
    "WARNING": 0,
    "CRITICAL": 0,
    "UNKNOWN": 0,
    "NOT_APPLICABLE": 0,
}

for package in packages:
    original_signal = package.get("final_signal")

    checks = []
    primary = package.get("primary_chain") or {}
    if primary:
        primary_check = chain_check(package, primary)
        primary_check["chain"] = "PRIMARY"
        checks.append(primary_check)

    merge = package.get("merge_chain") or {}
    if merge:
        merge_check = chain_check(package, merge)
        merge_check["chain"] = "MERGE"
        checks.append(merge_check)

    status = overall_status(checks)
    summary[status] = summary.get(status, 0) + 1

    package["math_consistency_shadow"] = {
        "model_version": 1,
        "status": status,
        "production_override": False,
        "checks": checks,
        "thresholds": {
            "passMaxAbsoluteDifferencePercent": PASS_MAX_DIFF_PERCENT,
            "warningMaxAbsoluteDifferencePercent": WARNING_MAX_DIFF_PERCENT,
        },
        "policy": (
            "Independent audit only. This check never changes CURRENT. "
            "A WARNING/CRITICAL result means network hashrate-derived and "
            "difficulty-derived expected work are materially inconsistent "
            "and should not be treated as independent confirmation."
        ),
    }

    if package.get("final_signal") != original_signal:
        raise SystemExit("Math consistency shadow modified final_signal")

feed["math_consistency_shadow"] = {
    "model_version": 1,
    "model_use": "MATH_CONSISTENCY_AUDIT_ONLY",
    "production_model": "CURRENT",
    "production_model_changed": False,
    "supported_algorithms": sorted(SUPPORTED_ALGORITHMS),
    "summary": summary,
}

BUY_FEED.write_text(
    json.dumps(feed, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print("MATH CONSISTENCY SHADOW APPLIED")
print("Summary:", summary)
print("CURRENT production fields were not modified")
