import json
from pathlib import Path

BUY_FEED = Path("buy-feed.json")


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    return None


def rounded(value, digits=4):
    if value is None:
        return None
    return round(float(value), digits)


def weighted_mispricing(q24, q7):
    if q24 is not None and q7 is not None:
        return 0.7 * q24 + 0.3 * q7
    if q24 is not None:
        return q24
    if q7 is not None:
        return q7
    return None


def edge_label(score):
    if score is None:
        return "UNKNOWN"
    if score >= 10:
        return "HIGH"
    if score >= 5:
        return "MEDIUM"
    if score >= 0:
        return "NEUTRAL"
    return "NEGATIVE"


def build_edge_shadow(package, calibration_confidence):
    history = package.get("history_trend") or {}
    profitability = package.get("profitability") or {}
    primary = package.get("primary_chain") or {}
    merge = package.get("merge_chain") or {}
    quality = history.get("current_mining_quality") or {}

    q24 = as_float(
        history.get("expected_blocks_per_btc_vs_24h_percent")
    )
    q7 = as_float(
        history.get("expected_blocks_per_btc_vs_7d_percent")
    )
    expected_return = as_float(
        profitability.get("expected_return_percent")
    )

    mispricing = weighted_mispricing(q24, q7)

    # EV/profitability remains the main filter. Negative expected-return
    # headroom penalizes the mining edge one-for-one; positive EV does not
    # boost the edge score in shadow v1.
    economic_penalty = None
    if expected_return is not None:
        economic_penalty = min(expected_return - 100.0, 0.0)

    score = None
    if mispricing is not None:
        score = mispricing + (
            economic_penalty
            if economic_penalty is not None
            else 0.0
        )

    break_even = profitability.get("break_even_risk") or {}
    current_signal = package.get("final_signal")

    return {
        "model_version": 1,
        "status": (
            "LOW_CONFIDENCE_AUDIT_ONLY"
            if str(calibration_confidence).upper() == "LOW"
            else "AUDIT_ONLY"
        ),
        "production_override": False,
        "current_signal": current_signal,
        "changed_from_current": False,
        "edge_label": edge_label(score),
        "edge_score": rounded(score),
        "mispricing_score_percent": rounded(mispricing),
        "economic_penalty_percent": rounded(economic_penalty),
        "formula": (
            "edge_score = 0.7*q24 + 0.3*q7 + "
            "min(expected_return_percent - 100, 0); "
            "if one history window is unavailable, use the available window. "
            "Positive EV never boosts edge_score in shadow v1."
        ),
        "single_ticket_probability": {
            "primary_hit_probability_percent": rounded(
                as_float(primary.get("model_hit_probability_percent"))
            ),
            "merge_hit_probability_percent": rounded(
                as_float(merge.get("model_hit_probability_percent"))
            ),
            "primary_break_even_probability_percent_approx": rounded(
                as_float(
                    profitability.get(
                        "break_even_probability_percent_approx"
                    )
                ),
                8,
            ),
            "profit_probability_note": (
                "Break-even probability is the existing primary-chain "
                "Poisson approximation, not an exact P(profit), especially "
                "for merged mining."
            ),
        },
        "hashwork_value": {
            "expected_blocks_per_btc": rounded(
                as_float(quality.get("expected_blocks_per_btc")),
                8,
            ),
            "hashrate_hps_per_btc": rounded(
                as_float(quality.get("hashrate_hps_per_btc")),
                4,
            ),
            "quality_vs_24h_percent": rounded(q24),
            "quality_vs_7d_percent": rounded(q7),
        },
        "economics": {
            "expected_return_percent": rounded(expected_return),
            "profitability_margin_percent": rounded(
                as_float(
                    profitability.get("profitability_margin_percent")
                )
            ),
            "expected_reward_btc_equiv": rounded(
                as_float(
                    profitability.get("expected_reward_btc_equiv")
                ),
                12,
            ),
            "price_btc_equiv": rounded(
                as_float(package.get("price_btc_equiv")),
                12,
            ),
            "break_even_risk": break_even.get("status"),
        },
        "network_context": {
            "duration_seconds": package.get("duration_seconds"),
            "package_hashrate_hps": package.get("package_hashrate_hps"),
            "primary": {
                "currency": primary.get("currency"),
                "network_difficulty": primary.get("network_difficulty"),
                "network_hashpower_hps": primary.get(
                    "network_hashpower_hps"
                ),
                "block_time_seconds": primary.get("block_time_seconds"),
                "block_reward": primary.get("block_reward"),
                "expected_blocks": primary.get("expected_blocks"),
            },
            "merge": (
                {
                    "currency": merge.get("currency"),
                    "network_difficulty": merge.get("network_difficulty"),
                    "network_hashpower_hps": merge.get(
                        "network_hashpower_hps"
                    ),
                    "block_time_seconds": merge.get("block_time_seconds"),
                    "block_reward": merge.get("block_reward"),
                    "expected_blocks": merge.get("expected_blocks"),
                }
                if merge
                else None
            ),
        },
        "calibration_confidence": calibration_confidence,
        "policy": (
            "EDGE is shadow/audit only. It cannot raise, lower, replace, "
            "or otherwise modify CURRENT final_signal."
        ),
    }


if not BUY_FEED.exists():
    raise SystemExit(f"BUY feed missing: {BUY_FEED}")

feed = json.loads(BUY_FEED.read_text(encoding="utf-8"))

if feed.get("status") != "BUY FEED OK" or feed.get("ok") is not True:
    raise SystemExit("Refusing to EDGE-annotate an unhealthy BUY feed")

packages = feed.get("packages")
if not isinstance(packages, list) or not packages:
    raise SystemExit("BUY feed packages missing or empty")

calibrated = feed.get("calibrated_shadow") or {}
confidence = calibrated.get("confidence") or "UNKNOWN"

for package in packages:
    if not isinstance(package, dict):
        continue

    original_signal = package.get("final_signal")
    shadow = build_edge_shadow(package, confidence)

    if package.get("final_signal") != original_signal:
        raise SystemExit("EDGE shadow unexpectedly modified final_signal")

    package["edge_shadow"] = shadow

feed["edge_shadow"] = {
    "model_version": 1,
    "model_use": "EDGE_SHADOW_AUDIT_ONLY",
    "production_model": "CURRENT",
    "production_model_changed": False,
    "calibration_confidence": confidence,
    "package_count": len(packages),
    "formula_policy": (
        "Mining mispricing is measured against 24h/7d historical "
        "hash-work-per-BTC quality. Negative EV penalizes the edge score; "
        "positive EV does not boost it. EDGE cannot modify final_signal."
    ),
}

BUY_FEED.write_text(
    json.dumps(feed, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

print("EDGE SHADOW APPLIED")
print("Packages annotated:", len(packages))
print("Calibration confidence:", confidence)
print("CURRENT production fields were not modified")
for package in packages:
    shadow = package.get("edge_shadow") or {}
    print(
        package.get("name"),
        "| final=", package.get("final_signal"),
        "| edge=", shadow.get("edge_label"),
        "| score=", shadow.get("edge_score"),
    )
