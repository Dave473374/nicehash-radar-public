import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

passed = 0


def check(condition, label):
    global passed
    if not condition:
        raise AssertionError(label)
    passed += 1
    print(f"PASS {passed:02d}: {label}")


def run_script(script, env):
    merged = os.environ.copy()
    merged.update({k: str(v) for k, v in env.items()})
    result = subprocess.run(
        [PYTHON, str(ROOT / script)],
        cwd=ROOT,
        env=merged,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise AssertionError(f"{script} failed")
    return result


def package(
    name="Palladium S",
    size="S",
    market="BTC",
    algorithm="SCRYPT",
    coin="LTC",
):
    return {
        "name": name,
        "size": size,
        "currency_market": market,
        "price_native": 0.0001 if market == "BTC" else 5.0,
        "price_btc": 0.0001 if market == "BTC" else None,
        "price_btc_equiv": 0.0001,
        "available": True,
        "duration_seconds": 3600,
        "package_hashrate_hps": float(2**32),
        "primary_chain": {
            "currency": coin,
            "algorithm": algorithm,
            "network_difficulty": 1.0,
            "expected_blocks": 1.0,
            "model_hit_probability_percent": 63.2,
        },
        "merge_chain": None,
        "nicehash_odds": {"display": "1:2"},
        "economics": {"package_cost_eur": 7.0},
        "profitability": {
            "expected_return_percent": 98.5,
            "break_even_risk": {"status": "TEST"},
        },
        "history_trend": {
            "expected_blocks_per_btc_vs_24h_percent": 15.0,
            "expected_blocks_per_btc_vs_7d_percent": 10.0,
        },
        "edge_shadow": {
            "edge_score": 12.0,
            "edge_label": "HIGH",
        },
        "math_consistency_shadow": {
            "status": "PASS",
            "production_override": False,
        },
        "final_signal": "BUY NOW",
        "mining_signal": "BUY NOW",
        "economic_signal": "FAIR",
    }


def feed(now, checked_at, history_key, packages):
    return {
        "status": "BUY FEED OK",
        "ok": True,
        "relay_version": "test",
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "history_key": history_key,
        "decision_engine": "TEST",
        "packages": packages,
    }


def market_rows(now, latest_age_minutes=1, complete=True):
    latest = now - timedelta(minutes=latest_age_minutes)
    start = latest - timedelta(hours=10.5)
    rows = []
    for i in range(48):
        ts = start + (latest - start) * i / 47
        algo = {
            "priceRaw": 1.0,
            "orders": 100,
            "speedRaw": 1000.0,
            "rigs": 100,
            "speedUnit": "TH",
        }
        if not complete and i == 47:
            algo.pop("priceRaw")
        rows.append({
            "collected_at": ts.isoformat(),
            "algorithms": {"SCRYPT": algo},
        })
    return rows


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, rows):
    Path(path).write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def run_alert(tmp, f, market, state="state.json", out="out.json"):
    feed_path = tmp / "feed.json"
    market_path = tmp / "market.jsonl"
    state_path = tmp / state
    out_path = tmp / out
    history_path = tmp / "history.jsonl"

    write_json(feed_path, f)
    write_jsonl(market_path, market)

    run_script(
        "scripts/build_alert_events.py",
        {
            "BUY_RADAR_FEED": feed_path,
            "BUY_RADAR_PUBLIC_MARKET_HISTORY": market_path,
            "BUY_RADAR_ALERT_STATE": state_path,
            "BUY_RADAR_ALERT_HISTORY": history_path,
            "BUY_RADAR_ALERT_OUTPUT": out_path,
        },
    )
    return json.loads(out_path.read_text()), (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {}
    )


with tempfile.TemporaryDirectory() as directory:
    tmp = Path(directory)
    now = datetime.now(timezone.utc)

    # Alerts/BATCH: unique snapshots, freshness, MARKET completeness.
    p = package()
    first_feed = feed(
        now,
        now - timedelta(minutes=2),
        "snapshot-A",
        [p],
    )
    ready_market = market_rows(now)

    first, state1 = run_alert(tmp, first_feed, ready_market)
    check(
        not any(
            e.get("eventType") == "BATCH_OPPORTUNITY"
            for e in first.get("events", [])
        ),
        "first qualifying snapshot does not confirm BATCH",
    )

    second_same, state2 = run_alert(
        tmp,
        first_feed,
        ready_market,
        out="same.json",
    )
    check(
        (state2["packages"]["Palladium S"].get("batchCandidateStreak")) == 1,
        "same feed snapshot cannot increment BATCH streak",
    )

    second_feed = feed(
        now,
        now - timedelta(minutes=1),
        "snapshot-B",
        [p],
    )
    second_unique, state3 = run_alert(
        tmp,
        second_feed,
        ready_market,
        out="unique.json",
    )
    check(
        sum(
            e.get("eventType") == "BATCH_OPPORTUNITY"
            for e in second_unique.get("events", [])
        ) == 1,
        "second distinct fresh snapshot confirms one BATCH",
    )

    stale_market = market_rows(now, latest_age_minutes=20)
    stale_out, _ = run_alert(
        tmp,
        feed(now, now - timedelta(minutes=1), "snapshot-C", [p]),
        stale_market,
        state="stale-market-state.json",
        out="stale-market.json",
    )
    stale_event = next(iter(stale_out.get("events", [])), {})
    check(
        (stale_event.get("publicMarket") or {}).get("status") == "STALE"
        and not (stale_event.get("batchOpportunity") or {}).get("candidate"),
        "stale MARKET is fail-closed for BATCH",
    )

    incomplete_market = market_rows(now, complete=False)
    incomplete_out, _ = run_alert(
        tmp,
        feed(now, now - timedelta(minutes=1), "snapshot-D", [p]),
        incomplete_market,
        state="invalid-market-state.json",
        out="invalid-market.json",
    )
    incomplete_event = next(iter(incomplete_out.get("events", [])), {})
    check(
        (incomplete_event.get("publicMarket") or {}).get("status")
        == "INVALID_DATA",
        "incomplete latest MARKET snapshot is INVALID_DATA",
    )

    stale_feed = feed(
        now,
        now - timedelta(minutes=20),
        "snapshot-E",
        [p],
    )
    stale_signal, stale_state = run_alert(
        tmp,
        stale_feed,
        ready_market,
        state="stale-feed-state.json",
        out="stale-feed.json",
    )
    check(
        stale_signal.get("eventCount") == 0
        and "Palladium S" not in stale_state.get("packages", {}),
        "stale feed emits no signal and does not poison dedupe state",
    )

    fresh_after_stale, _ = run_alert(
        tmp,
        feed(now, now - timedelta(minutes=1), "snapshot-F", [p]),
        ready_market,
        state="stale-feed-state.json",
        out="fresh-after-stale.json",
    )
    check(
        any(e.get("eventType") == "SIGNAL" for e in fresh_after_stale["events"]),
        "fresh signal still alerts after stale copy was ignored",
    )

    future_feed = feed(
        now,
        now + timedelta(minutes=5),
        "snapshot-G",
        [p],
    )
    future_out, _ = run_alert(
        tmp,
        future_feed,
        ready_market,
        state="future-state.json",
        out="future.json",
    )
    check(
        future_out.get("eventCount") == 0,
        "future-dated feed cannot alert",
    )

    primary = package(name="Palladium S", size="S")
    secondary = package(name="Palladium M", size="M")
    sorted_out, _ = run_alert(
        tmp,
        feed(
            now,
            now - timedelta(minutes=1),
            "snapshot-H",
            [secondary, primary],
        ),
        ready_market,
        state="sort-state.json",
        out="sort.json",
    )
    signal_events = [
        e for e in sorted_out.get("events", [])
        if e.get("eventType") == "SIGNAL"
    ]
    check(
        signal_events[0].get("purchasePriority") == "PRIMARY",
        "serialized alert output sorts PRIMARY before SECONDARY",
    )

    # Math consistency shadow.
    exact = package(name="Exact")
    warning = package(name="Warning")
    critical = package(name="Critical")
    for item in (exact, warning, critical):
        item["duration_seconds"] = 1
    warning["primary_chain"]["expected_blocks"] = 1.10
    critical["primary_chain"]["expected_blocks"] = 1.20

    math_feed = feed(
        now,
        now - timedelta(minutes=1),
        "math",
        [exact, warning, critical],
    )
    math_path = tmp / "math-feed.json"
    write_json(math_path, math_feed)
    run_script(
        "scripts/apply_math_consistency_shadow.py",
        {"BUY_RADAR_FEED": math_path},
    )
    math_result = json.loads(math_path.read_text())
    math_by_name = {p["name"]: p for p in math_result["packages"]}

    check(
        math_by_name["Exact"]["math_consistency_shadow"]["status"] == "PASS",
        "difficulty consistency exact case passes",
    )
    check(
        math_by_name["Warning"]["math_consistency_shadow"]["status"]
        == "WARNING",
        "10% expected-block disagreement is WARNING",
    )
    check(
        math_by_name["Critical"]["math_consistency_shadow"]["status"]
        == "CRITICAL",
        "20% expected-block disagreement is CRITICAL",
    )

    # Private entry-time matching.
    entry = now - timedelta(hours=1)
    snapshot_time = entry - timedelta(minutes=5)

    usdt_package = package(
        name="Silver 5",
        size="5",
        market="USDT",
        algorithm="SHA256ASICBOOST_USDT",
        coin="BCH",
    )
    usdt_package["price_native"] = 5.0
    usdt_package["price_btc_equiv"] = 0.00007

    btc_package = package(
        name="Palladium S",
        size="S",
        market="BTC",
        algorithm="SCRYPT",
        coin="LTC",
    )

    snapshot = {
        "collected_at": snapshot_time.isoformat(),
        "feed_generated_at": snapshot_time.isoformat(),
        "feed": {
            "checked_at": snapshot_time.isoformat(),
            "packages": [usdt_package, btc_package],
        },
    }

    radar_path = tmp / "radar.jsonl"
    write_jsonl(radar_path, [snapshot])

    def ts(minutes):
        return (entry + timedelta(minutes=minutes)).isoformat()

    private_orders = {
        "list": [
            {
                "startTs": ts(0),
                "endTs": ts(60),
                "packageName": "Silver 5",
                "packagePrice": 5.0,
                "payedAmount": 5.0,
                "currencyMarket": "USDT",
                "soloMiningCoin": "BCH",
                "isReward": True,
                "soloReward": [{"payoutRewardBtc": 0.00014}],
            },
            {
                "startTs": ts(1),
                "endTs": ts(61),
                "packageName": "Silver 5",
                "packagePrice": 5.0,
                "payedAmount": 5.0,
                "currencyMarket": "USDT",
                "soloMiningCoin": "BCH",
                "isReward": None,
                "soloReward": [],
            },
            {
                "startTs": ts(2),
                "endTs": ts(62),
                "packageName": "Silver 5",
                "packagePrice": 5.0,
                "payedAmount": 5.0,
                "currencyMarket": "USDT",
                "soloMiningCoin": "BCH",
                "isReward": True,
                "soloReward": [],
            },
            {
                "startTs": ts(3),
                "endTs": ts(63),
                "packageName": "Silver 5",
                "packagePrice": 5.0,
                "payedAmount": 0.0,
                "currencyMarket": "USDT",
                "soloMiningCoin": "BCH",
                "isReward": False,
                "soloReward": [],
            },
            {
                "startTs": ts(4),
                "endTs": ts(64),
                "packageName": "Silver 5",
                "packagePrice": 0.0001,
                "payedAmount": 0.0001,
                "currencyMarket": "BTC",
                "soloMiningCoin": "BCH",
                "isReward": False,
                "soloReward": [],
            },
            {
                "startTs": ts(30),
                "endTs": ts(90),
                "packageName": "Silver 5",
                "packagePrice": 5.0,
                "payedAmount": 5.0,
                "currencyMarket": "USDT",
                "soloMiningCoin": "BCH",
                "isReward": False,
                "soloReward": [],
            },
            {
                "startTs": ts(5),
                "endTs": ts(65),
                "packageName": "Palladium S",
                "packagePrice": 0.0001,
                "payedAmount": 0.00009,
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "isReward": False,
                "soloReward": [],
            },
        ]
    }

    private_path = tmp / "orders.json"
    private_out = tmp / "private-match.json"
    write_json(private_path, private_orders)

    run_script(
        "scripts/match_private_orders.py",
        {
            "PRIVATE_ORDERS_PATH": private_path,
            "RADAR_HISTORY_PATH": radar_path,
            "PRIVATE_MATCH_OUTPUT": private_out,
        },
    )
    private_result = json.loads(private_out.read_text())
    rows = private_result["matches"]

    first_usdt = rows[0]
    check(
        abs(first_usdt["actualCostBtcEquivalent"] - 0.00007) < 1e-12,
        "USDT cost is converted to BTC-equivalent instead of treated as BTC",
    )
    check(
        private_result["overall"]["unknownOutcomes"] == 1,
        "unknown isReward remains UNKNOWN and is excluded from hit-rate denominator",
    )
    hit_no_payout = next(
        row for row in rows
        if row["outcome"] == "HIT" and row["realizedReturnBtc"] is None
    )
    check(
        hit_no_payout["roiAvailable"] is False,
        "HIT with missing payout has UNKNOWN ROI, not -100%",
    )
    zero_paid = next(
        row for row in rows
        if row.get("costSource") == "PAYED_AMOUNT_NONPOSITIVE"
    )
    check(
        zero_paid["actualCostBtcEquivalent"] is None,
        "explicit zero payedAmount is unknown cost, not nominal-price fallback",
    )
    check(
        private_result["skipReasons"].get("FRESH_RADAR_CURRENCY_MISMATCH") == 1
        and private_result["skipReasons"].get("NO_FRESH_RADAR_SNAPSHOT") == 1,
        "currency mismatch and stale entry snapshot are not force-matched",
    )
    btc_row = next(
        row for row in rows
        if row["packageName"] == "Palladium S"
    )
    check(
        abs(btc_row["actualCostBtcEquivalent"] - 0.00009) < 1e-12
        and btc_row["outcome"] == "MISS"
        and btc_row["roiAvailable"] is True,
        "fresh BTC MISS keeps paid BTC cost and known zero payout",
    )

    # Reward-time evidence must never become entry evidence.
    event_snap = {
        "collected_at": snapshot_time.isoformat(),
        "feed": {"packages": [btc_package]},
    }
    event_radar = tmp / "event-radar.jsonl"
    write_jsonl(event_radar, [event_snap])

    event_fresh_ms = int(
        (snapshot_time + timedelta(minutes=5)).timestamp() * 1000
    )
    event_stale_ms = int(
        (snapshot_time + timedelta(minutes=30)).timestamp() * 1000
    )
    event_input = tmp / "events.json"
    write_json(
        event_input,
        [
            {
                "eventId": "fresh",
                "packageId": "1",
                "time": event_fresh_ms,
                "packageName": "Palladium S",
                "coins": ["LTC"],
                "rewardCount": 1,
                "mergedMining": False,
                "totalPayoutRewardBtc": 0.001,
                "rewards": [],
            },
            {
                "eventId": "stale",
                "packageId": "2",
                "time": event_stale_ms,
                "packageName": "Palladium S",
                "coins": ["LTC"],
                "rewardCount": 1,
                "mergedMining": False,
                "totalPayoutRewardBtc": 0.001,
                "rewards": [],
            },
        ],
    )
    event_out = tmp / "event-out.jsonl"
    run_script(
        "scripts/match_mining_events.py",
        {
            "RADAR_SNAPSHOTS_FILE": event_radar,
            "MINING_EVENTS_FILE": event_input,
            "MINING_EVENT_MATCH_OUTPUT": event_out,
        },
    )
    event_rows = [
        json.loads(line)
        for line in event_out.read_text().splitlines()
        if line.strip()
    ]
    fresh_event = next(row for row in event_rows if row["event_id"] == "fresh")
    stale_event = next(row for row in event_rows if row["event_id"] == "stale")

    check(
        fresh_event["evidence_role"] == "REWARD_TIME_CONTEXT_ONLY"
        and fresh_event["entry_time_eligible"] is False
        and fresh_event["can_supply_miss_denominator"] is False,
        "reward event is explicitly context-only, never entry/MISS evidence",
    )
    check(
        stale_event["radar_matched"] is False,
        "reward-time context older than 15 minutes is not matched",
    )

    # Security/data-minimization regressions.
    private_api_code = (
        ROOT / "scripts/nicehash_private_readonly.py"
    ).read_text(encoding="utf-8")
    shared_code = (
        ROOT / "scripts/collect_shared_packages.py"
    ).read_text(encoding="utf-8")

    check(
        "allow_redirects=False" in private_api_code,
        "signed private API requests explicitly reject redirects",
    )
    check(
        'row.get("isPublic") is True' in shared_code,
        "shared collector persists only explicitly public rows",
    )

assert passed >= 18, f"Expected at least 18 scenarios, got {passed}"
print(f"PHASE 0 CORRECTNESS REGRESSION: PASS ({passed} scenarios)")
