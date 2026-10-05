"""
==============================================
GESTION DE RIESGOS - BOT TRADING
==============================================
Controla stop-loss, take-profit, posición sizing,
y protección del capital (con Memoria Persistente).
"""

import logging
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
from datetime import datetime
from config import RISK_CONFIG

logger = logging.getLogger(__name__)

# ==========================================
# POSICION (Trade)
# ==========================================
@dataclass
class Position:
    """Representa una posición abierta."""
    symbol: str
    entry_price: float
    entry_date: str
    side: str  # "BUY" o "SELL"
    quantity: float
    stop_loss: float
    take_profit: float
    trailing_stop_triggered: float = 0.0
    highest_price: float = 0.0

    def to_dict(self):
        return asdict(self)
        
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


# ==========================================
# GESTOR DE RIESGOS Y MEMORIA
# ==========================================
class RiskManager:
    """
    Simulador interno y gestor de riesgos con memoria.
    Guarda todas las compras en portfolio.json para evitar compras duplicadas.
    """
    def __init__(self, starting_capital: float = None):
        self.portfolio_file = Path("portfolio.json")
        self.starting_capital = starting_capital if starting_capital else RISK_CONFIG["starting_capital"]
        
        # Estado por defecto
        self.current_capital = self.starting_capital
        self.positions: Dict[str, List[Position]] = {} 
        self.trade_history = []
        self.max_pyramid = 3 # Máximo 3 compras (DCA) del mismo activo
        
        self.load_portfolio()
        
    def load_portfolio(self):
        """Carga el estado del portafolio desde el disco (Anti-Amnesia)."""
        if self.portfolio_file.exists():
            try:
                with open(self.portfolio_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.current_capital = data.get("current_capital", self.starting_capital)
                    self.trade_history = data.get("trade_history", [])
                    
                    # Cargar posiciones
                    raw_positions = data.get("positions", {})
                    for sym, pos_list in raw_positions.items():
                        self.positions[sym] = [Position.from_dict(p) for p in pos_list]
                        
                logger.info(f"💾 Portafolio cargado: Capital ${self.current_capital:.2f}, {sum(len(v) for v in self.positions.values())} posiciones abiertas.")
            except Exception as e:
                logger.error(f"Error cargando portfolio.json: {e}. Iniciando en limpio.")
                self.current_capital = self.starting_capital
                self.positions = {}
        else:
            logger.info(f"🆕 Iniciando nuevo portafolio de simulación con ${self.current_capital:.2f}")

    def save_portfolio(self):
        """Guarda el estado del portafolio en el disco."""
        try:
            data = {
                "current_capital": self.current_capital,
                "positions": {sym: [p.to_dict() for p in pos_list] for sym, pos_list in self.positions.items()},
                "trade_history": self.trade_history
            }
            with open(self.portfolio_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando portfolio.json: {e}")

    def get_total_portfolio_value(self) -> float:
        """Estima el valor total (capital líquido + valor invertido en posiciones)."""
        pos_value = sum(
            p.quantity * p.entry_price 
            for pos_list in self.positions.values() 
            for p in pos_list
        )
        return self.current_capital + pos_value

    def get_drawdown(self) -> float:
        """Calcula la pérdida actual vs capital inicial."""
        current_val = self.get_total_portfolio_value()
        if current_val >= self.starting_capital:
            return 0.0
        return (self.starting_capital - current_val) / self.starting_capital

    def _calculate_position_size(self, entry_price: float, stop_loss_price: float) -> float:
        """Calcula el tamaño de posición basado en riesgo."""
        risk_amount = self.get_total_portfolio_value() * RISK_CONFIG["risk_per_trade"]
        price_risk_per_unit = abs(entry_price - stop_loss_price)
        
        if price_risk_per_unit <= 0:
            return 0
            
        units = risk_amount / price_risk_per_unit
        
        # Validar que tengamos suficiente capital líquido
        max_units_affordable = self.current_capital / entry_price
        return min(units, max_units_affordable)

    def can_open_position(self, symbol: str, price: float) -> bool:
        """Reglas estrictas anti-amnesia y Promedio DCA."""
        total_open_trades = sum(len(v) for v in self.positions.values())
        if total_open_trades >= RISK_CONFIG["max_positions"]:
            logger.warning(f"[{symbol}] Rechazado: Máximo de posiciones globales alcanzado ({total_open_trades}).")
            return False
            
        if symbol in self.positions and len(self.positions[symbol]) > 0:
            pos_list = self.positions[symbol]
            
            # Regla 1: Límite de piramidación
            if len(pos_list) >= self.max_pyramid:
                logger.warning(f"[{symbol}] Rechazado: Máximo de compras DCA alcanzado para este activo ({self.max_pyramid}).")
                return False
                
            # Regla 2: No comprar el mismo día
            today = str(datetime.now().date())
            # Convertimos la fecha guardada para compararla
            last_trade_date_str = pos_list[-1].entry_date[:10] # ej "2026-10-04"
            if today == last_trade_date_str:
                logger.warning(f"[{symbol}] Rechazado (Regla Anti-Spam): Ya se compró este activo el día de hoy.")
                return False
                
            # Regla 3: DCA (Solo comprar más si cayó al menos 5%)
            avg_entry_price = sum(p.entry_price * p.quantity for p in pos_list) / sum(p.quantity for p in pos_list)
            if price > (avg_entry_price * 0.95):
                logger.warning(f"[{symbol}] Rechazado (DCA): El precio actual ({price}) no es un 5% inferior a tu precio promedio ({avg_entry_price:.2f}).")
                return False

        return True

    def open_position(self, symbol: str, price: float, side: str, entry_date: str) -> Optional[Position]:
        """Abre posición si pasa los filtros de memoria y calcula stops."""
        
        if not self.can_open_position(symbol, price):
            return None
        
        # Calcular stops
        if side == "BUY":
            stop_loss = price * (1 - RISK_CONFIG["stop_loss_pct"])
            take_profit = price * (1 + RISK_CONFIG["take_profit_pct"])
        else: # SELL
            stop_loss = price * (1 + RISK_CONFIG["stop_loss_pct"])
            take_profit = price * (1 - RISK_CONFIG["take_profit_pct"])
            
        quantity = self._calculate_position_size(price, stop_loss)
        
        if quantity <= 0:
            logger.warning(f"[{symbol}] Capital insuficiente o riesgo demasiado alto.")
            return None
            
        cost = quantity * price
        
        if cost > self.current_capital:
            logger.warning(f"[{symbol}] Capital líquido insuficiente (${self.current_capital:.2f} < ${cost:.2f})")
            return None
            
        # Ejecutar simulación contable
        self.current_capital -= cost
        
        position = Position(
            symbol=symbol,
            entry_price=price,
            quantity=quantity,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_date=entry_date,
            highest_price=price
        )
        
        if symbol not in self.positions:
            self.positions[symbol] = []
            
        self.positions[symbol].append(position)
        
        # GUARDAR ESTADO
        self.save_portfolio()
        
        logger.info(f"[{symbol}] Position Size: {quantity:.4f} units | Risk: ${(abs(price-stop_loss)*quantity):.2f} | Capital: ${self.current_capital:.2f}")
        logger.info(f"✅ POSICION ABIERTA INTERNA: {side} {quantity:.4f} {symbol} @ {price:.2f} | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        
        return position

    def check_exit_conditions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Revisa todas las posiciones abiertas contra los precios actuales del mercado
        para disparar Stop-Loss, Take-Profit o Trailing Stops.
        Retorna una lista de órdenes de venta forzosas.
        """
        forced_exits = []
        
        for symbol, pos_list in list(self.positions.items()):
            if symbol not in current_prices:
                continue
                
            current_price = current_prices[symbol]
            sell_reasons = []
            
            for i, pos in enumerate(pos_list):
                # 1. Actualizar el "Highest Price" para el Trailing Stop
                if current_price > pos.highest_price and pos.side == "BUY":
                    pos.highest_price = current_price
                    # Recalcular el trailing stop price
                    if RISK_CONFIG.get("trailing_stop_enabled", False):
                        new_trailing_sl = pos.highest_price * (1 - RISK_CONFIG.get("trailing_stop_pct", 0.02))
                        # El stop loss solo puede subir, nunca bajar
                        if new_trailing_sl > pos.stop_loss:
                            pos.stop_loss = new_trailing_sl
                            
                # 2. Evaluar Stop Loss (Fijo o Trailing)
                if pos.side == "BUY" and current_price <= pos.stop_loss:
                    sell_reasons.append(f"STOP_LOSS (Trigger en ${pos.stop_loss:.2f})")
                    
                # 3. Evaluar Take Profit
                elif pos.side == "BUY" and current_price >= pos.take_profit:
                    sell_reasons.append(f"TAKE_PROFIT (Meta de ${pos.take_profit:.2f} alcanzada)")
                    
                # 4. Evaluar Vencimiento (Time-based exit)
                else:
                    days_held = (datetime.now() - datetime.fromisoformat(pos.entry_date)).days
                    if days_held >= RISK_CONFIG.get("max_hold_days", 30):
                        sell_reasons.append(f"MAX_TIME (Retenido por {days_held} días)")

            # Si alguna de las posiciones de este símbolo activó una alarma, lo liquidamos
            if sell_reasons:
                reason = " | ".join(set(sell_reasons))
                forced_exits.append({
                    "symbol": symbol,
                    "signal_type": "SELL",
                    "score": 10.0, # Fuerza máxima para que el bot venda sin dudar
                    "current_price": current_price,
                    "details": {"reason": reason, "price": current_price}
                })
                
        # Guardar si hubo actualizaciones de Trailing Stops
        self.save_portfolio()
        return forced_exits

    def close_position(self, symbol: str, price: float, reason: str, exit_date: str) -> float:
        """Cierra todas las posiciones de un activo (liquidar) y devuelve el PnL."""
        if symbol not in self.positions or not self.positions[symbol]:
            return 0.0
            
        total_pnl = 0.0
        total_revenue = 0.0
        
        for pos in self.positions[symbol]:
            if pos.side == "BUY":
                pnl = (price - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - price) * pos.quantity
                
            total_pnl += pnl
            total_revenue += (pos.entry_price * pos.quantity) + pnl
            
            self.trade_history.append({
                "symbol": symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": price,
                "pnl": pnl,
                "reason": reason,
                "date": exit_date
            })
            
        self.current_capital += total_revenue
        del self.positions[symbol]
        
        # GUARDAR ESTADO
        self.save_portfolio()
        
        logger.info(f"🔴 VENTA INTERNA: {symbol} @ {price:.2f} | PnL: ${total_pnl:.2f} | Razón: {reason}")
        return total_pnl

    def print_summary(self):
        """Imprime resumen del portfolio."""
        total_value = self.get_total_portfolio_value()
        pnl = total_value - self.starting_capital
        pnl_pct = (pnl / self.starting_capital) * 100 if self.starting_capital > 0 else 0
        
        print("\n" + "="*60)
        print("📊 RESUMEN DEL PORTFOLIO (SIMULADOR INTERNO)")
        print("="*60)
        print(f"Capital Libre:     ${self.current_capital:,.2f}")
        print(f"Valor Total Estim: ${total_value:,.2f}")
        print(f"P&L Total:         ${pnl:,.2f} ({pnl_pct:+.2f}%)")
        print(f"Activos Abiertos:  {len(self.positions)}")
        print("-" * 60)
        
        for sym, pos_list in self.positions.items():
            total_qty = sum(p.quantity for p in pos_list)
            avg_price = sum(p.entry_price * p.quantity for p in pos_list) / total_qty
            print(f" • {sym:8} | Qty: {total_qty:.4f} | Avg Entry: ${avg_price:,.2f} | Compras: {len(pos_list)}")
            
        print("="*60 + "\n")