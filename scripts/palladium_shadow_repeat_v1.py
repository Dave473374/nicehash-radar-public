"""High-coverage repeat harness for the frozen Palladium shadow v1 rule.

This wrapper does not alter the v1 decision rule. It isolates repeat evidence
from the completed first trial and samples the deployed public NO-KV relay path.
No account/Admin API, credentials, notification, BUY override or order action.
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

MANIFEST = Path("research/palladium-shadow-repeat-v1.json")
HISTORY = Path("calibration/palladium-shadow-repeat-v1-history.jsonl")
PUBLIC_HISTORY = Path("calibration/palladium-shadow-repeat-v1-public.jsonl")
STATE = Path("research/palladium-shadow-repeat-v1-state.json")
REPORT = Path("research/palladium-shadow-repeat-v1-report.json")
LATEST_ECONOMICS = Path("research/palladium-shadow-repeat-v1-latest-economics.json")
SHADOW_RELAY = "https://nicehash-easymining-relay.david-e5e.workers.dev/buy-feed-shadow"
EXPECTED_PROTOCOL_HASH = "1fcd3fcd9c45a45a86b781e7423b46a1702cc0d9c1a951beae9947af1993f3a3"
CURRENT = "/main/api/v2/public/stats/global/current/"
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
    if (
        data.get("schemaVersion") != 1
        or data.get("id") != "PALLADIUM_SHADOW_V1_HIGH_COVERAGE_REPEAT_2026_09_29"
        or data.get("frozenProtocolHash") != EXPECTED_PROTOCOL_HASH
        or data.get("frozenRuleChanged") is not False
        or data.get("source", {}).get("endpoint") != SHADOW_RELAY
        or data.get("source", {}).get("requiredResponseContract") != required
        or data.get("source", {}).get("cloudflareHistoryKvUsedBySampling") is not False
        or any(data.get(k) is not False for k in (
            "canRaiseSignal", "canSendNotifications", "automaticPurchase",
            "currentProductionModelChanged", "adminApiUsed", "privateApiUsed"
        ))
    ):
        raise ValueError("Invalid repeat manifest; refuse collection")
    return data


def repeat_model_code_digest():
    """Bind repeat state to the frozen core plus this exact harness/manifest."""
    base = _ORIGINAL_MODEL_CODE_DIGEST()
    wrapper = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    meta = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    return core.digest({
        "frozenCoreModelHash": base,
        "repeatHarnessSha256": wrapper,
        "repeatManifestSha256": meta,
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
            "User-Agent": "NiceHash-Radar-Palladium-Shadow-Repeat/1.0",
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


def plan():
    configure_core()
    p = core.load_protocol()
    if core.digest(p) != EXPECTED_PROTOCOL_HASH:
        raise ValueError("Frozen protocol hash changed")
    manifest()
    code = repeat_model_code_digest()
    state = core.load_live_state(p, code)
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
                {"automaticExpiry": True, "repeatHarness": True},
                True,
            )) + b"\n",
        )
    return result


def collect(samples, output):
    configure_core()
    p = core.load_protocol()
    if core.digest(p) != EXPECTED_PROTOCOL_HASH:
        raise ValueError("Frozen protocol hash changed")
    manifest()
    code = repeat_model_code_digest()
    state = core.load_live_state(p, code)
    if core.mode(state, core.utcnow()) != "trial":
        raise ValueError("Repeat expired or suspended")
    if (
        os.getenv("GITHUB_REF") != "refs/heads/main"
        or os.getenv("GITHUB_EVENT_NAME") not in ("push", "schedule", "workflow_dispatch")
    ):
        raise ValueError("Only main production workflow may persist repeat evidence")
    return core.collect(
        p, state, code, samples, output, True,
        sampler=sampler_factory(),
    )


def stage():
    configure_core()
    p = core.load_protocol()
    code = repeat_model_code_digest()
    core.load_live_state(p, code)
    from radar_snapshot_archive import stage_history
    stage_history(HISTORY)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("plan", "collect", "stage"))
    ap.add_argument("--samples", type=int, default=31)
    ap.add_argument("--output-dir", type=Path, default=Path("/tmp/palladium-shadow-repeat-v1"))
    args = ap.parse_args()
    if args.command == "plan":
        plan()
    elif args.command == "stage":
        stage()
    else:
        collect(args.samples, args.output_dir)


if __name__ == "__main__":
    main()
