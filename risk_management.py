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
from config import RISK_CONFIG, ASSETS

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
    last_known_price: float = 0.0 # Guardar último precio detectado

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
        self.forex_starting_capital = RISK_CONFIG.get("forex_starting_capital", 10000.0)
        
        # Estado por defecto
        self.current_capital = self.starting_capital
        self.forex_capital = self.forex_starting_capital
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
                    self.starting_capital = data.get("starting_capital", self.starting_capital)
                    self.current_capital = data.get("current_capital", self.starting_capital)
                    
                    self.forex_starting_capital = data.get("forex_starting_capital", self.forex_starting_capital)
                    self.forex_capital = data.get("forex_capital", self.forex_starting_capital)
                    
                    self.trade_history = data.get("trade_history", [])
                    
                    # Cargar posiciones
                    raw_positions = data.get("positions", {})
                    for sym, pos_list in raw_positions.items():
                        self.positions[sym] = [Position.from_dict(p) for p in pos_list]
                        
                logger.info(f"💾 Portafolio cargado: Capital Alpaca ${self.current_capital:.2f} | Capital Forex ${self.forex_capital:.2f}")
            except Exception as e:
                logger.error(f"Error cargando portfolio.json: {e}. Iniciando en limpio.")
                self.current_capital = self.starting_capital
                self.forex_capital = self.forex_starting_capital
                self.positions = {}
        else:
            logger.info(f"🆕 Iniciando nuevo portafolio de simulación con ${self.current_capital:.2f} (Real) y ${self.forex_capital:.2f} (Forex)")

    def save_portfolio(self):
        """Guarda el estado del portafolio en el disco."""
        try:
            data = {
                "starting_capital": self.starting_capital,
                "current_capital": self.current_capital,
                "forex_starting_capital": self.forex_starting_capital,
                "forex_capital": self.forex_capital,
                "positions": {sym: [p.to_dict() for p in pos_list] for sym, pos_list in self.positions.items()},
                "trade_history": self.trade_history
            }
            with open(self.portfolio_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando portfolio.json: {e}")

    def sync_with_broker(self, alpaca_client):
        """Sincroniza el portafolio local con las posiciones reales de Alpaca."""
        if not alpaca_client:
            return
            
        try:
            # 1. Sincronizar el capital real disponible
            account = alpaca_client.get_account()
            self.current_capital = float(account.cash)
            
            # 2. Obtener posiciones reales
            real_positions = alpaca_client.get_all_positions()
            real_symbols = []
            
            logger.info("🔄 Sincronizando portafolio interno con Alpaca...")
            logger.info(f"💰 Poder de compra (Cash) actualizado a: ${self.current_capital:,.2f}")
            
            for pos in real_positions:
                # Convertir Alpaca (ej. BTC/USD) a formato interno (BTC-USD)
                symbol = pos.symbol.replace("/", "-") if "/" in pos.symbol else pos.symbol
                real_symbols.append(symbol)
                
                qty = float(pos.qty)
                avg_price = float(pos.avg_entry_price)
                current_price = float(pos.current_price)
                
                # Alpaca side es un Enum
                side_str = "BUY" if "long" in str(pos.side).lower() else "SELL"
                
                # Calcular nuevos Stops basados en precio real
                if side_str == "BUY":
                    stop_loss = avg_price * (1 - RISK_CONFIG["stop_loss_pct"])
                    take_profit = avg_price * (1 + RISK_CONFIG["take_profit_pct"])
                else:
                    stop_loss = avg_price * (1 + RISK_CONFIG["stop_loss_pct"])
                    take_profit = avg_price * (1 - RISK_CONFIG["take_profit_pct"])
                    
                # Reemplazamos/Consolidamos la posición local con la realidad
                self.positions[symbol] = [Position(
                    symbol=symbol,
                    entry_price=avg_price,
                    entry_date=str(datetime.now()), 
                    side=side_str,
                    quantity=qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    highest_price=max(avg_price, current_price),
                    last_known_price=current_price
                )]
                logger.info(f"✅ Sincronizado {symbol}: Qty {qty:,.4f}, Avg Entry ${avg_price:,.4f}")
                
            # 3. Eliminar activos locales que ya no existen en Alpaca (excepto Forex)
            forex_symbols = ASSETS.get("forex", [])
            symbols_to_remove = []
            
            for local_sym in self.positions.keys():
                if local_sym not in real_symbols and local_sym not in forex_symbols:
                    logger.warning(f"🗑️ Eliminando {local_sym} del portafolio local (Posición cerrada o no existe en Alpaca).")
                    symbols_to_remove.append(local_sym)
                    
            for sym in symbols_to_remove:
                del self.positions[sym]
                
            # Guardar el estado unificado
            self.save_portfolio()
            logger.info("✅ Sincronización completada con éxito.")
            
        except Exception as e:
            logger.error(f"Error sincronizando con Alpaca: {e}")

    def get_total_portfolio_value(self, is_forex: bool = False) -> float:
        """Estima el valor total (capital líquido + valor invertido) para la cuenta correspondiente."""
        if is_forex:
            forex_symbols = ASSETS.get("forex", [])
            pos_value = sum(
                p.quantity * p.entry_price 
                for sym, pos_list in self.positions.items() if sym in forex_symbols
                for p in pos_list
            )
            return self.forex_capital + pos_value
        else:
            forex_symbols = ASSETS.get("forex", [])
            pos_value = sum(
                p.quantity * p.entry_price 
                for sym, pos_list in self.positions.items() if sym not in forex_symbols
                for p in pos_list
            )
            return self.current_capital + pos_value

    def get_drawdown(self) -> float:
        """Calcula la pérdida actual vs capital inicial (Solo cuenta real por ahora)."""
        current_val = self.get_total_portfolio_value(is_forex=False)
        if current_val >= self.starting_capital:
            return 0.0
        return (self.starting_capital - current_val) / self.starting_capital

    def _calculate_position_size(self, entry_price: float, stop_loss_price: float, is_forex: bool = False) -> float:
        """Calcula el tamaño de posición basado en riesgo y la cuenta correspondiente."""
        total_value = self.get_total_portfolio_value(is_forex)
        risk_amount = total_value * RISK_CONFIG["risk_per_trade"]
        price_risk_per_unit = abs(entry_price - stop_loss_price)
        
        if price_risk_per_unit <= 0:
            return 0
            
        units = risk_amount / price_risk_per_unit
        
        # Validar que tengamos suficiente capital líquido en la cuenta correcta
        available_cash = self.forex_capital if is_forex else self.current_capital
        max_units_affordable = available_cash / entry_price
        return min(units, max_units_affordable)

    def _get_asset_type(self, symbol: str) -> str:
        """Determina el tipo de activo basado en la config."""
        if symbol in ASSETS.get("forex", []):
            return "forex"
        if symbol in ASSETS.get("crypto", []) or "-USD" in symbol or "/" in symbol:
            return "crypto"
        return "stocks"

    def can_open_position(self, symbol: str, price: float) -> bool:
        """Reglas estrictas anti-amnesia y límites independientes por sector."""
        asset_type = self._get_asset_type(symbol)
        
        # Contar posiciones actuales de este TIPO
        open_positions_of_type = sum(
            1 for sym in self.positions.keys() if self._get_asset_type(sym) == asset_type
        )
        
        # Obtener el límite específico (por defecto 5 si no existe)
        max_limit = RISK_CONFIG.get("max_positions_per_type", {}).get(asset_type, 5)
        
        if open_positions_of_type >= max_limit:
            logger.warning(f"[{symbol}] Rechazado: Máximo de posiciones alcanzado para {asset_type.upper()} ({open_positions_of_type}/{max_limit}).")
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
        
        is_forex = self._get_asset_type(symbol) == "forex"
        
        # Calcular stops
        if side == "BUY":
            stop_loss = price * (1 - RISK_CONFIG["stop_loss_pct"])
            take_profit = price * (1 + RISK_CONFIG["take_profit_pct"])
        else: # SELL
            stop_loss = price * (1 + RISK_CONFIG["stop_loss_pct"])
            take_profit = price * (1 - RISK_CONFIG["take_profit_pct"])
            
        quantity = self._calculate_position_size(price, stop_loss, is_forex)
        
        if quantity <= 0:
            logger.warning(f"[{symbol}] Capital insuficiente o riesgo demasiado alto.")
            return None
            
        cost = quantity * price
        
        # Validar y restar saldo de la cuenta correcta
        if is_forex:
            if cost > self.forex_capital:
                logger.warning(f"[{symbol}] Capital Forex insuficiente (${self.forex_capital:.2f} < ${cost:.2f})")
                return None
            self.forex_capital -= cost
            available_capital = self.forex_capital
        else:
            if cost > self.current_capital:
                logger.warning(f"[{symbol}] Capital Alpaca insuficiente (${self.current_capital:.2f} < ${cost:.2f})")
                return None
            self.current_capital -= cost
            available_capital = self.current_capital
        
        position = Position(
            symbol=symbol,
            entry_price=price,
            quantity=quantity,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_date=entry_date,
            highest_price=price,
            last_known_price=price
        )
        
        if symbol not in self.positions:
            self.positions[symbol] = []
            
        self.positions[symbol].append(position)
        
        # GUARDAR ESTADO
        self.save_portfolio()
        
        logger.info(f"[{symbol}] Position Size: {quantity:.4f} units | Risk: ${(abs(price-stop_loss)*quantity):.2f} | Capital Restante: ${available_capital:.2f}")
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
                # Guardar el precio actual para mostrar el P&L en tiempo real
                pos.last_known_price = current_price
                
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
                
        # Guardar si hubo actualizaciones
        self.save_portfolio()
        return forced_exits

    def close_position(self, symbol: str, price: float, reason: str, exit_date: str) -> float:
        """Cierra todas las posiciones de un activo (liquidar) y devuelve el PnL."""
        if symbol not in self.positions or not self.positions[symbol]:
            return 0.0
            
        total_pnl = 0.0
        total_revenue = 0.0
        is_forex = self._get_asset_type(symbol) == "forex"
        
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
            
        if is_forex:
            self.forex_capital += total_revenue
        else:
            self.current_capital += total_revenue
            
        del self.positions[symbol]
        
        # GUARDAR ESTADO
        self.save_portfolio()
        
        logger.info(f"🔴 VENTA INTERNA: {symbol} @ {price:.2f} | PnL: ${total_pnl:.2f} | Razón: {reason}")
        return total_pnl

    def print_summary(self):
        """Imprime resumen del portfolio separando real (Alpaca) de simulado (Forex) con PnL flotante."""
        forex_symbols = ASSETS.get("forex", [])
        
        # Totales Invertidos (Costo Base)
        real_invested_cost = 0.0
        simulated_invested_cost = 0.0
        
        # Totales Actuales (Usando último precio conocido)
        real_current_value = 0.0
        simulated_current_value = 0.0
        
        real_positions_count = 0
        simulated_positions_count = 0
        
        # Cálculos por posición
        for sym, pos_list in self.positions.items():
            is_forex = sym in forex_symbols
            for p in pos_list:
                cost = p.quantity * p.entry_price
                
                # Si tenemos un último precio conocido (actualizado por check_exit_conditions)
                current_price = p.last_known_price if p.last_known_price > 0 else p.entry_price
                current_value = p.quantity * current_price
                
                if is_forex:
                    simulated_invested_cost += cost
                    simulated_current_value += current_value
                    simulated_positions_count += 1
                else:
                    real_invested_cost += cost
                    real_current_value += current_value
                    real_positions_count += 1
                
        # --- CARTERA REAL (ALPACA) ---
        real_portfolio_total = self.current_capital + real_current_value
        pnl_real = real_portfolio_total - self.starting_capital
        pnl_real_pct = (pnl_real / self.starting_capital) * 100 if self.starting_capital > 0 else 0
        
        # --- CARTERA SIMULADA (FOREX) ---
        forex_portfolio_total = self.forex_capital + simulated_current_value
        pnl_forex = forex_portfolio_total - self.forex_starting_capital
        pnl_forex_pct = (pnl_forex / self.forex_starting_capital) * 100 if self.forex_starting_capital > 0 else 0
        
        print("\n" + "="*70)
        print("📊 RESUMEN DE PORTAFOLIOS INDEPENDIENTES")
        print("="*70)
        
        print(f"📈 [1] CUENTA REAL (ALPACA - ACCIONES/CRIPTOS)")
        print(f"   Saldo Inicial:        ${self.starting_capital:,.2f}")
        print(f"   Efectivo Libre:       ${self.current_capital:,.2f}")
        print(f"   Valor Invertido:      ${real_current_value:,.2f} (Costo: ${real_invested_cost:,.2f})")
        print(f"   Valor Total Actual:   ${real_portfolio_total:,.2f}")
        print(f"   P&L Flotante Total:   ${pnl_real:,.2f} ({pnl_real_pct:+.2f}%)")
        print(f"   Posiciones Abiertas:  {real_positions_count} / {RISK_CONFIG.get('max_positions_per_type', {}).get('stocks', 5) + RISK_CONFIG.get('max_positions_per_type', {}).get('crypto', 5)}")
        print("-" * 70)
        
        print(f"🕹️ [2] CUENTA SIMULADA (FOREX)")
        print(f"   Saldo Inicial:        ${self.forex_starting_capital:,.2f}")
        print(f"   Efectivo Libre:       ${self.forex_capital:,.2f}")
        print(f"   Valor Invertido:      ${simulated_current_value:,.2f} (Costo: ${simulated_invested_cost:,.2f})")
        print(f"   Valor Total Actual:   ${forex_portfolio_total:,.2f}")
        print(f"   P&L Flotante Total:   ${pnl_forex:,.2f} ({pnl_forex_pct:+.2f}%)")
        print(f"   Posiciones Abiertas:  {simulated_positions_count} / {RISK_CONFIG.get('max_positions_per_type', {}).get('forex', 5)}")
        print("=" * 70)
        
        for sym, pos_list in self.positions.items():
            total_qty = sum(p.quantity for p in pos_list)
            avg_price = sum(p.entry_price * p.quantity for p in pos_list) / total_qty
            
            # Obtener P&L individual
            last_price = pos_list[0].last_known_price if pos_list[0].last_known_price > 0 else avg_price
            indiv_pnl = (last_price - avg_price) * total_qty
            indiv_pnl_pct = ((last_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0
            
            tag = "[SIM_FOREX]" if sym in forex_symbols else "[REAL_ALPACA]"
            print(f" • {sym:8} {tag:13} | Qty: {total_qty:10.4f} | Avg Entry: ${avg_price:,.4f} | P&L: {indiv_pnl_pct:+.2f}%")
            
        print("="*70 + "\n")

        # Preparar string para retornar (sin códigos de escape ANSI)
        summary_str = "="*70 + "\n"
        summary_str += "📊 RESUMEN DE PORTAFOLIOS INDEPENDIENTES\n"
        summary_str += "="*70 + "\n"
        summary_str += "📈 [1] CUENTA REAL (ALPACA - ACCIONES/CRIPTOS)\n"
        summary_str += f"   Saldo Inicial:        ${self.starting_capital:,.2f}\n"
        summary_str += f"   Efectivo Libre:       ${self.current_capital:,.2f}\n"
        summary_str += f"   Valor Invertido:      ${real_current_value:,.2f} (Costo: ${real_invested_cost:,.2f})\n"
        summary_str += f"   Valor Total Actual:   ${real_portfolio_total:,.2f}\n"
        summary_str += f"   P&L Flotante Total:   ${pnl_real:,.2f} ({pnl_real_pct:+.2f}%)\n"
        summary_str += f"   Posiciones Abiertas:  {real_positions_count} / {RISK_CONFIG.get('max_positions_per_type', {}).get('stocks', 5) + RISK_CONFIG.get('max_positions_per_type', {}).get('crypto', 5)}\n"
        summary_str += "-" * 70 + "\n"
        summary_str += "🕹️ [2] CUENTA SIMULADA (FOREX)\n"
        summary_str += f"   Saldo Inicial:        ${self.forex_starting_capital:,.2f}\n"
        summary_str += f"   Efectivo Libre:       ${self.forex_capital:,.2f}\n"
        summary_str += f"   Valor Invertido:      ${simulated_current_value:,.2f} (Costo: ${simulated_invested_cost:,.2f})\n"
        summary_str += f"   Valor Total Actual:   ${forex_portfolio_total:,.2f}\n"
        summary_str += f"   P&L Flotante Total:   ${pnl_forex:,.2f} ({pnl_forex_pct:+.2f}%)\n"
        summary_str += f"   Posiciones Abiertas:  {simulated_positions_count} / {RISK_CONFIG.get('max_positions_per_type', {}).get('forex', 5)}\n"
        summary_str += "=" * 70 + "\n"

        for sym, pos_list in self.positions.items():
            total_qty = sum(p.quantity for p in pos_list)
            avg_price = sum(p.entry_price * p.quantity for p in pos_list) / total_qty
            last_price = pos_list[0].last_known_price if pos_list[0].last_known_price > 0 else avg_price
            indiv_pnl_pct = ((last_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0
            tag = "[SIM_FOREX]" if sym in forex_symbols else "[REAL_ALPACA]"
            summary_str += f" • {sym:8} {tag:13} | Qty: {total_qty:10.4f} | Avg Entry: ${avg_price:,.4f} | P&L: {indiv_pnl_pct:+.2f}%\n"

        summary_str += "="*70 + "\n"
        
        return summary_str