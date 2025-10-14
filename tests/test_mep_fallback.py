import json
import unittest
from unittest.mock import patch

import server
from lib.tools import market_data


class FetchBondQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "test-user"

    def test_records_original_settlement_metadata(self):
        """Successful fetch records settlement metadata using requested CI/T0."""

        def fake_get_market_data(symbol, entries=None, depth=1, settlement="CI", user_id="anonymous"):
            payload = {
                "success": True,
                "symbol": symbol,
                "market_data": {"data": {"bid": {"price": 855.8, "size": 1}, "offer": {"price": 856.1, "size": 1}}},
            }
            return json.dumps(payload)

        with patch("lib.tools.market_data.get_market_data", side_effect=fake_get_market_data):
            ars_result, usd_result = market_data._fetch_bond_quotes_for_mep("AL30", "CI", self.user_id)

        self.assertTrue(ars_result["success"])
        self.assertTrue(usd_result["success"])
        self.assertEqual(usd_result["_meta"]["settlement_used"], "T0")

    def test_aggregates_errors_when_all_attempts_fail(self):
        """Failure message lists the attempted settlements for easier debugging."""

        def always_fail(symbol, entries=None, depth=1, settlement="CI", user_id="anonymous"):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

        with patch("lib.tools.market_data.get_market_data", side_effect=always_fail), \
                patch("lib.tools.market_data._require_auth", return_value=(False, "skip", None)):
            _, usd_result = market_data._fetch_bond_quotes_for_mep("AL30", "CI", self.user_id)

        self.assertFalse(usd_result["success"])
        self.assertIn("AL30D@T0", usd_result["error"])


if __name__ == "__main__":
    unittest.main()
