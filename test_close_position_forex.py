"""Regression: closing a forex trade must restore forex cash, not Alpaca cash."""

import os
import tempfile
import unittest

import config
from risk_management import RiskManager


class ClosePositionForexCapitalTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmpdir.cleanup()

    def test_forex_close_credits_forex_capital_not_alpaca(self):
        rm = RiskManager(starting_capital=100_000)
        alpaca_cash_before = rm.current_capital
        forex_cash_before = rm.forex_capital
        symbol = config.ASSETS["forex"][0]
        price = 1.10
        atr = 0.01

        position = rm.open_position(
            symbol=symbol,
            price=price,
            side="BUY",
            atr=atr,
            spread_pct=0.001,
            score=8.0,
            entry_date="2026-11-01T12:00:00",
        )
        self.assertIsNotNone(position)
        cost = position.quantity * price
        self.assertAlmostEqual(rm.forex_capital, forex_cash_before - cost, places=6)
        self.assertEqual(rm.current_capital, alpaca_cash_before)

        exit_price = price * 1.01
        pnl = rm.close_position(
            symbol=symbol,
            price=exit_price,
            reason="TAKE_PROFIT",
            exit_date="2026-11-02T12:00:00",
        )

        expected_revenue = position.quantity * exit_price
        self.assertAlmostEqual(pnl, expected_revenue - cost, places=6)
        self.assertAlmostEqual(rm.forex_capital, forex_cash_before + (expected_revenue - cost), places=6)
        self.assertEqual(rm.current_capital, alpaca_cash_before)
        self.assertNotIn(symbol, rm.positions)

    def test_old_microstructure_lookup_never_detects_forex(self):
        """Documents the broken comparison that used to skip forex cash."""
        rm = RiskManager(starting_capital=100_000)
        symbol = config.ASSETS["forex"][0]
        position = rm.open_position(
            symbol=symbol,
            price=1.10,
            side="BUY",
            atr=0.01,
            spread_pct=0.001,
            score=8.0,
            entry_date="2026-11-01T12:00:00",
        )
        self.assertIsNotNone(position)
        asset_type = rm.positions[symbol][0].asset_type
        self.assertEqual(asset_type, "forex")
        self.assertNotEqual(config.MICROSTRUCTURE_CONFIG.get(asset_type), "forex")
        self.assertIsInstance(config.MICROSTRUCTURE_CONFIG.get(asset_type), dict)


if __name__ == "__main__":
    unittest.main()
