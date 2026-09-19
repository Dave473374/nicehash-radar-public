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
                result["skipReasonsByPackage"]["Palladium M|NO_FRESH_RADAR_MATCH"],
                1,
            )
            self.assertEqual(
                result["skipReasonsByPackage"]["Palladium S|NO_FRESH_RADAR_MATCH"],
                1,
            )
            self.assertNotIn("orderId", json.dumps(result["skipReasonsByPackage"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
