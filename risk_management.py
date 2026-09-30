"""
==============================================
GESTION DE RIESGOS - BOT TRADING
==============================================
Controla stop-loss, take-profit, posición sizing,
y protección del capital.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple
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
    trailing_stop_triggered: float = None
    
    def current_pnl(self, current_price: float) -> Tuple[float, float]:
        """Calcula P&L actual (valor y porcentaje)."""
        if self.side == "BUY":
            pnl = (current_price - self.entry_price) * self.quantity
            pnl_pct = (current_price - self.entry_price) / self.entry_price * 100
        else:  # SELL
            pnl = (self.entry_price - current_price) * self.quantity
            pnl_pct = (self.entry_price - current_price) / self.entry_price * 100
        
        return pnl, pnl_pct
    
    def should_exit(self, current_price: float) -> Tuple[bool, str]:
        """Determina si la posición debe cerrarse."""
        
        # Stop Loss
        if self.side == "BUY" and current_price <= self.stop_loss:
            return True, f"STOP_LOSS ({current_price:.2f} <= {self.stop_loss:.2f})"
        
        if self.side == "SELL" and current_price >= self.stop_loss:
            return True, f"STOP_LOSS ({current_price:.2f} >= {self.stop_loss:.2f})"
        
        # Take Profit
        if self.side == "BUY" and current_price >= self.take_profit:
            return True, f"TAKE_PROFIT ({current_price:.2f} >= {self.take_profit:.2f})"
        
        if self.side == "SELL" and current_price <= self.take_profit:
            return True, f"TAKE_PROFIT ({current_price:.2f} <= {self.take_profit:.2f})"
        
        # Trailing Stop
        if RISK_CONFIG["trailing_stop_enabled"]:
            if self.side == "BUY":
                trailing_level = self.trailing_stop_triggered or self.entry_price
                new_trailing = current_price * (1 - RISK_CONFIG["trailing_stop_pct"])
                if new_trailing > trailing_level:
                    self.trailing_stop_triggered = new_trailing
                # ← Añade esta verificación
                if self.trailing_stop_triggered is not None and current_price < self.trailing_stop_triggered:
                    return True, f"TRAILING_STOP"
        
        return False, ""
    
    def __str__(self):
        return f"{self.side} {self.quantity:.4f} {self.symbol} @ {self.entry_price:.2f}"


# ==========================================
# PORTFOLIO MANAGER
# ==========================================
class RiskManager:
    """Gestiona el capital, posiciones y riesgos."""
    
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.positions: dict[str, Position] = {}
        self.closed_trades: list = []
        self.trade_history: list = []
        self.peak_capital = initial_capital
        
    def get_available_capital(self) -> float:
        """Capital disponible para nuevas posiciones."""
        capital_in_positions = sum(pos.entry_price * pos.quantity for pos in self.positions.values())
        return self.current_capital - capital_in_positions
    
    def get_portfolio_value(self, current_prices: dict) -> Tuple[float, float]:
        """Valor total del portfolio y P&L."""
        portfolio_value = self.current_capital
        total_pnl = 0
        
        for symbol, position in self.positions.items():
            if symbol in current_prices:
                pnl, _ = position.current_pnl(current_prices[symbol])
                total_pnl += pnl
                portfolio_value += pnl
        
        return portfolio_value, total_pnl
    
    def get_drawdown(self) -> float:
        """Drawdown máximo desde pico."""
        return (self.current_capital - self.peak_capital) / self.peak_capital
    
    def calculate_position_size(self, 
                                symbol: str, 
                                entry_price: float, 
                                stop_loss_price: float) -> float:
        """
        Calcula cuántas unidades comprar basado en:
        - Riesgo máximo por trade (% del capital)
        - Distancia al stop loss
        
        Formula: Position_Size = (Capital * Risk%) / (Entry - Stop_Loss)
        """
        
        max_loss_amount = self.current_capital * RISK_CONFIG["risk_per_trade"]
        risk_per_unit = abs(entry_price - stop_loss_price)
        
        if risk_per_unit == 0:
            return 0
        
        position_size = max_loss_amount / risk_per_unit
        
        # Validaciones
        max_capital_per_position = self.current_capital * 0.15  # 15% max por posición
        max_units = max_capital_per_position / entry_price
        
        position_size = min(position_size, max_units)
        
        logger.info(
            f"[{symbol}] Position Size: {position_size:.4f} units | "
            f"Risk: ${max_loss_amount:.2f} | "
            f"Capital: ${self.current_capital:.2f}"
        )
        
        return position_size
    
    def open_position(self,
                     symbol: str,
                     entry_price: float,
                     side: str = "BUY",
                     stop_loss_pct: Optional[float] = None,
                     take_profit_pct: Optional[float] = None,
                     entry_date: str = "") -> Optional[Position]:
        """
        Abre una nueva posición con gestión automática de riesgo.
        
        Args:
            symbol: Símbolo del activo
            entry_price: Precio de entrada
            side: "BUY" o "SELL"
            stop_loss_pct: Porcentaje del stop loss (ej: 0.03 = 3%)
            take_profit_pct: Porcentaje del take profit
            entry_date: Fecha de entrada
        """
        
        # Validaciones
        if len(self.positions) >= RISK_CONFIG["max_positions"]:
            logger.warning(f"Máximo de posiciones alcanzado ({RISK_CONFIG['max_positions']})")
            return None
        
        if symbol in self.positions:
            logger.warning(f"{symbol} ya tiene una posición abierta")
            return None
        
        if self.get_drawdown() < -RISK_CONFIG["max_drawdown"]:
            logger.error("Drawdown máximo alcanzado. Stop automático.")
            return None
        
        # Calcular stops
        sl_pct = stop_loss_pct or RISK_CONFIG["stop_loss_pct"]
        tp_pct = take_profit_pct or RISK_CONFIG["take_profit_pct"]
        
        if side == "BUY":
            stop_loss = entry_price * (1 - sl_pct)
            take_profit = entry_price * (1 + tp_pct)
        else:  # SELL
            stop_loss = entry_price * (1 + sl_pct)
            take_profit = entry_price * (1 - tp_pct)
        
        # Calcular tamaño de posición
        quantity = self.calculate_position_size(symbol, entry_price, stop_loss)
        
        if quantity == 0:
            logger.error(f"No se puede abrir posición en {symbol}")
            return None
        
        # Crear posición
        position = Position(
            symbol=symbol,
            entry_price=entry_price,
            entry_date=entry_date,
            side=side,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.positions[symbol] = position
        
        logger.info(
            f"✅ POSICION ABIERTA: {position} | "
            f"SL: {stop_loss:.2f} | TP: {take_profit:.2f}"
        )
        
        return position
    
    def close_position(self,
                      symbol: str,
                      exit_price: float,
                      reason: str = "MANUAL",
                      exit_date: str = "") -> bool:
        """Cierra una posición abierta."""
        
        if symbol not in self.positions:
            logger.warning(f"No hay posición abierta en {symbol}")
            return False
        
        position = self.positions[symbol]
        pnl, pnl_pct = position.current_pnl(exit_price)
        
        # Registro de operación cerrada
        trade_record = {
            "symbol": symbol,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "quantity": position.quantity,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "entry_date": position.entry_date,
            "exit_date": exit_date
        }
        
        self.trade_history.append(trade_record)
        self.current_capital += pnl
        self.peak_capital = max(self.peak_capital, self.current_capital)
        
        del self.positions[symbol]
        
        emoji = "✅" if pnl > 0 else "❌"
        logger.info(
            f"{emoji} POSICION CERRADA: {symbol} | "
            f"P&L: ${pnl:.2f} ({pnl_pct:.2f}%) | "
            f"Razón: {reason}"
        )
        
        return True
    
    def get_summary(self) -> dict:
        """Resumen del portfolio."""
        total_trades = len(self.trade_history)
        winning_trades = sum(1 for t in self.trade_history if t["pnl"] > 0)
        losing_trades = sum(1 for t in self.trade_history if t["pnl"] < 0)
        total_pnl = sum(t["pnl"] for t in self.trade_history)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        avg_win = (sum(t["pnl"] for t in self.trade_history if t["pnl"] > 0) / winning_trades) if winning_trades > 0 else 0
        avg_loss = (sum(t["pnl"] for t in self.trade_history if t["pnl"] < 0) / losing_trades) if losing_trades > 0 else 0
        
        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "current_capital": self.current_capital,
            "open_positions": len(self.positions),
            "max_drawdown": self.get_drawdown(),
        }
    
    def print_summary(self):
        """Imprime un resumen bonito del portfolio."""
        summary = self.get_summary()
        print("\n" + "=" * 60)
        print("📊 RESUMEN DEL PORTFOLIO")
        print("=" * 60)
        print(f"Capital Inicial:   ${self.initial_capital:,.2f}")
        print(f"Capital Actual:    ${summary['current_capital']:,.2f}")
        print(f"P&L Total:         ${summary['total_pnl']:,.2f}")
        print(f"Posiciones Abiertas: {summary['open_positions']}")
        print("-" * 60)
        print(f"Total de Trades:   {summary['total_trades']}")
        print(f"Trades Ganadores:  {summary['winning_trades']}")
        print(f"Trades Perdedores: {summary['losing_trades']}")
        print(f"Win Rate:          {summary['win_rate']:.1f}%")
        print(f"Promedio Ganancia: ${summary['avg_win']:,.2f}")
        print(f"Promedio Pérdida:  ${summary['avg_loss']:,.2f}")
        print(f"Max Drawdown:      {summary['max_drawdown']*100:.2f}%")
        print("=" * 60 + "\n")
