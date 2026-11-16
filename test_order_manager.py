import os
import unittest
import tempfile
from unittest.mock import MagicMock
from order_manager import OrderManager
from risk_management import RiskManager


class OrderManagerTest(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.test_dir.name, "test_pending_orders.json")
        self.manager = OrderManager(state_file=self.state_file)
        self.risk_manager = RiskManager(starting_capital=50000.0, in_memory=True)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_add_and_has_pending_order(self):
        self.assertFalse(self.manager.has_pending_order("GOGO", "BUY"))
        self.manager.add_order(
            order_id="ord_123",
            client_order_id="TR2_BUY_GOGO_1",
            symbol="GOGO",
            side="BUY",
            qty=100.0,
            limit_price=3.99,
            asset_type="normal"
        )
        self.assertTrue(self.manager.has_pending_order("GOGO", "BUY"))
        self.assertTrue(self.manager.has_pending_order("GOGO"))
        self.assertFalse(self.manager.has_pending_order("GOGO", "SELL"))
        self.assertFalse(self.manager.has_pending_order("AAPL", "BUY"))

    def test_reconcile_filled_order(self):
        self.manager.add_order(
            order_id="ord_buy_fill",
            client_order_id="TR2_BUY_AAPL_1",
            symbol="AAPL",
            side="BUY",
            qty=10.0,
            limit_price=220.0,
            asset_type="normal"
        )
        
        # Mock de Alpaca
        mock_alpaca = MagicMock()
        mock_order = MagicMock()
        mock_order.status = "filled"
        mock_order.filled_qty = "10.0"
        mock_order.filled_avg_price = "219.50"
        mock_order.filled_at = None
        mock_alpaca.get_order_by_id.return_value = mock_order

        summary = self.manager.reconcile_orders(mock_alpaca, self.risk_manager)
        
        self.assertEqual(len(summary["filled"]), 1)
        self.assertFalse(self.manager.has_pending_order("AAPL", "BUY"))
        # Debe haberse abierto la posición con precio de llenado real de Alpaca (219.50)
        self.assertIn("AAPL", self.risk_manager.positions)
        self.assertEqual(self.risk_manager.positions["AAPL"][0].entry_price, 219.50)
        self.assertEqual(self.risk_manager.positions["AAPL"][0].quantity, 10.0)

    def test_reconcile_expired_with_partial_fill(self):
        # Caso A del veredicto: orden DAY expirada que alcanzó a llenar 4 de 10 acciones
        self.manager.add_order(
            order_id="ord_partial_expire",
            client_order_id="TR2_BUY_MSFT_1",
            symbol="MSFT",
            side="BUY",
            qty=10.0,
            limit_price=410.0,
            asset_type="normal"
        )
        
        mock_alpaca = MagicMock()
        mock_order = MagicMock()
        mock_order.status = "expired"
        mock_order.filled_qty = "4.0"
        mock_order.filled_avg_price = "409.80"
        mock_order.filled_at = None
        mock_alpaca.get_order_by_id.return_value = mock_order

        summary = self.manager.reconcile_orders(mock_alpaca, self.risk_manager)
        
        self.assertEqual(len(summary["canceled"]), 1)
        self.assertFalse(self.manager.has_pending_order("MSFT"))
        # Las 4 acciones deben haberse registrado localmente a 409.80
        self.assertIn("MSFT", self.risk_manager.positions)
        self.assertEqual(self.risk_manager.positions["MSFT"][0].quantity, 4.0)
        self.assertEqual(self.risk_manager.positions["MSFT"][0].entry_price, 409.80)

    def test_get_pending_speculative_cost(self):
        self.manager.add_order(
            order_id="warr_1",
            client_order_id="TR2_BUY_WARR_1",
            symbol="CYAB",
            side="BUY",
            qty=500.0,
            limit_price=0.80,
            asset_type="warrant"
        )
        self.manager.add_order(
            order_id="norm_1",
            client_order_id="TR2_BUY_NORM_1",
            symbol="NVDA",
            side="BUY",
            qty=2.0,
            limit_price=130.0,
            asset_type="normal"
        )
        
        spec_cost = self.manager.get_pending_speculative_cost()
        # Solo CYAB cuenta: 500 * 0.80 = 400.0
        self.assertEqual(spec_cost, 400.0)


if __name__ == "__main__":
    unittest.main()
