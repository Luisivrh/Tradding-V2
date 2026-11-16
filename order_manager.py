"""
==============================================
GESTOR DE ÓRDENES PENDIENTES Y RECONCILIACIÓN
==============================================
Garantiza que Alpaca sea la única fuente de verdad (SSOT).
Desacopla el envío de órdenes de la contabilidad local:
- Las compras y ventas solo se registran en portfolio.json cuando Alpaca confirma el 'FILL'.
- Reconcilia órdenes al inicio de cada ciclo.
- Registra llenados parciales incluso si la orden expira o es cancelada.
- Previene el reenvío de órdenes duplicadas.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

KIND_TRADE = "TRADE"
KIND_PROTECTIVE_STOP = "PROTECTIVE_STOP"


class OrderManager:
    """Administra el ciclo de vida asíncrono de órdenes pendientes contra Alpaca."""

    def __init__(self, state_file: str = "pending_orders.json"):
        self.state_file = state_file
        self.pending_orders: Dict[str, Dict] = {}
        self.load_state()

    def load_state(self):
        """Carga las órdenes pendientes desde el archivo JSON."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.pending_orders = data.get("orders", {})
            except Exception as e:
                logger.error(f"Error cargando {self.state_file}: {e}")
                self.pending_orders = {}
        else:
            self.pending_orders = {}

    def save_state(self):
        """Guarda las órdenes pendientes en el archivo JSON."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"orders": self.pending_orders, "updated_at": datetime.now().isoformat()}, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando {self.state_file}: {e}")

    def add_order(
        self,
        order_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float,
        asset_type: str = "normal",
        details: Optional[Dict] = None,
        kind: str = KIND_TRADE,
        stop_price: float = 0.0
    ):
        """
        Registra una nueva orden pendiente de confirmación.
        kind=KIND_TRADE: orden de entrada/salida del bot.
        kind=KIND_PROTECTIVE_STOP: stop loss que vive en Alpaca (GTC) protegiendo la posición.
        """
        self.pending_orders[order_id] = {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side.upper(),
            "qty": float(qty),
            "limit_price": float(limit_price),
            "stop_price": float(stop_price),
            "kind": kind,
            "asset_type": asset_type,
            "details": details or {},
            "created_at": datetime.now().isoformat(),
            "partially_filled_qty": 0.0
        }
        self.save_state()
        price_txt = f"stop ${stop_price:.4f}" if kind == KIND_PROTECTIVE_STOP else f"@ ${limit_price:.4f}"
        logger.info(f"📋 Orden {order_id} ({kind} {side} {qty} {symbol} {price_txt}) registrada en pendientes.")

    def remove_order(self, order_id: str):
        """Deja de dar seguimiento a una orden (ej. reemplazada o cancelada por el bot)."""
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]
            self.save_state()

    def has_pending_order(self, symbol: str, side: Optional[str] = None, include_protective: bool = False) -> bool:
        """
        Verifica si ya existe una orden activa del bot para este símbolo.
        Los stops de protección se ignoran por defecto: no deben bloquear una venta del bot
        (el bot los cancela antes de vender).
        """
        for o in self.pending_orders.values():
            if o.get("symbol") != symbol:
                continue
            if not include_protective and o.get("kind") == KIND_PROTECTIVE_STOP:
                continue
            if side is None or o.get("side") == side.upper():
                return True
        return False

    def get_protective_orders(self, symbol: str) -> List[Dict]:
        """Devuelve los stops de protección registrados para un símbolo."""
        return [o for o in self.pending_orders.values()
                if o.get("symbol") == symbol and o.get("kind") == KIND_PROTECTIVE_STOP]

    def get_pending_speculative_cost(self) -> float:
        """Calcula el costo total comprometido en órdenes de compra especulativas pendientes."""
        total = 0.0
        for o in self.pending_orders.values():
            if o.get("side") == "BUY" and o.get("asset_type") in ["warrant", "penny"]:
                remaining_qty = o.get("qty", 0.0) - o.get("partially_filled_qty", 0.0)
                price = o.get("limit_price", 0.0)
                total += max(0.0, remaining_qty * price)
        return total

    def reconcile_orders(self, alpaca_client, risk_manager, telegram_bot=None) -> Dict[str, List[str]]:
        """
        Reconcilia órdenes pendientes contra el estado real de Alpaca:
        - FILLED: Ejecuta open_position o close_position con el precio y cantidad reales de Alpaca.
        - CANCELED / EXPIRED con filled_qty > 0: Registra la porción parcialmente ejecutada.
        - Elimina órdenes terminales de pending_orders.json.
        """
        if not alpaca_client:
            return {"filled": [], "canceled": [], "pending": []}

        summary = {"filled": [], "canceled": [], "pending": []}
        orders_to_remove = []

        logger.info(f"🔄 Reconciliando {len(self.pending_orders)} órdenes pendientes con Alpaca...")

        for order_id, order_info in list(self.pending_orders.items()):
            try:
                alpaca_order = alpaca_client.get_order_by_id(order_id)
                status = str(alpaca_order.status).lower()
                symbol = order_info["symbol"]
                side = order_info["side"]
                details = order_info.get("details", {})
                
                filled_qty = float(alpaca_order.filled_qty or 0.0)
                filled_avg_price = float(alpaca_order.filled_avg_price or 0.0)
                previously_recorded_qty = float(order_info.get("partially_filled_qty", 0.0))
                new_filled_qty = filled_qty - previously_recorded_qty

                # CASO 1: ORDEN COMPLETAMENTE EJECUTADA
                if "filled" in status and "partially" not in status:
                    # [PUNTO 7 CORREGIDO] Solo registrar si queda cantidad pendiente por registrar (evita duplicar)
                    if new_filled_qty > 0 and filled_avg_price > 0:
                        self._record_fill(
                            risk_manager=risk_manager,
                            symbol=symbol,
                            side=side,
                            qty=new_filled_qty,
                            price=filled_avg_price,
                            order_info=order_info,
                            filled_at=str(alpaca_order.filled_at or datetime.now()),
                            telegram_bot=telegram_bot
                        )
                        summary["filled"].append(f"{symbol} ({side} {new_filled_qty} @ ${filled_avg_price:.4f})")
                    orders_to_remove.append(order_id)

                # CASO 2: CANCELADA O EXPIRADA
                elif any(s in status for s in ["canceled", "cancelled", "expired", "rejected"]):
                    # REGLA CRÍTICA: Si tuvo llenado parcial antes de expirar/cancelar, registrar lo ejecutado
                    if new_filled_qty > 0 and filled_avg_price > 0:
                        logger.warning(f"⚠️ Orden {order_id} ({symbol}) finalizó en '{status}' con llenado parcial ({new_filled_qty} acciones). Registrando.")
                        self._record_fill(
                            risk_manager=risk_manager,
                            symbol=symbol,
                            side=side,
                            qty=new_filled_qty,
                            price=filled_avg_price,
                            order_info=order_info,
                            filled_at=str(alpaca_order.filled_at or datetime.now()),
                            telegram_bot=telegram_bot
                        )
                    orders_to_remove.append(order_id)
                    summary["canceled"].append(f"{symbol} ({side} - {status})")

                # CASO 3: PARCIALMENTE EJECUTADA (AÚN ABIERTA)
                elif "partially_filled" in status:
                    if new_filled_qty > 0 and filled_avg_price > 0:
                        logger.info(f"⏳ Llenado parcial en {symbol}: +{new_filled_qty} acciones @ ${filled_avg_price:.4f}")
                        self._record_fill(
                            risk_manager=risk_manager,
                            symbol=symbol,
                            side=side,
                            qty=new_filled_qty,
                            price=filled_avg_price,
                            order_info=order_info,
                            filled_at=str(alpaca_order.filled_at or datetime.now()),
                            telegram_bot=telegram_bot
                        )
                        order_info["partially_filled_qty"] = filled_qty
                        self.save_state()
                    summary["pending"].append(f"{symbol} ({side} - parcial {filled_qty}/{order_info['qty']})")

                # CASO 4: REEMPLAZADA (ORDEN MODIFICADA EN ALPACA)
                elif "replaced" in status:
                    replaced_by = getattr(alpaca_order, "replaced_by", None)
                    if replaced_by:
                        logger.info(f"🔄 Orden {order_id} ({symbol}) reemplazada por {replaced_by}. Migrando tracking.")
                        order_info["order_id"] = str(replaced_by)
                        self.pending_orders[str(replaced_by)] = order_info
                    orders_to_remove.append(order_id)

                # CASO 5: SIGUE ABIERTA
                else:
                    summary["pending"].append(f"{symbol} ({side} - {status})")

            except Exception as e:
                logger.error(f"Error consultando estado de orden {order_id} en Alpaca: {e}")

        # Limpiar órdenes terminales
        for oid in orders_to_remove:
            if oid in self.pending_orders:
                del self.pending_orders[oid]

        if orders_to_remove:
            self.save_state()

        logger.info(f"✅ Reconciliación completada: {len(summary['filled'])} filled, {len(summary['canceled'])} finalizadas/canceladas, {len(summary['pending'])} pendientes.")
        return summary

    def _record_fill(
        self,
        risk_manager,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        order_info: Dict,
        filled_at: str,
        telegram_bot=None
    ):
        """Registra el llenado real en el portafolio local."""
        details = order_info.get("details", {})
        asset_type = order_info.get("asset_type", "normal")
        atr = details.get("atr", price * 0.05)
        spread = details.get("spread", 0.001)
        score = details.get("score", 7.0)

        if side == "BUY":
            position = risk_manager.open_position(
                symbol=symbol,
                price=price,
                side="BUY",
                atr=atr,
                spread_pct=spread,
                score=score,
                entry_date=filled_at,
                forced_quantity=qty,
                bypass_filters=True
            )
            logger.info(f"✅ [COMPRA CONFIRMADA EN BROKER] {symbol}: {qty} acciones @ ${price:.4f}")
            if telegram_bot and position:
                cost = qty * price
                telegram_bot.send_buy_alert(
                    symbol=symbol,
                    asset_type=position.asset_type,
                    price=price,
                    qty=qty,
                    cost=cost,
                    score=score,
                    atr=atr,
                    spread=spread,
                    reason=details.get("reason", "ORDEN_LIMIT_EJECUTADA"),
                    is_real=True
                )
        elif side == "SELL":
            entry_p = price
            hold_hours = 0.0
            if symbol in risk_manager.positions and risk_manager.positions[symbol]:
                entry_p = risk_manager.positions[symbol][0].entry_price
                try:
                    dt_in = datetime.fromisoformat(risk_manager.positions[symbol][0].entry_date)
                    hold_hours = (datetime.now() - dt_in).total_seconds() / 3600.0
                except Exception:
                    hold_hours = 0.0

            reason = details.get("reason", "ORDEN_VENTA_EJECUTADA")
            pnl = risk_manager.close_position(
                symbol=symbol,
                price=price,
                reason=reason,
                exit_date=filled_at,
                exit_atr=atr,
                exit_spread=spread,
                exit_score=score,
                quantity_to_close=qty
            )
            logger.info(f"🔴 [VENTA CONFIRMADA EN BROKER] {symbol}: {qty} acciones @ ${price:.4f} | PnL: ${pnl:.2f}")
            if telegram_bot:
                cost_basis = entry_p * qty
                pnl_pct = (pnl / cost_basis) if cost_basis > 0 else 0.0
                telegram_bot.send_sell_alert(
                    symbol=symbol,
                    entry_price=entry_p,
                    exit_price=price,
                    qty=qty,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_time=hold_hours,
                    reason=reason,
                    atr=atr,
                    spread=spread,
                    is_real=True
                )
