import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrivateOrderMatchDiagnosticsTests(unittest.TestCase):
    def test_skip_reasons_are_aggregated_by_package_without_order_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"

            orders.write_text(
                json.dumps({
                    "list": [
                        {
                            "startTs": "2026-09-19T10:00:00+00:00",
                            "endTs": "2026-09-19T11:00:00+00:00",
                            "packageName": "Palladium M",
                            "currencyMarket": "BTC",
                            "soloMiningCoin": "LTC",
                            "soloMiningMergeCoin": "DOGE",
                            "isReward": False,
                            "packagePrice": 0.001,
                        },
                        {
                            "startTs": "2026-09-19T12:00:00+00:00",
                            "endTs": "2026-09-19T13:00:00+00:00",
                            "packageName": "Palladium S",
                            "currencyMarket": "BTC",
                            "soloMiningCoin": "LTC",
                            "soloMiningMergeCoin": "DOGE",
                            "isReward": True,
                            "packagePrice": 0.0001,
                        },
                    ]
                }),
                encoding="utf-8",
            )
            radar.write_text("", encoding="utf-8")

            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium M|NO_RADAR_HISTORY_BEFORE_ENTRY"],
                1,
            )
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium S|NO_RADAR_HISTORY_BEFORE_ENTRY"],
                1,
            )
            self.assertEqual(
                result["skipReasonsByPackageOutcome"]["Palladium M|MISS|NO_RADAR_HISTORY_BEFORE_ENTRY"],
                1,
            )
            self.assertEqual(
                result["skipReasonsByPackageOutcome"]["Palladium S|HIT|NO_RADAR_HISTORY_BEFORE_ENTRY"],
                1,
            )
            self.assertNotIn("orderId", json.dumps(result["skipReasonsByPackage"]))



    def test_stale_snapshot_is_distinguished_from_missing_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"
            orders.write_text(json.dumps({"list": [{
                "startTs": "2026-09-19T12:00:00+00:00",
                "endTs": "2026-09-19T13:00:00+00:00",
                "packageName": "Palladium S",
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "soloMiningMergeCoin": "DOGE",
                "isReward": False,
                "packagePrice": 0.0001,
            }]}), encoding="utf-8")
            snapshot = {
                "collected_at": "2026-09-19T11:00:00+00:00",
                "feed": {"checked_at": "2026-09-19T11:00:00+00:00", "packages": []},
            }
            radar.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run([sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                           check=True, capture_output=True, text=True, env=env)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium S|NO_FRESH_RADAR_SNAPSHOT"],
                1,
            )

    def test_currency_mismatch_records_only_aggregate_currency_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"
            orders.write_text(json.dumps({"list": [{
                "startTs": "2026-09-19T12:00:00+00:00",
                "endTs": "2026-09-19T13:00:00+00:00",
                "packageName": "Palladium S",
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "soloMiningMergeCoin": "DOGE",
                "isReward": False,
                "packagePrice": 0.0001,
            }]}), encoding="utf-8")
            snapshot = {
                "collected_at": "2026-09-19T11:55:00+00:00",
                "feed": {
                    "checked_at": "2026-09-19T11:55:00+00:00",
                    "packages": [{
                        "name": "Palladium S",
                        "currency_market": "USDT",
                        "primary_chain": {"currency": "LTC"},
                    }],
                },
            }
            radar.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run([sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                           check=True, capture_output=True, text=True, env=env)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                result["currencyMismatchByPackageOutcome"][
                    "Palladium S|MISS|ORDER_BTC|RADAR_USDT"
                ],
                1,
            )
            self.assertNotIn("startTs", json.dumps(result["currencyMismatchByPackageOutcome"]))

    def test_legacy_282_btc_only_schema_can_match_btc_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"
            orders.write_text(json.dumps({"list": [{
                "startTs": "2026-09-19T12:00:00+00:00",
                "endTs": "2026-09-19T13:00:00+00:00",
                "packageName": "Palladium S",
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "soloMiningMergeCoin": "DOGE",
                "isReward": False,
                "packagePrice": 0.0001,
            }]}), encoding="utf-8")
            snapshot = {
                "collected_at": "2026-09-19T11:55:00+00:00",
                "feed": {
                    "relay_version": "2.8.2",
                    "checked_at": "2026-09-19T11:55:00+00:00",
                    "packages": [{
                        "name": "Palladium S",
                        "size": "S",
                        "price_btc": 0.0001,
                        "primary_chain": {"currency": "LTC"},
                    }],
                },
            }
            radar.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run([sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                           check=True, capture_output=True, text=True, env=env)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["validMatchedOrders"], 1)
            self.assertEqual(
                result["matches"][0]["radarCurrencySource"],
                "LEGACY_2_8_2_BTC_ONLY_SCHEMA",
            )

    def test_missing_currency_is_not_inferred_for_unknown_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"
            orders.write_text(json.dumps({"list": [{
                "startTs": "2026-09-19T12:00:00+00:00",
                "endTs": "2026-09-19T13:00:00+00:00",
                "packageName": "Palladium S",
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "soloMiningMergeCoin": "DOGE",
                "isReward": False,
                "packagePrice": 0.0001,
            }]}), encoding="utf-8")
            snapshot = {
                "collected_at": "2026-09-19T11:55:00+00:00",
                "feed": {
                    "relay_version": "unknown",
                    "checked_at": "2026-09-19T11:55:00+00:00",
                    "packages": [{
                        "name": "Palladium S",
                        "size": "S",
                        "price_btc": 0.0001,
                        "primary_chain": {"currency": "LTC"},
                    }],
                },
            }
            radar.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run([sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                           check=True, capture_output=True, text=True, env=env)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium S|FRESH_RADAR_CURRENCY_MISMATCH"],
                1,
            )

    def test_fresh_snapshot_without_exact_package_is_distinguished(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orders = root / "orders.json"
            radar = root / "radar.jsonl"
            output = root / "matches.json"
            orders.write_text(json.dumps({"list": [{
                "startTs": "2026-09-19T12:00:00+00:00",
                "endTs": "2026-09-19T13:00:00+00:00",
                "packageName": "Palladium S",
                "currencyMarket": "BTC",
                "soloMiningCoin": "LTC",
                "soloMiningMergeCoin": "DOGE",
                "isReward": True,
                "packagePrice": 0.0001,
            }]}), encoding="utf-8")
            snapshot = {
                "collected_at": "2026-09-19T11:55:00+00:00",
                "feed": {
                    "checked_at": "2026-09-19T11:55:00+00:00",
                    "packages": [{
                        "name": "Silver S",
                        "currency_market": "BTC",
                        "primary_chain": {"currency": "BCH"},
                    }],
                },
            }
            radar.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PRIVATE_ORDERS_PATH": str(orders),
                "RADAR_HISTORY_PATH": str(radar),
                "PRIVATE_MATCH_OUTPUT": str(output),
            })
            subprocess.run([sys.executable, str(ROOT / "scripts" / "match_private_orders.py")],
                           check=True, capture_output=True, text=True, env=env)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium S|FRESH_RADAR_NO_EXACT_PACKAGE"],
                1,
            )

if __name__ == "__main__":
    unittest.main(verbosity=2)
