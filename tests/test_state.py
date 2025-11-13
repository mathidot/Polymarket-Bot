import os
import importlib
import unittest

class TestState(unittest.TestCase):
    def setUp(self):
        os.environ["trade_unit"] = "1.0"
        os.environ["slippage_tolerance"] = "0.01"
        os.environ["pct_profit"] = "0.02"
        os.environ["pct_loss"] = "-0.02"
        os.environ["cash_profit"] = "1.0"
        os.environ["cash_loss"] = "-1.0"
        os.environ["spike_threshold"] = "0.02"
        os.environ["sold_position_time"] = "60"
        os.environ["YOUR_PROXY_WALLET"] = "0x0000000000000000000000000000000000000000"
        os.environ["BOT_TRADER_ADDRESS"] = "0x0000000000000000000000000000000000000001"
        os.environ["USDC_CONTRACT_ADDRESS"] = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        os.environ["POLYMARKET_SETTLEMENT_CONTRACT"] = "0x56C79347e95530c01A2FC76E732f9566dA16E113"
        os.environ["PK"] = "0xdeadbeef"
        os.environ["holding_time_limit"] = "3600"
        os.environ["max_concurrent_trades"] = "3"
        os.environ["min_liquidity_requirement"] = "10.0"
        os.environ["price_lower_bound"] = "0.20"
        os.environ["price_upper_bound"] = "0.80"
        import polymarket_bot.config as cfg
        importlib.reload(cfg)

    def test_price_history_append(self):
        from polymarket_bot.state import ThreadSafeState
        state = ThreadSafeState(max_price_history_size=3, keep_min_shares=0)
        state.add_price("A", 0.0, 0.30, "event", "outcome")
        state.add_price("A", 1.0, 0.40, "event", "outcome")
        hist = state.get_price_history("A")
        self.assertEqual(len(hist), 2)
        self.assertAlmostEqual(hist[-1][1], 0.40)

if __name__ == "__main__":
    unittest.main()
