"""
==============================================
STOPS DE PROTECCIÓN EN EL BROKER (OPCIÓN A)
==============================================
Cada posición de acciones queda protegida por una orden STOP (GTC) que vive en Alpaca.
Alpaca vigila el precio de forma continua, así que la posición está protegida aunque
el bot no esté corriendo.

Reglas:
- Solo acciones (no cripto ni forex) y solo cantidades enteras (Alpaca no acepta stops fraccionales).
- Stop del símbolo = el más alto entre los lotes (igual que check_exit_conditions, que vende
  todo el símbolo si cualquier lote toca su stop).
- El stop solo sube (trailing). Si sube al menos `stop_replace_min_pct`, se reemplaza la orden.
- Si cambia la cantidad (DCA o venta parcial), se reemplaza la orden.
- Si el stop ya está por encima del precio actual, no se coloca: la salida la ejecuta el bot.
- Si el bot tiene una venta propia pendiente, no se coloca stop (las acciones están apartadas).
- Antes de cualquier venta del bot, cancel_for_symbol() libera las acciones y espera la
  confirmación de Alpaca. Si el stop se ejecutó mientras tanto, avisa para NO vender dos veces.
- Cuando Alpaca ejecuta el stop, la reconciliación de OrderManager registra la venta.
"""

import logging
import math
import time
from datetime import datetime
from typing import Dict, List, Optional

from config import ASSETS, SCHEDULE_CONFIG
from order_manager import KIND_PROTECTIVE_STOP
from pricing_utils import round_order_price

logger = logging.getLogger(__name__)

# Estados finales de una orden en Alpaca
_TERMINAL_CANCELED = ("canceled", "cancelled", "expired", "rejected", "replaced")


def _status(order) -> str:
    return str(getattr(order, "status", "")).lower()


def _is_filled(status: str) -> bool:
    return "filled" in status and "partially" not in status


def is_stock_symbol(symbol: str) -> bool:
    """True si el símbolo es una acción/ETF operada en Alpaca (no cripto ni forex)."""
    if symbol in ASSETS.get("forex", []) or symbol.endswith("=X"):
        return False
    if "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", []):
        return False
    return True


class ProtectiveStopManager:
    """Coloca, sube y cancela los stops de protección en Alpaca."""

    def __init__(self, alpaca_client, order_manager, risk_manager, telegram=None):
        self.alpaca = alpaca_client
        self.orders = order_manager
        self.risk = risk_manager
        self.telegram = telegram
        self.enabled = bool(alpaca_client) and SCHEDULE_CONFIG.get("protective_stops_enabled", True)
        self.replace_min_pct = SCHEDULE_CONFIG.get("stop_replace_min_pct", 0.002)

    # ------------------------------------------------------------------
    # Cálculo del stop deseado
    # ------------------------------------------------------------------
    def desired_stop(self, symbol: str) -> Optional[Dict]:
        """Devuelve {'qty', 'stop_price', 'last_price', 'asset_type'} o None si no aplica."""
        pos_list = self.risk.positions.get(symbol) or []
        if not pos_list or not is_stock_symbol(symbol):
            return None
        if any(p.side != "BUY" for p in pos_list):
            return None  # Solo protegemos posiciones largas

        qty = math.floor(sum(p.quantity for p in pos_list) + 1e-9)
        if qty < 1:
            return None

        stop_raw = max(p.stop_loss for p in pos_list)
        stop_price = round_order_price(stop_raw)
        last_price = pos_list[-1].last_known_price or pos_list[-1].entry_price
        if stop_price <= 0:
            return None
        if last_price > 0 and stop_price >= last_price:
            # Ya está "disparado": no tiene sentido un stop por encima del precio. Lo vende el bot.
            return None
        return {"qty": qty, "stop_price": stop_price, "last_price": last_price,
                "asset_type": pos_list[0].asset_type, "atr": pos_list[0].atr_at_entry}

    # ------------------------------------------------------------------
    # Sincronización (colocar / subir / ajustar cantidad)
    # ------------------------------------------------------------------
    def ensure_all(self) -> Dict[str, List[str]]:
        """Asegura que cada posición de acciones tenga su stop correcto en Alpaca."""
        summary = {"placed": [], "raised": [], "skipped": [], "errors": []}
        if not self.enabled:
            return summary

        open_sell_orders = self._fetch_open_sell_orders()
        self._adopt_untracked_stops(open_sell_orders)

        for symbol in list(self.risk.positions.keys()):
            try:
                result = self.ensure_symbol(symbol, open_sell_orders.get(self._alpaca_symbol(symbol), []))
                if result:
                    summary.setdefault(result, []).append(symbol)
            except Exception as e:
                logger.error(f"[{symbol}] Error asegurando stop de protección: {e}")
                summary["errors"].append(symbol)

        if summary["placed"] or summary["raised"]:
            logger.info(f"🛡️ Stops en broker -> colocados: {summary['placed']} | subidos: {summary['raised']}")
        return summary

    def ensure_symbol(self, symbol: str, open_sell_orders: Optional[List] = None) -> Optional[str]:
        """Coloca o ajusta el stop de un símbolo. Devuelve 'placed', 'raised', 'skipped' o None."""
        target = self.desired_stop(symbol)
        tracked = self.orders.get_protective_orders(symbol)

        if target is None:
            return "skipped" if is_stock_symbol(symbol) else None

        # Si el bot tiene su propia venta pendiente, las acciones están apartadas: no colocar stop.
        if self.orders.has_pending_order(symbol, "SELL"):
            return "skipped"
        tracked_ids = {o["order_id"] for o in tracked}
        foreign = [o for o in (open_sell_orders or []) if str(o.id) not in tracked_ids]
        if foreign:
            logger.info(f"[{symbol}] Tiene órdenes de venta abiertas no gestionadas como stop. No se coloca stop.")
            return "skipped"

        if not tracked:
            return self._place(symbol, target)

        current = tracked[0]
        cur_stop = float(current.get("stop_price", 0.0))
        cur_qty = float(current.get("qty", 0.0))
        qty_changed = int(cur_qty) != int(target["qty"])
        raised = target["stop_price"] > cur_stop * (1 + self.replace_min_pct)

        if qty_changed or raised:
            new_stop = max(target["stop_price"], cur_stop)  # Nunca bajar el stop
            return self._replace(symbol, current, target["qty"], new_stop)
        return None

    def _place(self, symbol: str, target: Dict) -> Optional[str]:
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client_order_id = f"TR2_STOP_{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        req = StopOrderRequest(
            symbol=self._alpaca_symbol(symbol),
            qty=target["qty"],
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=target["stop_price"],
            client_order_id=client_order_id,
        )
        order = self.alpaca.submit_order(order_data=req)
        self.orders.add_order(
            order_id=str(order.id),
            client_order_id=client_order_id,
            symbol=symbol,
            side="SELL",
            qty=target["qty"],
            limit_price=0.0,
            stop_price=target["stop_price"],
            asset_type=target["asset_type"],
            kind=KIND_PROTECTIVE_STOP,
            details={"reason": f"STOP_LOSS_BROKER (Stop ${target['stop_price']:.4f})",
                     "atr": target["atr"], "spread": 0.0, "score": 0.0},
        )
        logger.info(f"🛡️ [{symbol}] Stop de protección colocado en Alpaca: SELL {target['qty']} @ stop ${target['stop_price']:.4f}")
        return "placed"

    def _replace(self, symbol: str, current: Dict, qty: int, stop_price: float) -> Optional[str]:
        from alpaca.trading.requests import ReplaceOrderRequest

        old_id = current["order_id"]
        try:
            new_order = self.alpaca.replace_order_by_id(
                old_id, ReplaceOrderRequest(qty=qty, stop_price=stop_price)
            )
        except Exception as e:
            # Puede haberse ejecutado o cancelado mientras tanto; la reconciliación lo resolverá.
            logger.warning(f"[{symbol}] No se pudo reemplazar el stop {old_id}: {e}")
            return None

        info = dict(current)
        info.update({"order_id": str(new_order.id), "qty": float(qty), "stop_price": float(stop_price)})
        info["details"] = dict(current.get("details", {}))
        info["details"]["reason"] = f"STOP_LOSS_BROKER (Stop ${stop_price:.4f})"
        self.orders.remove_order(old_id)
        self.orders.pending_orders[info["order_id"]] = info
        self.orders.save_state()
        logger.info(f"🛡️ [{symbol}] Stop actualizado en Alpaca: {qty} @ stop ${stop_price:.4f} (antes ${current.get('stop_price', 0):.4f})")
        return "raised"

    # ------------------------------------------------------------------
    # Cancelación antes de que el bot venda
    # ------------------------------------------------------------------
    def cancel_for_symbol(self, symbol: str, timeout_s: float = 5.0) -> str:
        """
        Cancela los stops de protección del símbolo y espera confirmación de Alpaca.
        Devuelve:
          'none'     -> no había stop registrado
          'canceled' -> acciones liberadas, el bot puede vender
          'filled'   -> el stop ya se ejecutó: el bot NO debe vender (la reconciliación lo registra)
          'unknown'  -> no se pudo confirmar a tiempo
        """
        tracked = self.orders.get_protective_orders(symbol) if self.alpaca else []
        if not tracked:
            return "none"

        outcome = "canceled"
        for info in tracked:
            order_id = info["order_id"]
            try:
                self.alpaca.cancel_order_by_id(order_id)
            except Exception as e:
                logger.info(f"[{symbol}] Cancelación de stop {order_id}: {e}")

            status = self._wait_terminal(order_id, timeout_s)
            if _is_filled(status) or "partially" in status:
                outcome = "filled"  # Se deja en pendientes para que la reconciliación registre la venta
                logger.warning(f"[{symbol}] El stop {order_id} se ejecutó en Alpaca antes de cancelarlo ({status}).")
            elif any(s in status for s in _TERMINAL_CANCELED):
                self.orders.remove_order(order_id)
            else:
                outcome = "unknown" if outcome != "filled" else outcome
                logger.warning(f"[{symbol}] No se confirmó la cancelación del stop {order_id} (estado: {status}).")
        return outcome

    def _wait_terminal(self, order_id: str, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        status = ""
        while True:
            try:
                status = _status(self.alpaca.get_order_by_id(order_id))
            except Exception as e:
                logger.debug(f"Consultando orden {order_id}: {e}")
            if _is_filled(status) or any(s in status for s in _TERMINAL_CANCELED):
                return status
            if time.monotonic() >= deadline:
                return status
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @staticmethod
    def _alpaca_symbol(symbol: str) -> str:
        return symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol

    def _fetch_open_sell_orders(self) -> Dict[str, List]:
        """Órdenes de venta abiertas en Alpaca agrupadas por símbolo."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        grouped: Dict[str, List] = {}
        try:
            open_orders = self.alpaca.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
        except Exception as e:
            logger.warning(f"No se pudieron consultar órdenes abiertas en Alpaca: {e}")
            return grouped
        for o in open_orders or []:
            if "sell" in str(getattr(o, "side", "")).lower():
                grouped.setdefault(str(o.symbol), []).append(o)
        return grouped

    def _adopt_untracked_stops(self, open_sell_orders: Dict[str, List]):
        """
        Si existe en Alpaca un stop de venta del bot que no está en pending_orders.json
        (por ejemplo, se perdió el archivo), se adopta en lugar de crear un duplicado.
        """
        tracked_ids = set(self.orders.pending_orders.keys())
        for alp_symbol, orders in open_sell_orders.items():
            symbol = alp_symbol.replace("/USD", "-USD")
            if symbol not in self.risk.positions:
                continue
            for o in orders:
                order_type = str(getattr(o, "order_type", getattr(o, "type", ""))).lower()
                coid = str(getattr(o, "client_order_id", "") or "")
                if str(o.id) in tracked_ids or "stop" not in order_type or not coid.startswith("TR2_STOP_"):
                    continue
                stop_price = float(getattr(o, "stop_price", 0) or 0)
                self.orders.add_order(
                    order_id=str(o.id), client_order_id=coid, symbol=symbol, side="SELL",
                    qty=float(o.qty or 0), limit_price=0.0, stop_price=stop_price,
                    asset_type=self.risk.positions[symbol][0].asset_type, kind=KIND_PROTECTIVE_STOP,
                    details={"reason": f"STOP_LOSS_BROKER (Stop ${stop_price:.4f})",
                             "atr": self.risk.positions[symbol][0].atr_at_entry, "spread": 0.0, "score": 0.0},
                )
                tracked_ids.add(str(o.id))
                logger.info(f"🛡️ [{symbol}] Stop existente en Alpaca adoptado ({o.id}).")
