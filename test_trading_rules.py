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
        import logging
        for h in logging.root.handlers[:]:
            h.close()
            logging.root.removeHandler(h)
        for logger_name in logging.Logger.manager.loggerDict:
            lg = logging.getLogger(logger_name)
            for h in getattr(lg, "handlers", [])[:]:
                h.close()
                lg.removeHandler(h)
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

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

    def test_sub_penny_pricing_rule(self):
        from pricing_utils import round_order_price
        
        # Acciones >= $1.00 deben truncar/redondear a exactamente 2 decimales ($0.01)
        self.assertEqual(round_order_price(3.9850), 3.99)
        self.assertEqual(round_order_price(4.5550), 4.56)
        self.assertEqual(round_order_price(1418.312), 1418.31)
        self.assertEqual(round_order_price(1.0001), 1.00)
        
        # Acciones < $1.00 permiten hasta 4 decimales ($0.0001)
        self.assertEqual(round_order_price(0.4562), 0.4562)
        self.assertEqual(round_order_price(0.45625), 0.4563)
        self.assertEqual(round_order_price(0.0053), 0.0053)
        
        # Criptomonedas permiten 4 decimales
        self.assertEqual(round_order_price(1.23456, is_crypto=True), 1.2346)
        
        # Casos inválidos
        self.assertEqual(round_order_price(0.0), 0.0)
        self.assertEqual(round_order_price(None), 0.0)

    def test_resolve_exit_price_fallback_chain(self):
        from pricing_utils import resolve_exit_price
        
        # 1. STOP normal con bid válido
        price = resolve_exit_price("STOP_LOSS_ATR", execution_price=10.0, quote={"bid": 9.80, "price": 9.85}, opp_current_price=9.85, last_known_price=10.0)
        self.assertEqual(price, 9.80)
        
        # 2. STOP en mercado cerrado (bid=0), pero quote tiene price
        price = resolve_exit_price("STOP_LOSS_ATR", execution_price=0.0, quote={"bid": 0.0, "price": 0.285}, opp_current_price=0.285, last_known_price=0.30)
        self.assertEqual(price, 0.285)
        
        # 3. STOP sin quote (bid=0, price=0), pero opp_current_price tiene valor
        price = resolve_exit_price("STOP_LOSS_ATR", execution_price=0.0, quote={"bid": 0.0, "price": 0.0}, opp_current_price=0.35, last_known_price=0.30)
        self.assertEqual(price, 0.35)
        
        # 4. STOP sin cotizaciones vivas, cae al last_known_price
        price = resolve_exit_price("STOP_LOSS_ATR", execution_price=0.0, quote={}, opp_current_price=0.0, last_known_price=0.42)
        self.assertEqual(price, 0.42)
        
        # 5. Todo en cero (desliste absoluto / sin datos previos): debe retornar 0.0 para que se omita la venta
        price = resolve_exit_price("STOP_LOSS_ATR", execution_price=0.0, quote={"bid": 0.0}, opp_current_price=0.0, last_known_price=0.0)
        self.assertEqual(price, 0.0)
        
        # 6. Venta técnica estándar (no STOP): usa execution_price
        price = resolve_exit_price("SEÑAL_TÉCNICA_SELL", execution_price=15.50, quote={"bid": 15.45}, opp_current_price=15.50, last_known_price=15.0)
        self.assertEqual(price, 15.50)

    def test_max_speculative_dollars_cap(self):
        # Cuenta con $100,000. Para un warrant barato (ej: GGROW a $0.005), NUNCA debe asignar más de $500 USD
        rm = RiskManager(starting_capital=100000.0)
        size = rm._calculate_position_size(entry_price=0.005, atr=0.0005, asset_type="warrant")
        cost = size * 0.005
        self.assertLessEqual(cost, 500.01)

    def test_max_speculative_portfolio_exposure(self):
        # Cuenta con $10,000. 10% máximo es $1,000.
        rm = RiskManager(starting_capital=10000.0)
        rm.current_capital = 10000.0
        
        # Con $900 en órdenes especulativas pendientes, debe permitir si no excede los $1000
        rm.pending_speculative_cost = 900.0
        # Pero si ya tiene $1,100 pendientes o abiertos, can_open_position para warrant debe devolver False
        rm.pending_speculative_cost = 1100.0
        can_buy = rm.can_open_position("CYAB", price=0.80, spread_pct=0.01, asset_type="warrant")
        self.assertFalse(can_buy)

    def test_telegram_send_sell_alert_signature_compatibility(self):
        """Verifica que send_sell_alert no lance TypeError con ninguna combinación de argumentos."""
        from telegram_bot import TelegramNotifier
        from unittest.mock import patch
        
        bot = TelegramNotifier()
        with patch.object(bot, 'send_message') as mock_send:
            # 1. Llamada canónica con todos los argumentos
            bot.send_sell_alert(
                symbol="AAPL", entry_price=220.0, exit_price=225.0, qty=10.0,
                pnl=50.0, pnl_pct=0.0227, hold_time=24.5, reason="TAKE_PROFIT",
                atr=3.5, spread=0.001, is_real=True
            )
            self.assertTrue(mock_send.called)
            
            # 2. Llamada alternativa con holding_time y price
            bot.send_sell_alert(
                symbol="MSFT", price=410.0, qty=5.0, pnl=15.0,
                pnl_pct=0.01, holding_time="12.0h", reason="SEÑAL_TÉCNICA", is_real=False
            )
            self.assertEqual(mock_send.call_count, 2)

    def test_partial_position_close(self):
        """Verifica que close_position con quantity_to_close cierre solo la cantidad pedida."""
        rm = RiskManager(starting_capital=10000.0, in_memory=True)
        rm.open_position("AAPL", price=100.0, side="BUY", atr=2.0, spread_pct=0.001, score=8.0, entry_date="2026-11-01T10:00:00", forced_quantity=10.0, bypass_filters=True)
        self.assertEqual(rm.positions["AAPL"][0].quantity, 10.0)
        
        # Cerrar solo 4 acciones a $110 (ganancia de $40)
        pnl = rm.close_position("AAPL", price=110.0, reason="PARTIAL_EXIT", exit_date="2026-11-02T10:00:00", quantity_to_close=4.0)
        self.assertAlmostEqual(pnl, 40.0)
        # Deben quedar 6 acciones en AAPL
        self.assertIn("AAPL", rm.positions)
        self.assertEqual(len(rm.positions["AAPL"]), 1)
        self.assertAlmostEqual(rm.positions["AAPL"][0].quantity, 6.0)

    def test_confirmed_fill_bypasses_filters(self):
        """Verifica que una orden ya confirmada en broker se registre aunque can_open_position falle."""
        rm = RiskManager(starting_capital=1000.0, in_memory=True)
        # Saturar exposición especulativa
        rm.pending_speculative_cost = 5000.0
        # Sin bypass debe ser rechazada
        pos_fail = rm.open_position("CYAB", price=0.80, side="BUY", atr=0.05, spread_pct=0.01, score=7.0, entry_date="2026-11-01T10:00:00", forced_quantity=100.0, bypass_filters=False)
        self.assertIsNone(pos_fail)
        
        # Con bypass (confirmación de broker) DEBE registrarse incondicionalmente
        pos_ok = rm.open_position("CYAB", price=0.80, side="BUY", atr=0.05, spread_pct=0.01, score=7.0, entry_date="2026-11-01T10:00:00", forced_quantity=100.0, bypass_filters=True)
        self.assertIsNotNone(pos_ok)
        self.assertIn("CYAB", rm.positions)

    def test_dry_run_initialization_safety(self):
        """Verifica que TradingBot en dry-run se inicie in_memory y no active live_trading."""
        from main import TradingBot
        bot = TradingBot(dry_run=True)
        self.assertTrue(bot.dry_run)
        self.assertTrue(bot.risk_manager.in_memory)
        self.assertFalse(bot.live_trading)
        self.assertIsNone(bot.alpaca_client)


if __name__ == "__main__":
    unittest.main()
