"""
Pruebas del modo dry-run:
- RiskManager(in_memory=True, load_snapshot=True) carga portfolio.json en solo lectura.
- Nunca escribe portfolio.json ni audit_log.json.
- El backtester (in_memory sin snapshot) sigue arrancando vacío.
- execute_trades en dry-run no envía alertas de compra a Telegram.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from risk_management import RiskManager


SAMPLE_PORTFOLIO = {
    "starting_capital": 100000.0,
    "current_capital": 90000.0,
    "forex_starting_capital": 10000.0,
    "forex_capital": 10000.0,
    "positions": {
        "AAPL": [{
            "symbol": "AAPL", "asset_type": "normal", "entry_price": 200.0,
            "entry_date": "2026-11-10 10:15:00", "side": "BUY", "quantity": 10.0,
            "stop_loss": 190.0, "take_profit": 240.0, "atr_at_entry": 5.0,
            "spread_at_entry": 0.001, "highest_price": 200.0, "lowest_price": 200.0,
            "last_known_price": 200.0, "entry_score": 7.0
        }]
    },
    "trade_history": []
}


class TestDryRunSnapshot(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        with open("portfolio.json", "w", encoding="utf-8") as f:
            json.dump(SAMPLE_PORTFOLIO, f)
        self._original_bytes = Path("portfolio.json").read_bytes()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_snapshot_loads_positions_read_only(self):
        rm = RiskManager(100000, in_memory=True, load_snapshot=True)
        self.assertIn("AAPL", rm.positions)
        self.assertEqual(rm.current_capital, 90000.0)

        # Operar en memoria no debe tocar disco
        rm.close_position("AAPL", 210.0, "TEST", "2026-11-16 12:00:00")
        rm.open_position("MSFT", 400.0, "BUY", 8.0, 0.001, 7.5, "2026-11-16 12:00:00", forced_quantity=2, bypass_filters=True)
        rm.save_portfolio()

        self.assertNotIn("AAPL", rm.positions)
        self.assertEqual(Path("portfolio.json").read_bytes(), self._original_bytes)
        self.assertFalse(os.path.exists("audit_log.json"))

    def test_backtester_mode_starts_empty(self):
        rm = RiskManager(100000, in_memory=True)
        self.assertEqual(rm.positions, {})
        self.assertEqual(rm.current_capital, 100000)
        self.assertFalse(os.path.exists("audit_log.json"))


class TestDryRunNoTelegramBuyAlert(unittest.TestCase):
    def test_simulated_buy_does_not_alert_in_dry_run(self):
        import main

        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.object(main, "AIAnalyzer"), patch.object(main, "PostMortemAnalyzer"), \
                     patch.object(main, "DataFetcher"), patch.object(main, "TelegramNotifier"):
                    bot = main.TradingBot(dry_run=True)
                bot.telegram = MagicMock()
                opp = {
                    "symbol": "AAPL", "signal_type": "BUY", "score": 8.0, "current_price": 200.0,
                    "details": {"atr": 4.0, "asset_type": "normal", "price": 200.0},
                }
                trades = bot.execute_trades([opp])

                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0]["status"], "SIMULADO")
                bot.telegram.send_buy_alert.assert_not_called()
                self.assertFalse(os.path.exists("portfolio.json"))
                self.assertFalse(os.path.exists("audit_log.json"))
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
