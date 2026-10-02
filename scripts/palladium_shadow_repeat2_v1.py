"""Second coverage-focused repeat harness for the exact frozen Palladium shadow v1 rule.

The signal rule is unchanged. This wrapper isolates new evidence from prior trials
and uses the deployed public NO-KV shadow feed. Collection continuity is an
operational concern only; it cannot alter candidate or confirmation semantics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request

import palladium_shadow_trial as core
from collect_public_market_history import NoRedirect, get_json, snapshot
from enrich_scrypt_market_history import build_economics, timestamp

MANIFEST = Path("research/palladium-shadow-repeat2-v1.json")
HISTORY = Path("calibration/palladium-shadow-repeat2-v1-history.jsonl")
PUBLIC_HISTORY = Path("calibration/palladium-shadow-repeat2-v1-public.jsonl")
STATE = Path("research/palladium-shadow-repeat2-v1-state.json")
REPORT = Path("research/palladium-shadow-repeat2-v1-report.json")
LATEST_ECONOMICS = Path("research/palladium-shadow-repeat2-v1-latest-economics.json")
SHADOW_RELAY = "https://nicehash-easymining-relay.david-e5e.workers.dev/buy-feed-shadow"
EXPECTED_PROTOCOL_HASH = "1fcd3fcd9c45a45a86b781e7423b46a1702cc0d9c1a951beae9947af1993f3a3"
CURRENT = "/main/api/v2/public/stats/global/current/"
WATCHDOG_STALE_SECONDS = 600
_ORIGINAL_MODEL_CODE_DIGEST = core.model_code_digest


def manifest():
    data = json.loads(MANIFEST.read_text())
    required = {
        "shadow_sampling_mode": "NO_KV",
        "shadow_history_access": "NONE",
        "history_status": "HISTORY BYPASSED",
        "history_saved": False,
        "history_key": None,
    }
    collection = data.get("collection") or {}
    if (
        data.get("schemaVersion") != 1
        or data.get("id") != "PALLADIUM_SHADOW_V1_COVERAGE_REPEAT2_2026_10_02"
        or data.get("frozenProtocolHash") != EXPECTED_PROTOCOL_HASH
        or data.get("frozenRuleChanged") is not False
        or data.get("source", {}).get("endpoint") != SHADOW_RELAY
        or data.get("source", {}).get("requiredResponseContract") != required
        or data.get("source", {}).get("cloudflareHistoryKvUsedBySampling") is not False
        or collection.get("trialHours") != 72
        or collection.get("intervalSeconds") != 60
        or collection.get("samplesPerRun") != 31
        or collection.get("targetCoveragePercent") != 90
        or collection.get("targetIsGuarantee") is not False
        or any(data.get(k) is not False for k in (
            "canRaiseSignal", "canSendNotifications", "automaticPurchase",
            "currentProductionModelChanged", "adminApiUsed", "privateApiUsed"
        ))
    ):
        raise ValueError("Invalid repeat2 manifest; refuse collection")
    return data


def repeat_model_code_digest():
    """Bind repeat2 state to exact frozen core + this wrapper + manifest."""
    base = _ORIGINAL_MODEL_CODE_DIGEST()
    wrapper = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    meta = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return core.digest({
        "frozenCoreModelHash": base,
        "repeat2HarnessSha256": wrapper,
        "repeat2ManifestSha256": meta,
        "transport": "PUBLIC_NO_KV_SHADOW",
    })


def configure_core():
    core.HISTORY = HISTORY
    core.PUBLIC_HISTORY = PUBLIC_HISTORY
    core.STATE = STATE
    core.REPORT = REPORT
    core.LATEST_ECONOMICS = LATEST_ECONOMICS
    core.model_code_digest = repeat_model_code_digest


def validate_shadow_feed(data):
    if not isinstance(data, dict):
        raise ValueError("NO_KV_SHADOW_CONTRACT")
    expected = manifest()["source"]["requiredResponseContract"]
    for key, value in expected.items():
        if data.get(key) != value:
            raise ValueError("NO_KV_SHADOW_CONTRACT")
    if data.get("relay_version") != "2.9.0":
        raise ValueError("UNEXPECTED_RELAY_VERSION")
    if data.get("status") not in ("BUY FEED OK", "BUY FEED WARNING"):
        raise ValueError("UNHEALTHY_SHADOW_FEED")
    if not isinstance(data.get("packages"), list):
        raise ValueError("PACKAGE_LIST_MISSING")
    return data


def shadow_feed():
    req = urllib.request.Request(
        SHADOW_RELAY,
        method="GET",
        headers={
            "User-Agent": "NiceHash-Radar-Palladium-Shadow-Repeat2/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
        if response.status != 200 or response.geturl() != SHADOW_RELAY:
            raise RuntimeError("Unexpected NO-KV shadow response")
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Public response too large")
    return validate_shadow_feed(json.loads(raw))


def sampler_factory(clock=core.utcnow):
    metadata = {}

    def sample():
        if not metadata:
            metadata["info"] = get_json("/main/api/v2/public/buy/info/")
            metadata["registry"] = get_json("/main/api/v2/mining/algorithms/")
            metadata["fetchedAt"] = clock().isoformat()
        feed = shadow_feed()
        current = get_json(CURRENT)
        now = clock()
        market = snapshot(metadata["info"], current, metadata["registry"], now.isoformat())
        market["scryptEconomics"] = build_economics(feed, market, now, [market])
        market["scryptEconomics"]["samplingTransport"] = "PUBLIC_NO_KV_SHADOW"
        market["scryptEconomics"]["shadowHistoryAccess"] = "NONE"
        market["registryMetadataFetchedAt"] = metadata["fetchedAt"]
        for algo in market["algorithms"].values():
            contract = algo.get("unitContract")
            if isinstance(contract, dict):
                contract["observedAt"] = metadata["fetchedAt"]
        return market

    return sample


def _load():
    configure_core()
    p = core.load_protocol()
    if core.digest(p) != EXPECTED_PROTOCOL_HASH:
        raise ValueError("Frozen protocol hash changed")
    manifest()
    code = repeat_model_code_digest()
    state = core.load_live_state(p, code)
    return p, code, state


def plan():
    p, code, state = _load()
    current = core.mode(state, core.utcnow())
    result = "complete" if current == "legacy" else current
    print(result)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write("mode=" + result + "\n")
    if state is not None and current == "legacy":
        core.atomic(
            REPORT,
            core.encoded(core.make_report(
                state, core.utcnow(),
                {"automaticExpiry": True, "repeat2Harness": True},
                True,
            )) + b"\n",
        )
    return result


def watchdog():
    p, code, state = _load()
    now = core.utcnow()
    should_dispatch = False
    age = None
    reason = "NO_ACTION"

    if state is None:
        should_dispatch = True
        reason = "NO_PERSISTED_ACTIVATION"
    else:
        current = core.mode(state, now)
        if current == "trial":
            last = timestamp(state.get("lastReceiptAt"))
            if last is None:
                should_dispatch = True
                reason = "TRIAL_WITHOUT_RECEIPT"
            else:
                age = (now - last).total_seconds()
                should_dispatch = age >= WATCHDOG_STALE_SECONDS
                reason = f"LAST_RECEIPT_AGE_SECONDS={age:.1f}"
        elif current == "suspended":
            reason = "SUSPENDED_HTTP_POLICY"
        else:
            reason = "COMPLETE"

    print(json.dumps({
        "shouldDispatch": should_dispatch,
        "reason": reason,
        "lastReceiptAgeSeconds": age,
        "thresholdSeconds": WATCHDOG_STALE_SECONDS,
    }))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write("should_dispatch=" + ("true" if should_dispatch else "false") + "\n")
            handle.write("reason=" + reason + "\n")
    return should_dispatch


def collect(samples, output):
    p, code, state = _load()
    if core.mode(state, core.utcnow()) != "trial":
        raise ValueError("Repeat2 expired or suspended")
    if (
        os.getenv("GITHUB_REF") != "refs/heads/main"
        or os.getenv("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch")
    ):
        raise ValueError("Only main push/dispatch workflow may persist repeat2 evidence")
    return core.collect(
        p, state, code, samples, output, True,
        sampler=sampler_factory(),
    )


def stage():
    p, code, state = _load()
    if state is None:
        raise ValueError("No repeat2 state to stage")
    from radar_snapshot_archive import stage_history
    stage_history(HISTORY)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("plan", "watchdog", "collect", "stage"))
    ap.add_argument("--samples", type=int, default=31)
    ap.add_argument("--output-dir", type=Path, default=Path("/tmp/palladium-shadow-repeat2-v1"))
    args = ap.parse_args()
    if args.command == "plan":
        plan()
    elif args.command == "watchdog":
        watchdog()
    elif args.command == "stage":
        stage()
    else:
        collect(args.samples, args.output_dir)


if __name__ == "__main__":
    main()
