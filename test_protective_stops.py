"""
Pruebas de la opción A (stops de protección en Alpaca) y del planificador del modo auto.
Todo con mocks: no hay red ni órdenes reales.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from order_manager import OrderManager, KIND_PROTECTIVE_STOP
from protective_stops import ProtectiveStopManager
from risk_management import RiskManager
from scheduler import decide_tasks


def _order(order_id, status="new", filled_qty=0, filled_avg_price=None, **extra):
    return SimpleNamespace(id=order_id, status=status, filled_qty=filled_qty,
                           filled_avg_price=filled_avg_price, filled_at=None, **extra)


class _Base(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.rm = RiskManager(100000, in_memory=True)
        self.om = OrderManager(state_file="pending_orders.json")
        self.alpaca = MagicMock()
        self.alpaca.get_orders.return_value = []
        self.alpaca.submit_order.side_effect = lambda order_data: _order("stop-1")
        self.sm = ProtectiveStopManager(self.alpaca, self.om, self.rm)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def add_position(self, symbol="AAPL", qty=10.6, price=200.0, stop=190.123, last=200.0):
        pos = self.rm.open_position(symbol, price, "BUY", 5.0, 0.001, 7.0, "2026-11-16 15:45:00",
                                    forced_quantity=qty, bypass_filters=True)
        pos.stop_loss = stop
        pos.last_known_price = last
        return pos


class TestPlaceAndRaise(_Base):
    def test_places_gtc_stop_with_whole_shares(self):
        self.add_position()
        summary = self.sm.ensure_all()

        self.assertEqual(summary["placed"], ["AAPL"])
        req = self.alpaca.submit_order.call_args.kwargs["order_data"]
        self.assertEqual(req.qty, 10)                 # 10.6 -> 10 acciones enteras
        self.assertEqual(req.stop_price, 190.12)      # sub-penny: 2 decimales
        self.assertEqual(str(req.time_in_force.value), "gtc")
        self.assertEqual(str(req.side.value), "sell")
        tracked = self.om.get_protective_orders("AAPL")
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["kind"], KIND_PROTECTIVE_STOP)

    def test_no_stop_for_crypto_forex_or_fractional(self):
        self.add_position("BTC-USD", qty=0.5, price=60000, stop=55000, last=60000)
        self.add_position("EURUSD=X", qty=1000, price=1.1, stop=1.05, last=1.1)
        self.add_position("TSLA", qty=0.4, price=350, stop=330, last=350)
        self.sm.ensure_all()
        self.alpaca.submit_order.assert_not_called()

    def test_no_stop_when_already_above_price(self):
        self.add_position(stop=205.0, last=200.0)
        self.sm.ensure_all()
        self.alpaca.submit_order.assert_not_called()

    def test_raises_stop_when_trailing_moves_up(self):
        pos = self.add_position()
        self.sm.ensure_all()
        self.alpaca.replace_order_by_id.return_value = _order("stop-2")

        pos.stop_loss = 195.0
        summary = self.sm.ensure_all()

        self.assertEqual(summary["raised"], ["AAPL"])
        old_id, req = self.alpaca.replace_order_by_id.call_args.args
        self.assertEqual(old_id, "stop-1")
        self.assertEqual(req.stop_price, 195.0)
        tracked = self.om.get_protective_orders("AAPL")
        self.assertEqual([o["order_id"] for o in tracked], ["stop-2"])

    def test_never_lowers_and_ignores_tiny_moves(self):
        pos = self.add_position()
        self.sm.ensure_all()
        pos.stop_loss = 190.2   # +0.04%: debajo del umbral de 0.2%
        self.sm.ensure_all()
        pos.stop_loss = 180.0   # bajar: nunca
        self.sm.ensure_all()
        self.alpaca.replace_order_by_id.assert_not_called()

    def test_qty_change_replaces_order_keeping_stop(self):
        pos = self.add_position()
        self.sm.ensure_all()
        self.alpaca.replace_order_by_id.return_value = _order("stop-2")
        pos.quantity = 15.0
        self.sm.ensure_all()
        _, req = self.alpaca.replace_order_by_id.call_args.args
        self.assertEqual(req.qty, 15)
        self.assertEqual(req.stop_price, 190.12)

    def test_skips_when_bot_sell_is_pending(self):
        self.add_position()
        self.om.add_order("sell-1", "c", "AAPL", "SELL", 10, 201.0)
        self.sm.ensure_all()
        self.alpaca.submit_order.assert_not_called()

    def test_adopts_existing_stop_instead_of_duplicating(self):
        self.add_position()
        existing = SimpleNamespace(id="stop-legacy", symbol="AAPL", side="OrderSide.SELL", order_type="OrderType.STOP",
                                   client_order_id="TR2_STOP_AAPL_20261116", qty="10", stop_price="190.12")
        self.alpaca.get_orders.return_value = [existing]
        self.sm.ensure_all()
        self.alpaca.submit_order.assert_not_called()
        self.assertEqual([o["order_id"] for o in self.om.get_protective_orders("AAPL")], ["stop-legacy"])


class TestCancelAndReconcile(_Base):
    def test_protective_stop_does_not_block_bot_sell(self):
        self.add_position()
        self.sm.ensure_all()
        self.assertFalse(self.om.has_pending_order("AAPL", "SELL"))
        self.assertTrue(self.om.has_pending_order("AAPL", "SELL", include_protective=True))

    def test_cancel_frees_shares(self):
        self.add_position()
        self.sm.ensure_all()
        self.alpaca.get_order_by_id.return_value = _order("stop-1", status="OrderStatus.CANCELED")
        self.assertEqual(self.sm.cancel_for_symbol("AAPL"), "canceled")
        self.assertEqual(self.om.get_protective_orders("AAPL"), [])

    def test_stop_filled_before_cancel_is_reported_and_reconciled(self):
        self.add_position(qty=10)
        self.sm.ensure_all()
        filled = _order("stop-1", status="OrderStatus.FILLED", filled_qty=10, filled_avg_price=189.9)
        self.alpaca.get_order_by_id.return_value = filled

        self.assertEqual(self.sm.cancel_for_symbol("AAPL"), "filled")
        self.assertIn("AAPL", self.rm.positions)  # el bot NO vende

        # La reconciliación registra la venta hecha por Alpaca
        self.om.reconcile_orders(self.alpaca, self.rm, telegram_bot=None)
        self.assertNotIn("AAPL", self.rm.positions)
        self.assertEqual(self.om.pending_orders, {})
        self.assertIn("STOP_LOSS_BROKER", self.rm.trade_history[-1]["reason"])
        self.assertAlmostEqual(self.rm.trade_history[-1]["exit_price"], 189.9)


class TestSellPathIntegration(unittest.TestCase):
    """execute_trades debe cancelar el stop de Alpaca antes de vender, y no vender si ya se ejecutó."""

    def _bot(self, cancel_result):
        import main
        with patch.object(main, "AIAnalyzer"), patch.object(main, "PostMortemAnalyzer"), \
             patch.object(main, "DataFetcher"), patch.object(main, "TelegramNotifier"):
            bot = main.TradingBot(dry_run=True)
        bot.dry_run = False
        bot.live_trading = True
        bot.alpaca_client = MagicMock()
        bot.alpaca_client.get_orders.return_value = []
        bot.alpaca_client.close_position.return_value = _order("close-1", status="OrderStatus.ACCEPTED")
        bot.order_manager = OrderManager(state_file="pending_orders.json")
        bot.stop_manager = MagicMock()
        bot.stop_manager.cancel_for_symbol.return_value = cancel_result
        bot.risk_manager.open_position("AAPL", 200.0, "BUY", 5.0, 0.001, 7.0, "2026-11-16 15:45:00",
                                       forced_quantity=10, bypass_filters=True)
        return bot

    def _sell(self, bot):
        opp = {"symbol": "AAPL", "signal_type": "SELL", "score": 0.0, "current_price": 185.0,
               "details": {"reason": "STOP_LOSS_ATR (Trigger en $190)", "price": 185.0, "atr": 5.0}}
        return bot.execute_trades([opp])

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_skips_sell_when_broker_stop_already_filled(self):
        bot = self._bot("filled")
        self._sell(bot)
        bot.stop_manager.cancel_for_symbol.assert_called_once_with("AAPL")
        bot.alpaca_client.close_position.assert_not_called()
        bot.alpaca_client.submit_order.assert_not_called()

    def test_sells_after_stop_canceled(self):
        bot = self._bot("canceled")
        self._sell(bot)
        bot.alpaca_client.close_position.assert_called_once_with("AAPL")


class TestExitMonitorTrailing(unittest.TestCase):
    def test_monitor_does_not_move_trailing(self):
        rm = RiskManager(100000, in_memory=True)
        pos = rm.open_position("AAPL", 200.0, "BUY", 5.0, 0.001, 7.0, "2026-11-16 15:45:00",
                               forced_quantity=10, bypass_filters=True)
        sl_before = pos.stop_loss
        rm.check_exit_conditions({"AAPL": {"price": 215.0, "atr": 5.0}}, update_trailing=False)
        self.assertEqual(pos.stop_loss, sl_before)
        self.assertEqual(pos.highest_price, 200.0)
        rm.check_exit_conditions({"AAPL": {"price": 215.0, "atr": 5.0}}, update_trailing=True)
        self.assertGreater(pos.stop_loss, sl_before)


class TestScheduler(unittest.TestCase):
    @staticmethod
    def utc(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)

    def test_stocks_scan_near_close_winter_est(self):
        now = self.utc(2026, 11, 17, 20, 45)   # 15:45 EST (martes)
        t = decide_tasks(now, True, now + timedelta(minutes=15), {})
        self.assertTrue(t["stocks_scan"])
        self.assertFalse(t["monitor_stocks"])

    def test_stocks_scan_near_close_summer_edt(self):
        now = self.utc(2026, 7, 14, 19, 45)    # 15:45 EDT
        t = decide_tasks(now, True, now + timedelta(minutes=15), {})
        self.assertTrue(t["stocks_scan"])

    def test_morning_is_monitor_only(self):
        now = self.utc(2026, 11, 17, 15, 15)   # 10:15 EST
        t = decide_tasks(now, True, now + timedelta(hours=6), {})
        self.assertFalse(t["stocks_scan"])
        self.assertTrue(t["monitor_stocks"])

    def test_scan_only_once_per_day(self):
        now = self.utc(2026, 11, 17, 20, 50)
        t = decide_tasks(now, True, now + timedelta(minutes=10), {"stocks_scan_date": "2026-11-17"})
        self.assertFalse(t["stocks_scan"])
        self.assertTrue(t["monitor_stocks"])

    def test_no_scan_too_close_to_close(self):
        now = self.utc(2026, 11, 17, 20, 57)   # 15:57, cierra 16:00
        t = decide_tasks(now, True, now + timedelta(minutes=3), {})
        self.assertFalse(t["stocks_scan"])

    def test_market_closed_no_stock_tasks(self):
        now = self.utc(2026, 11, 21, 20, 45)   # sábado, escaneo de cripto del día ya hecho
        t = decide_tasks(now, False, None, {"daily_24h_scan_date": "2026-11-21"})
        self.assertFalse(t["stocks_scan"])
        self.assertFalse(t["monitor_stocks"])
        self.assertTrue(t["monitor_crypto"])

    def test_daily_crypto_scan_and_weekend_forex(self):
        sat = self.utc(2026, 11, 21, 0, 10)
        t = decide_tasks(sat, False, None, {})
        self.assertTrue(t["daily_24h_scan"])
        self.assertFalse(t["include_forex"])
        self.assertFalse(t["monitor_crypto"])   # el escaneo ya cubre cripto
        t2 = decide_tasks(sat, False, None, {"daily_24h_scan_date": "2026-11-21"})
        self.assertFalse(t2["daily_24h_scan"])
        self.assertTrue(t2["monitor_crypto"])
        tue = self.utc(2026, 11, 17, 0, 10)
        self.assertTrue(decide_tasks(tue, False, None, {})["include_forex"])


if __name__ == "__main__":
    unittest.main()
