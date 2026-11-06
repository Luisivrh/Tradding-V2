import os
import tempfile
import unittest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

import config
from risk_management import RiskManager, Position
from technical_analysis import SignalAnalyzer, TechnicalAnalyzer


class TradingRulesValidationTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmpdir.cleanup()

    def _create_mock_df(self, count=50, base_price=10.0, trend="up"):
        dates = pd.date_range(end=datetime.now(), periods=count, freq="D")
        closes = []
        p = base_price
        for i in range(count):
            if trend == "up":
                p += 0.2
            elif trend == "down":
                p -= 0.2
            closes.append(p)
            
        df = pd.DataFrame({
            "Open": [c - 0.1 for c in closes],
            "High": [c + 0.2 for c in closes],
            "Low": [c - 0.2 for c in closes],
            "Close": closes,
            "Volume": [1000000] * count
        }, index=dates)
        return df

    def test_speculative_buy_signal_not_blocked_by_calibration(self):
        # Generar datos con volumen alto y precio creciente para penny stock
        df = self._create_mock_df(count=30, base_price=1.5, trend="up")
        df.iloc[-1, df.columns.get_loc("Volume")] = 5000000 # Volumen relativo 5x
        
        analyzer = SignalAnalyzer(df, "MERC", asset_type="penny", source="speculative")
        action, score, details = analyzer.evaluate_signal()
        
        # Debe ser evaluada con el motor especulativo
        self.assertTrue(analyzer.is_speculative)
        self.assertFalse(details.get("is_calibration", False))
        self.assertFalse(config.SPECULATIVE_CONFIG.get("calibration_mode", True))

    def test_dust_position_does_not_block_new_buy(self):
        rm = RiskManager(starting_capital=50000)
        # Simular que TSLA tiene solo 0.20 acciones (dust de $72)
        rm.positions["TSLA"] = [
            Position(
                symbol="TSLA",
                asset_type="normal",
                entry_price=360.0,
                entry_date=(datetime.now() - timedelta(days=5)).isoformat(),
                side="BUY",
                quantity=0.20,
                stop_loss=340.0,
                take_profit=420.0,
                atr_at_entry=5.0,
                spread_at_entry=0.001
            )
        ]
        
        # can_open_position debe detectar el residuo y permitir comprar
        can_buy = rm.can_open_position("TSLA", price=365.0, spread_pct=0.001, asset_type="normal")
        self.assertTrue(can_buy)

    def test_category_specific_max_hold_timeout(self):
        rm = RiskManager(starting_capital=50000)
        # Penny stock con 4 días de antigüedad (límite penny es 3 días)
        old_date = (datetime.now() - timedelta(days=4)).isoformat()
        rm.positions["CRIS"] = [
            Position(
                symbol="CRIS",
                asset_type="penny",
                entry_price=1.19,
                entry_date=old_date,
                side="BUY",
                quantity=1000,
                stop_loss=0.80,
                take_profit=1.43,
                atr_at_entry=0.10,
                spread_at_entry=0.005
            )
        ]
        
        current_prices = {"CRIS": {"price": 1.15, "atr": 0.10, "spread": 0.005}}
        forced = rm.check_exit_conditions(current_prices)
        self.assertEqual(len(forced), 1)
        self.assertIn("MAX_TIME", forced[0]["details"]["reason"])

    def test_category_specific_take_profit(self):
        rm = RiskManager(starting_capital=50000)
        pos = rm.open_position(
            symbol="SLGB",
            price=0.30,
            side="BUY",
            atr=0.02,
            spread_pct=0.001,
            score=8.5,
            entry_date=datetime.now().isoformat()
        )
        self.assertIsNotNone(pos)
        # El take profit debe ser +20% (0.36)
        expected_tp = 0.30 * 1.20
        self.assertAlmostEqual(pos.take_profit, expected_tp, places=4)


if __name__ == "__main__":
    unittest.main()
