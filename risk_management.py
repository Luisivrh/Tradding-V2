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
from config import RISK_CONFIG, ASSETS, MICROSTRUCTURE_CONFIG

logger = logging.getLogger(__name__)

# ==========================================
# POSICION (Trade)
# ==========================================
@dataclass
class Position:
    """Representa una posición abierta."""
    symbol: str
    asset_type: str
    entry_price: float
    entry_date: str
    side: str  # "BUY" o "SELL"
    quantity: float
    stop_loss: float
    take_profit: float
    atr_at_entry: float
    spread_at_entry: float
    trailing_stop_triggered: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    last_known_price: float = 0.0
    entry_score: float = 0.0

    def to_dict(self):
        return asdict(self)
        
    @classmethod
    def from_dict(cls, data):
        # Compatibility with older versions
        if 'lowest_price' not in data:
            data['lowest_price'] = data.get('entry_price', 0.0)
        return cls(**data)


# ==========================================
# GESTOR DE RIESGOS Y MEMORIA
# ==========================================
class RiskManager:
    """
    Simulador interno y gestor de riesgos con memoria.
    Guarda todas las compras en portfolio.json para evitar compras duplicadas.
    Registra operaciones cerradas en audit_log.json.
    """
    def __init__(self, starting_capital: float = None):
        self.portfolio_file = Path("portfolio.json")
        self.audit_file = Path("audit_log.json")
        self.starting_capital = starting_capital if starting_capital else RISK_CONFIG["starting_capital"]
        self.forex_starting_capital = RISK_CONFIG.get("forex_starting_capital", 10000.0)
        
        # Estado por defecto
        self.current_capital = self.starting_capital
        self.forex_capital = self.forex_starting_capital
        self.positions: Dict[str, List[Position]] = {} 
        self.trade_history = []
        self.max_pyramid = 3 # Máximo 3 compras (DCA) del mismo activo
        
        self.load_portfolio()
        if not self.audit_file.exists():
            with open(self.audit_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
        
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
                        # Compatibilidad con versiones viejas del json
                        for p in pos_list:
                            if 'asset_type' not in p: p['asset_type'] = 'normal'
                            if 'atr_at_entry' not in p: p['atr_at_entry'] = 0.0
                            if 'spread_at_entry' not in p: p['spread_at_entry'] = 0.0
                            if 'entry_score' not in p: p['entry_score'] = 0.0
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

    def categorize_asset(self, symbol: str, price: float) -> str:
        """Determina la microestructura del activo para asignar reglas específicas."""
        if symbol in ASSETS.get("forex", []): return "forex"
        if symbol in ASSETS.get("crypto", []) or "-USD" in symbol or "/" in symbol: return "crypto"
        
        # Warrants normalmente terminan en W o WW y tienen precios muy bajos
        if (symbol.endswith('W') or symbol.endswith('WW')) and len(symbol) >= 4:
            return "warrant"
        
        if price < 1.0:
            return "warrant" # Tratamos ultra-microcaps como warrants por su volatilidad
            
        if price < 5.0:
            return "penny"
            
        return "normal"

    def sync_with_broker(self, alpaca_client):
        """Sincroniza el portafolio local con las posiciones reales de Alpaca."""
        if not alpaca_client:
            return
            
        try:
            account = alpaca_client.get_account()
            self.current_capital = float(account.cash)
            
            real_positions = alpaca_client.get_all_positions()
            real_symbols = []
            
            logger.info("🔄 Sincronizando portafolio interno con Alpaca...")
            
            for pos in real_positions:
                symbol = pos.symbol.replace("/", "-") if "/" in pos.symbol else pos.symbol
                real_symbols.append(symbol)
                
                qty = float(pos.qty)
                avg_price = float(pos.avg_entry_price)
                current_price = float(pos.current_price)
                asset_type = self.categorize_asset(symbol, avg_price)
                
                side_str = "BUY" if "long" in str(pos.side).lower() else "SELL"
                
                # Si no está localmente, lo agregamos con stops genéricos de la categoría
                if symbol not in self.positions:
                    rules = MICROSTRUCTURE_CONFIG[asset_type]
                    # Asumimos un ATR del 5% del precio si no tenemos histórico
                    fallback_atr = avg_price * 0.05 
                    sl_dist = fallback_atr * rules["atr_sl_mult"]
                    
                    stop_loss = avg_price - sl_dist if side_str == "BUY" else avg_price + sl_dist
                    take_profit = avg_price * (1 + RISK_CONFIG["take_profit_pct"]) if side_str == "BUY" else avg_price * (1 - RISK_CONFIG["take_profit_pct"])
                    
                    self.positions[symbol] = [Position(
                        symbol=symbol,
                        asset_type=asset_type,
                        entry_price=avg_price,
                        entry_date=str(datetime.now()), 
                        side=side_str,
                        quantity=qty,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        atr_at_entry=fallback_atr,
                        spread_at_entry=0.0,
                        highest_price=max(avg_price, current_price),
                        last_known_price=current_price
                    )]
                else:
                    # Actualizar valores sin perder configuración local (DCA)
                    # Simplificado: si Alpaca tiene una qty diferente, asumimos reajuste
                    pass 
                
            forex_symbols = ASSETS.get("forex", [])
            symbols_to_remove = [sym for sym in self.positions.keys() if sym not in real_symbols and sym not in forex_symbols]
                    
            for sym in symbols_to_remove:
                del self.positions[sym]
                
            self.save_portfolio()
            
        except Exception as e:
            logger.error(f"Error sincronizando con Alpaca: {e}")

    def get_total_portfolio_value(self, is_forex: bool = False) -> float:
        if is_forex:
            forex_symbols = ASSETS.get("forex", [])
            pos_value = sum(p.quantity * p.entry_price for sym, pos_list in self.positions.items() if sym in forex_symbols for p in pos_list)
            return self.forex_capital + pos_value
        else:
            forex_symbols = ASSETS.get("forex", [])
            pos_value = sum(p.quantity * p.entry_price for sym, pos_list in self.positions.items() if sym not in forex_symbols for p in pos_list)
            return self.current_capital + pos_value

    def get_drawdown(self) -> float:
        current_val = self.get_total_portfolio_value(is_forex=False)
        if current_val >= self.starting_capital: return 0.0
        return (self.starting_capital - current_val) / self.starting_capital

    def _calculate_position_size(self, entry_price: float, atr: float, asset_type: str, is_forex: bool = False) -> float:
        """
        Calcula el tamaño dinámico usando ATR y Límites de Categoría.
        Esto previene destrozar la cuenta con un Warrant de 10% del portafolio.
        """
        total_value = self.get_total_portfolio_value(is_forex)
        rules = MICROSTRUCTURE_CONFIG[asset_type]
        
        # 1. ¿Cuánto capital máximo podemos asignar a este tipo de activo?
        max_capital_allowed = total_value * rules["max_pos_pct"]
        
        # 2. Risk Parity (Volatilidad)
        # Queremos arriesgar RISK_CONFIG["risk_per_trade"] (Ej. 1%) de la cuenta.
        # ¿Cuánto dinero perdemos por acción si toca el stop?
        risk_amount = total_value * RISK_CONFIG["risk_per_trade"]
        # Distancia al stop
        per_share_risk = atr * rules["atr_sl_mult"]
        
        if per_share_risk <= 0:
            return 0
            
        units_by_risk = risk_amount / per_share_risk
        capital_by_risk = units_by_risk * entry_price
        
        # Limitamos por el capital máximo permitido para la categoría
        final_capital = min(capital_by_risk, max_capital_allowed)
        
        # Validar capital líquido real
        available_cash = self.forex_capital if is_forex else self.current_capital
        final_capital = min(final_capital, available_cash)
        
        return final_capital / entry_price

    def can_open_position(self, symbol: str, price: float, spread_pct: float, asset_type: str) -> bool:
        """Reglas de filtrado anti-amnesia, liquidez y sector."""
        rules = MICROSTRUCTURE_CONFIG[asset_type]
        
        if spread_pct > rules["max_spread_pct"]:
            logger.warning(f"[{symbol}] Rechazado: Spread excesivo ({spread_pct*100:.2f}% > {rules['max_spread_pct']*100:.2f}%) para categoría {asset_type.upper()}.")
            return False
            
        # Contar por bloque general
        macro_group = "forex" if asset_type == "forex" else ("crypto" if asset_type == "crypto" else "stocks")
        open_positions = sum(1 for sym in self.positions.keys() if (
            (macro_group == "forex" and sym in ASSETS.get("forex", [])) or
            (macro_group == "crypto" and sym in ASSETS.get("crypto", [])) or
            (macro_group == "stocks" and sym not in ASSETS.get("forex", []) and sym not in ASSETS.get("crypto", []))
        ))
        
        max_limit = RISK_CONFIG.get("max_positions_per_type", {}).get(macro_group, 5)
        
        if open_positions >= max_limit:
            return False
            
        if symbol in self.positions and len(self.positions[symbol]) > 0:
            pos_list = self.positions[symbol]
            if len(pos_list) >= self.max_pyramid: return False
            today = str(datetime.now().date())
            if today == pos_list[-1].entry_date[:10]: return False
            avg_entry_price = sum(p.entry_price * p.quantity for p in pos_list) / sum(p.quantity for p in pos_list)
            if price > (avg_entry_price * 0.95): return False

        return True

    def open_position(self, symbol: str, price: float, side: str, atr: float, spread_pct: float, score: float, entry_date: str) -> Optional[Position]:
        """Abre posición usando reglas ATR y Límites Estrictos."""
        
        asset_type = self.categorize_asset(symbol, price)
        if not self.can_open_position(symbol, price, spread_pct, asset_type):
            return None
        
        is_forex = asset_type == "forex"
        rules = MICROSTRUCTURE_CONFIG[asset_type]
        
        # Evitar ATR 0
        safe_atr = atr if atr > 0 else price * 0.05
        
        quantity = self._calculate_position_size(price, safe_atr, asset_type, is_forex)
        if quantity <= 0: return None
        
        # Calcular stops usando ATR
        stop_dist = safe_atr * rules["atr_sl_mult"]
        if side == "BUY":
            stop_loss = price - stop_dist
            take_profit = price * (1 + RISK_CONFIG["take_profit_pct"])
        else: # SELL
            stop_loss = price + stop_dist
            take_profit = price * (1 - RISK_CONFIG["take_profit_pct"])
            
        cost = quantity * price
        
        if is_forex:
            self.forex_capital -= cost
        else:
            self.current_capital -= cost
        
        position = Position(
            symbol=symbol,
            asset_type=asset_type,
            entry_price=price,
            quantity=quantity,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_date=entry_date,
            highest_price=price,
            lowest_price=price,
            last_known_price=price,
            atr_at_entry=safe_atr,
            spread_at_entry=spread_pct,
            entry_score=score
        )
        
        if symbol not in self.positions:
            self.positions[symbol] = []
            
        self.positions[symbol].append(position)
        self.save_portfolio()
        
        logger.info(f"✅ POSICION ABIERTA ({asset_type.upper()}): {side} {quantity:.4f} {symbol} @ {price:.2f} | SL: {stop_loss:.2f} | Spread: {spread_pct*100:.2f}%")
        return position

    def check_exit_conditions(self, current_prices: Dict[str, Dict]) -> List[Dict]:
        """
        current_prices: { 'AAPL': {'price': 150.0, 'atr': 3.5, 'spread': 0.001}, ... }
        Revisa posiciones abiertas contra precios y ajusta Chandelier Trailing Stop (ATR).
        """
        forced_exits = []
        
        for symbol, pos_list in list(self.positions.items()):
            if symbol not in current_prices:
                continue
                
            info = current_prices[symbol]
            current_price = info.get('price', 0)
            current_atr = info.get('atr', 0)
            current_spread = info.get('spread', 0)
            
            sell_reasons = []
            
            for i, pos in enumerate(pos_list):
                pos.last_known_price = current_price
                pos.lowest_price = min(pos.lowest_price, current_price)
                
                rules = MICROSTRUCTURE_CONFIG.get(pos.asset_type, MICROSTRUCTURE_CONFIG["normal"])
                
                # 1. Trailing Stop Adaptativo (ATR)
                if current_price > pos.highest_price and pos.side == "BUY":
                    pos.highest_price = current_price
                    if RISK_CONFIG.get("trailing_stop_enabled", True):
                        safe_atr = current_atr if current_atr > 0 else pos.atr_at_entry
                        ts_dist = safe_atr * rules["atr_ts_mult"]
                        new_trailing_sl = pos.highest_price - ts_dist
                        
                        # El stop solo sube
                        if new_trailing_sl > pos.stop_loss:
                            pos.stop_loss = new_trailing_sl
                            
                # 2. Evaluar Stop Loss (Fijo inicial o Trailing actual)
                if pos.side == "BUY" and current_price <= pos.stop_loss:
                    sell_reasons.append(f"STOP_LOSS_ATR (Trigger en ${pos.stop_loss:.4f})")
                    
                # 3. Take Profit (Mantenemos Fijo para asegurar salidas)
                elif pos.side == "BUY" and current_price >= pos.take_profit:
                    sell_reasons.append(f"TAKE_PROFIT (Meta ${pos.take_profit:.2f})")
                    
                # 4. Timeout
                else:
                    days_held = (datetime.now() - datetime.fromisoformat(pos.entry_date)).days
                    if days_held >= RISK_CONFIG.get("max_hold_days", 30):
                        sell_reasons.append(f"MAX_TIME ({days_held} días)")

            if sell_reasons:
                reason = " | ".join(set(sell_reasons))
                forced_exits.append({
                    "symbol": symbol,
                    "signal_type": "SELL",
                    "score": 0.0, # Fuerza Venta Absoluta
                    "current_price": current_price,
                    "details": {"reason": reason, "price": current_price, "atr": current_atr, "spread": current_spread}
                })
                
        self.save_portfolio()
        return forced_exits

    def close_position(self, symbol: str, price: float, reason: str, exit_date: str, exit_atr: float = 0.0, exit_spread: float = 0.0, exit_score: float = 0.0) -> float:
        """Cierra todas las posiciones de un activo, guarda historial y escribe en Audit Log."""
        if symbol not in self.positions or not self.positions[symbol]:
            return 0.0
            
        total_pnl = 0.0
        total_revenue = 0.0
        is_forex = MICROSTRUCTURE_CONFIG.get(self.positions[symbol][0].asset_type) == "forex"
        
        audit_entries = []
        
        for pos in self.positions[symbol]:
            # Always ensure MFE/MAE are relative to final values
            pos.highest_price = max(pos.highest_price, price)
            pos.lowest_price = min(pos.lowest_price, price)
            
            mfe_pct = (pos.highest_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            mae_pct = (pos.lowest_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            
            if pos.side == "BUY":
                pnl = (price - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - price) * pos.quantity
                
            total_pnl += pnl
            cost_basis = pos.entry_price * pos.quantity
            total_revenue += cost_basis + pnl
            
            pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
            
            dt_in = datetime.fromisoformat(pos.entry_date)
            dt_out = datetime.fromisoformat(exit_date)
            hold_hours = (dt_out - dt_in).total_seconds() / 3600.0
            
            # Record para historial interno
            hist_entry = {
                "symbol": symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": price,
                "pnl": pnl,
                "reason": reason,
                "date": exit_date
            }
            self.trade_history.append(hist_entry)
            
            # Record para Audit Log (Analítico)
            audit_entries.append({
                "symbol": symbol,
                "asset_type": pos.asset_type,
                "entry_price": pos.entry_price,
                "exit_price": price,
                "qty": pos.quantity,
                "cost_basis": cost_basis,
                "profit_usd": pnl,
                "profit_pct": pnl_pct,
                "holding_time_hours": hold_hours,
                "reason": reason,
                "spread_entry": pos.spread_at_entry,
                "spread_exit": exit_spread,
                "atr_entry": pos.atr_at_entry,
                "atr_exit": exit_atr,
                "score_compra": pos.entry_score,
                "score_venta": exit_score,
                "mfe_price": pos.highest_price,
                "mae_price": pos.lowest_price,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
                "price_after_1d": None,
                "missed_profit_1d": None,
                "price_after_3d": None,
                "missed_profit_3d": None,
                "price_after_5d": None,
                "missed_profit_5d": None,
                "price_after_10d": None,
                "missed_profit_10d": None,
                "exit_quality_score": "Pendiente",
                "entry_date": pos.entry_date,
                "exit_date": exit_date
            })
            
        if is_forex: self.forex_capital += total_revenue
        else: self.current_capital += total_revenue
            
        del self.positions[symbol]
        self.save_portfolio()
        
        # Guardar en audit_log.json
        try:
            with open(self.audit_file, 'r+', encoding='utf-8') as f:
                logs = json.load(f)
                logs.extend(audit_entries)
                f.seek(0)
                json.dump(logs, f, indent=4)
        except Exception as e:
            logger.error(f"Error escribiendo en audit_log.json: {e}")
        
        logger.info(f"🔴 VENTA INTERNA: {symbol} @ {price:.4f} | PnL: ${total_pnl:.2f} | Razón: {reason}")
        return total_pnl

    def print_summary(self):
        # (Impresión idéntica pero respetando la nueva estructura)
        # Omitimos cambios estéticos aquí por espacio, asume compatibilidad.
        forex_symbols = ASSETS.get("forex", [])
        real_invested_cost = 0.0; simulated_invested_cost = 0.0
        real_current_value = 0.0; simulated_current_value = 0.0
        real_positions_count = 0; simulated_positions_count = 0
        
        for sym, pos_list in self.positions.items():
            is_forex = sym in forex_symbols
            for p in pos_list:
                cost = p.quantity * p.entry_price
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
                
        real_portfolio_total = self.current_capital + real_current_value
        pnl_real = real_portfolio_total - self.starting_capital
        pnl_real_pct = (pnl_real / self.starting_capital) * 100 if self.starting_capital > 0 else 0
        
        forex_portfolio_total = self.forex_capital + simulated_current_value
        pnl_forex = forex_portfolio_total - self.forex_starting_capital
        pnl_forex_pct = (pnl_forex / self.forex_starting_capital) * 100 if self.forex_starting_capital > 0 else 0
        
        summary_str = "="*70 + "\n📊 RESUMEN DE PORTAFOLIOS INDEPENDIENTES\n" + "="*70 + "\n"
        summary_str += f"📈 [1] CUENTA REAL (ALPACA) -> Total: ${real_portfolio_total:,.2f} | P&L: {pnl_real_pct:+.2f}%\n"
        summary_str += f"🕹️ [2] CUENTA SIMULADA (FOREX) -> Total: ${forex_portfolio_total:,.2f} | P&L: {pnl_forex_pct:+.2f}%\n"
        summary_str += "="*70 + "\n"
        
        for sym, pos_list in self.positions.items():
            total_qty = sum(p.quantity for p in pos_list)
            avg_price = sum(p.entry_price * p.quantity for p in pos_list) / total_qty
            last_price = pos_list[0].last_known_price if pos_list[0].last_known_price > 0 else avg_price
            indiv_pnl_pct = ((last_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0
            tag = pos_list[0].asset_type.upper()
            summary_str += f" • {sym:8} [{tag}] | Qty: {total_qty:10.4f} | Avg Entry: ${avg_price:,.4f} | P&L: {indiv_pnl_pct:+.2f}%\n"
        return summary_str