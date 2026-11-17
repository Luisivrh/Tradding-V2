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
from typing import Optional, Dict, List, Tuple
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
    def __init__(self, starting_capital: float = None, in_memory: bool = False, load_snapshot: bool = False):
        """
        in_memory=True: nunca escribe portfolio.json ni audit_log.json.
        load_snapshot=True (solo con in_memory): carga portfolio.json en modo SOLO LECTURA
        para que el dry-run evalúe las posiciones reales. El backtester lo deja en False.
        """
        self.portfolio_file = Path("portfolio.json")
        self.audit_file = Path("audit_log.json")
        self.in_memory = in_memory
        self.load_snapshot = load_snapshot
        self.starting_capital = starting_capital if starting_capital else RISK_CONFIG["starting_capital"]
        self.forex_starting_capital = RISK_CONFIG.get("forex_starting_capital", 10000.0)
        
        # Estado por defecto
        self.current_capital = self.starting_capital
        self.forex_capital = self.forex_starting_capital
        self.positions: Dict[str, List[Position]] = {} 
        self.trade_history = []
        self.max_pyramid = 3 # Máximo 3 compras (DCA) del mismo activo
        
        # Baselines para Freno de Emergencia por Pérdidas (Circuit Breaker)
        self.daily_baseline_date = None
        self.daily_baseline_equity = None
        self.weekly_baseline_week = None
        self.weekly_baseline_equity = None
        
        if not self.in_memory:
            self.load_portfolio()
            if not self.audit_file.exists():
                with open(self.audit_file, 'w', encoding='utf-8') as f:
                    json.dump([], f)
        elif self.load_snapshot:
            self.load_portfolio()  # Solo lectura: save_portfolio() sigue bloqueado por in_memory

    def load_portfolio(self):
        """Carga el estado del portafolio desde el disco (Anti-Amnesia)."""
        if self.in_memory and not self.load_snapshot:
            return
        if self.portfolio_file.exists():
            try:
                with open(self.portfolio_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.starting_capital = data.get("starting_capital", self.starting_capital)
                    self.current_capital = data.get("current_capital", self.starting_capital)
                    
                    self.forex_starting_capital = data.get("forex_starting_capital", self.forex_starting_capital)
                    self.forex_capital = data.get("forex_capital", self.forex_starting_capital)
                    
                    self.daily_baseline_date = data.get("daily_baseline_date")
                    self.daily_baseline_equity = data.get("daily_baseline_equity")
                    self.weekly_baseline_week = data.get("weekly_baseline_week")
                    self.weekly_baseline_equity = data.get("weekly_baseline_equity")
                    
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
        if self.in_memory:
            return
        try:
            data = {
                "starting_capital": self.starting_capital,
                "current_capital": self.current_capital,
                "forex_starting_capital": self.forex_starting_capital,
                "forex_capital": self.forex_capital,
                "daily_baseline_date": self.daily_baseline_date,
                "daily_baseline_equity": self.daily_baseline_equity,
                "weekly_baseline_week": self.weekly_baseline_week,
                "weekly_baseline_equity": self.weekly_baseline_equity,
                "positions": {sym: [p.to_dict() for p in pos_list] for sym, pos_list in self.positions.items()},
                "trade_history": self.trade_history
            }
            with open(self.portfolio_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando portfolio.json: {e}")

    def check_circuit_breaker(self) -> Tuple[bool, str]:
        """
        [CIRCUIT BREAKER] Evalúa pérdidas intradía o intrasemanales.
        Usa account.last_equity vs account.equity de Alpaca cuando esté disponible (SSOT).
        En simulación o modo local, usa Mark-to-Market contra el baseline registrado en disco.
        """
        from config import CIRCUIT_BREAKER_CONFIG
        if not CIRCUIT_BREAKER_CONFIG.get("enabled", True):
            return False, ""

        max_daily = CIRCUIT_BREAKER_CONFIG.get("max_daily_loss_pct", 0.02)
        max_weekly = CIRCUIT_BREAKER_CONFIG.get("max_weekly_loss_pct", 0.05)

        # 1. Si tenemos datos reales de Alpaca (SSOT)
        if getattr(self, "broker_last_equity", 0.0) > 0 and getattr(self, "broker_equity", 0.0) > 0:
            daily_loss = (self.broker_last_equity - self.broker_equity) / self.broker_last_equity
            if daily_loss >= max_daily:
                return True, f"Freno Diario Activo (Alpaca): Pérdida hoy de {daily_loss*100:.2f}% >= {max_daily*100:.1f}% (${self.broker_equity:,.2f} vs cierre ayer ${self.broker_last_equity:,.2f})"

        # 2. Mark-to-Market local / simulación
        try:
            import zoneinfo
            now_ny = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        except Exception:
            now_ny = datetime.now()
        today_str = now_ny.strftime("%Y-%m-%d")
        current_week_str = now_ny.strftime("%Y-W%U")
        current_equity = self.get_total_portfolio_value(is_forex=False)

        # Inicializar baselines si cambia de día o de semana, y persistir
        changed_baseline = False
        if self.daily_baseline_date != today_str or not self.daily_baseline_equity:
            self.daily_baseline_date = today_str
            self.daily_baseline_equity = current_equity
            changed_baseline = True

        if self.weekly_baseline_week != current_week_str or not self.weekly_baseline_equity:
            self.weekly_baseline_week = current_week_str
            self.weekly_baseline_equity = current_equity
            changed_baseline = True

        if changed_baseline:
            self.save_portfolio()

        # Pérdida Diaria
        if self.daily_baseline_equity and self.daily_baseline_equity > 0:
            daily_loss = (self.daily_baseline_equity - current_equity) / self.daily_baseline_equity
            if daily_loss >= max_daily:
                return True, f"Freno Diario Activo: Pérdida hoy de {daily_loss*100:.2f}% >= {max_daily*100:.1f}% (${current_equity:,.2f} vs base hoy ${self.daily_baseline_equity:,.2f})"

        # Pérdida Semanal
        if self.weekly_baseline_equity and self.weekly_baseline_equity > 0:
            weekly_loss = (self.weekly_baseline_equity - current_equity) / self.weekly_baseline_equity
            if weekly_loss >= max_weekly:
                return True, f"Freno Semanal Activo: Pérdida semanal de {weekly_loss*100:.2f}% >= {max_weekly*100:.1f}% (${current_equity:,.2f} vs base semana ${self.weekly_baseline_equity:,.2f})"

        return False, ""

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
        """Sincroniza el portafolio local con las posiciones reales de Alpaca de forma inmutable."""
        if not alpaca_client:
            return
            
        try:
            account = alpaca_client.get_account()
            # [BLOQUEANTE 4 CORREGIDO] Usar cash o equity real, NO buying_power que en cuentas con margen infla el capital 2x a 4x
            self.current_capital = float(account.cash if hasattr(account, 'cash') else account.equity)
            self.broker_equity = float(account.equity if hasattr(account, 'equity') else self.current_capital)
            self.broker_last_equity = float(account.last_equity if hasattr(account, 'last_equity') else self.broker_equity)
            
            # [CIRCUIT BREAKER] Fijar baseline semanal al inicio de semana usando cierre del viernes (last_equity)
            try:
                import zoneinfo
                now_ny = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
            except Exception:
                now_ny = datetime.now()
            current_week_str = now_ny.strftime("%Y-W%U")
            if self.weekly_baseline_week != current_week_str:
                self.weekly_baseline_week = current_week_str
                self.weekly_baseline_equity = float(account.last_equity if hasattr(account, 'last_equity') else self.broker_equity)
                self.save_portfolio()
            
            real_positions = alpaca_client.get_all_positions()
            real_symbols = []
            
            logger.info("🔄 Sincronizando portafolio interno con Alpaca (SSOT)...")
            
            for pos in real_positions:
                symbol = pos.symbol.replace("/", "-") if "/" in pos.symbol else pos.symbol
                real_symbols.append(symbol)
                
                qty = float(pos.qty)
                avg_price = float(pos.avg_entry_price)
                current_price = float(pos.current_price)
                asset_type = self.categorize_asset(symbol, avg_price)
                
                side_str = "BUY" if "long" in str(pos.side).lower() else "SELL"
                
                # Si no está localmente, lo agregamos conservando la fecha real si es posible
                if symbol not in self.positions:
                    entry_date = str(datetime.now())
                    try:
                        from alpaca.trading.requests import GetOrdersRequest
                        from alpaca.trading.enums import QueryOrderStatus, OrderSide
                        alp_sym = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[alp_sym], side=OrderSide.BUY, limit=1)
                        closed_buys = alpaca_client.get_orders(filter=req)
                        if closed_buys and closed_buys[0].filled_at:
                            entry_date = closed_buys[0].filled_at.isoformat()
                    except Exception:
                        pass

                    rules = MICROSTRUCTURE_CONFIG[asset_type]
                    fallback_atr = current_price * 0.05 
                    sl_dist = fallback_atr * rules["atr_sl_mult"]
                    tp_pct = rules.get("take_profit_pct", RISK_CONFIG["take_profit_pct"])
                    
                    # [FASE 0] Stop anclado a current_price para evitar paradas artificiales inmediatas
                    stop_loss = current_price - sl_dist if side_str == "BUY" else current_price + sl_dist
                    take_profit = current_price * (1 + tp_pct) if side_str == "BUY" else current_price * (1 - tp_pct)
                    
                    self.positions[symbol] = [Position(
                        symbol=symbol,
                        asset_type=asset_type,
                        entry_price=avg_price,
                        entry_date=entry_date, 
                        side=side_str,
                        quantity=qty,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        atr_at_entry=fallback_atr,
                        spread_at_entry=0.0,
                        highest_price=max(avg_price, current_price),
                        last_known_price=current_price
                    )]
                    logger.warning(f"🔄 [{symbol}] Reconciliada posición desconocida desde Alpaca: Qty={qty}, Entrada={avg_price}, Stop={stop_loss:.4f}")
                else:
                    # [BLOQUEANTE 3 CORREGIDO] Actualizar valores en vivo sin multiplicar cantidades en compras acumuladas (DCA)
                    total_local_qty = sum(p.quantity for p in self.positions[symbol])
                    if total_local_qty > 0 and len(self.positions[symbol]) > 1:
                        # Escalar proporcionalmente cada lote para que la suma total sea exactamente qty de Alpaca
                        scale = qty / total_local_qty
                        for p in self.positions[symbol]:
                            p.quantity *= scale
                            p.last_known_price = current_price
                            p.highest_price = max(p.highest_price, current_price)
                            p.lowest_price = min(p.lowest_price, current_price)
                    elif len(self.positions[symbol]) == 1:
                        p = self.positions[symbol][0]
                        p.quantity = qty
                        p.last_known_price = current_price
                        p.highest_price = max(p.highest_price, current_price)
                        p.lowest_price = min(p.lowest_price, current_price)
                
            forex_symbols = ASSETS.get("forex", [])
            symbols_to_remove = [sym for sym in self.positions.keys() if sym not in real_symbols and sym not in forex_symbols]
                    
            for sym in symbols_to_remove:
                del self.positions[sym]
                
            self.save_portfolio()
            
        except Exception as e:
            logger.error(f"Error sincronizando con Alpaca: {e}")

    def get_total_portfolio_value(self, is_forex: bool = False) -> float:
        """Calcula el valor total del portafolio en Mark-to-Market (Efectivo + Valor Actual de Mercado)."""
        forex_symbols = ASSETS.get("forex", [])
        if is_forex:
            pos_value = sum(
                p.quantity * (p.last_known_price if p.last_known_price > 0 else p.entry_price)
                for sym, pos_list in self.positions.items()
                if sym in forex_symbols
                for p in pos_list
            )
            return self.forex_capital + pos_value
        else:
            pos_value = sum(
                p.quantity * (p.last_known_price if p.last_known_price > 0 else p.entry_price)
                for sym, pos_list in self.positions.items()
                if sym not in forex_symbols
                for p in pos_list
            )
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
        
        # [FASE 2.1] Tope absoluto en dólares para activos especulativos ($500 USD)
        if asset_type in ["warrant", "penny"]:
            max_spec_dollars = RISK_CONFIG.get("max_speculative_dollars", 500.0)
            final_capital = min(final_capital, max_spec_dollars)

        # Validar capital líquido real
        available_cash = self.forex_capital if is_forex else self.current_capital
        final_capital = min(final_capital, available_cash)

        # Validar que alcance para comprar al menos 1 acción entera en activos no cripto
        if not is_forex and asset_type != "crypto":
            if (final_capital / entry_price) < 1.0:
                logger.info(f"Capital asignado (${final_capital:.2f}) no alcanza para 1 acción de ${entry_price:.2f}.")
                return 0.0
        
        return final_capital / entry_price

    def can_open_position(self, symbol: str, price: float, spread_pct: float, asset_type: str) -> bool:
        """Reglas de filtrado anti-amnesia, liquidez, sector y freno de emergencia."""
        # [CIRCUIT BREAKER] Freno de emergencia por pérdidas intradía o semanales
        tripped, cb_reason = self.check_circuit_breaker()
        if tripped:
            logger.warning(f"[{symbol}] Rechazado por Circuit Breaker: {cb_reason}")
            return False

        rules = MICROSTRUCTURE_CONFIG[asset_type]
        
        if spread_pct > rules["max_spread_pct"]:
            logger.warning(f"[{symbol}] Rechazado: Spread excesivo ({spread_pct*100:.2f}% > {rules['max_spread_pct']*100:.2f}%) para categoría {asset_type.upper()}.")
            return False

        # [FASE 2.2] Límite de Exposición Agregada Especulativa (≤ 10% del portafolio)
        if asset_type in ["warrant", "penny"]:
            total_val = self.get_total_portfolio_value(is_forex=False)
            max_spec_exposure = total_val * RISK_CONFIG.get("max_speculative_portfolio_pct", 0.10)
            current_spec_cost = sum(
                p.quantity * p.entry_price 
                for sym, p_list in self.positions.items() 
                for p in p_list 
                if self.categorize_asset(sym, p.entry_price) in ["warrant", "penny"]
            )
            pending_spec_cost = getattr(self, "pending_speculative_cost", 0.0)
            if (current_spec_cost + pending_spec_cost) >= max_spec_exposure:
                logger.warning(
                    f"[{symbol}] Rechazado: Exposición agregada especulativa del 10% alcanzada "
                    f"(${current_spec_cost + pending_spec_cost:,.2f} >= ${max_spec_exposure:,.2f})."
                )
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
            # Si la posición acumulada es solo residuo (dust) menor a $100 o menos de 1 acción normal, permitir nueva compra
            total_shares = sum(p.quantity for p in pos_list)
            total_val = sum(p.quantity * price for p in pos_list)
            if asset_type == "normal" and (total_shares < 1.0 or total_val < 100.0):
                logger.info(f"[{symbol}] Posición residual detectada ({total_shares:.4f} shares / ${total_val:.2f}). Se permite compra completa.")
                return True

            if len(pos_list) >= self.max_pyramid: return False
            today = str(datetime.now().date())
            if today == pos_list[-1].entry_date[:10]: return False
            avg_entry_price = sum(p.entry_price * p.quantity for p in pos_list) / sum(p.quantity for p in pos_list)
            if price > (avg_entry_price * 0.95): return False

        return True

    def open_position(self, symbol: str, price: float, side: str, atr: float, spread_pct: float, score: float, entry_date: str, forced_quantity: Optional[float] = None, bypass_filters: bool = False) -> Optional[Position]:
        """Abre posición usando reglas ATR y Límites Estrictos."""
        
        asset_type = self.categorize_asset(symbol, price)
        # [PUNTO 6 CORREGIDO] Si la orden ya llenó en el broker (bypass_filters=True), registrar incondicionalmente
        if not bypass_filters and not self.can_open_position(symbol, price, spread_pct, asset_type):
            return None
        
        is_forex = asset_type == "forex"
        rules = MICROSTRUCTURE_CONFIG[asset_type]
        
        # Evitar ATR 0
        safe_atr = atr if atr > 0 else price * 0.05
        
        if forced_quantity is not None and forced_quantity > 0:
            quantity = float(forced_quantity)
        else:
            quantity = self._calculate_position_size(price, safe_atr, asset_type, is_forex)
            
        if quantity <= 0: return None
        
        # Calcular stops usando ATR y take profit configurado por categoría
        stop_dist = safe_atr * rules["atr_sl_mult"]
        tp_pct = rules.get("take_profit_pct", RISK_CONFIG["take_profit_pct"])
        if side == "BUY":
            stop_loss = price - stop_dist
            take_profit = price * (1 + tp_pct)
        else: # SELL
            stop_loss = price + stop_dist
            take_profit = price * (1 - tp_pct)
            
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

    def check_exit_conditions(self, current_prices: Dict[str, Dict], update_trailing: bool = True) -> List[Dict]:
        """
        current_prices: { 'AAPL': {'price': 150.0, 'atr': 3.5, 'spread': 0.001}, ... }
        Revisa posiciones abiertas contra precios y ajusta Chandelier Trailing Stop (ATR).

        update_trailing=False (monitor intradía): solo evalúa SL/TP/timeout sin mover el trailing,
        para que el trailing se calcule con la vela diaria igual que en el backtest.
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
                
                # 1. Trailing Stop Adaptativo (ATR) - solo en el escaneo diario
                if update_trailing and current_price > pos.highest_price and pos.side == "BUY":
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
                    
                # 4. Timeout diferenciado por microestructura del activo
                else:
                    days_held = (datetime.now() - datetime.fromisoformat(pos.entry_date)).days
                    max_hold = rules.get("max_hold_days", RISK_CONFIG.get("max_hold_days", 30))
                    if days_held >= max_hold:
                        sell_reasons.append(f"MAX_TIME ({days_held}d >= {max_hold}d {pos.asset_type.upper()})")

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

    def close_position(self, symbol: str, price: float, reason: str, exit_date: str, exit_atr: float = 0.0, exit_spread: float = 0.0, exit_score: float = 0.0, quantity_to_close: Optional[float] = None, slippage_usd: float = 0.0, slippage_pct: float = 0.0) -> float:
        """Cierra posiciones de un activo (total o parcialmente), guarda historial y escribe en Audit Log."""
        if symbol not in self.positions or not self.positions[symbol]:
            return 0.0
            
        total_pnl = 0.0
        total_revenue = 0.0
        # MICROSTRUCTURE_CONFIG values are rule dicts, not category names.
        is_forex = self.positions[symbol][0].asset_type == "forex"
        
        audit_entries = []
        
        total_available = sum(p.quantity for p in self.positions[symbol])
        qty_target = quantity_to_close if (quantity_to_close is not None and quantity_to_close > 0) else total_available
        qty_remaining_to_close = min(qty_target, total_available)
        
        new_positions_list = []
        
        for pos in self.positions[symbol]:
            if qty_remaining_to_close <= 1e-6:
                new_positions_list.append(pos)
                continue
                
            qty_closing_lot = min(pos.quantity, qty_remaining_to_close)
            
            # Always ensure MFE/MAE are relative to final values
            pos.highest_price = max(pos.highest_price, price)
            pos.lowest_price = min(pos.lowest_price, price)
            
            mfe_pct = (pos.highest_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            mae_pct = (pos.lowest_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            
            if pos.side == "BUY":
                pnl = (price - pos.entry_price) * qty_closing_lot
            else:
                pnl = (pos.entry_price - price) * qty_closing_lot
                
            total_pnl += pnl
            cost_basis = pos.entry_price * qty_closing_lot
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
                "quantity": qty_closing_lot,
                "pnl": pnl,
                "reason": reason,
                "date": exit_date,
                "slippage_usd": slippage_usd,
                "slippage_pct": slippage_pct
            }
            self.trade_history.append(hist_entry)
            
            # Record para Audit Log (Analítico)
            audit_entries.append({
                "symbol": symbol,
                "asset_type": pos.asset_type,
                "entry_price": pos.entry_price,
                "exit_price": price,
                "qty": qty_closing_lot,
                "cost_basis": cost_basis,
                "profit_usd": pnl,
                "profit_pct": pnl_pct,
                "holding_time_hours": hold_hours,
                "slippage_usd": slippage_usd,
                "slippage_pct": slippage_pct,
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
            
            qty_remaining_to_close -= qty_closing_lot
            pos.quantity -= qty_closing_lot
            
            if pos.quantity > 1e-6:
                new_positions_list.append(pos)
            
        if is_forex: self.forex_capital += total_revenue
        else: self.current_capital += total_revenue
            
        if new_positions_list:
            self.positions[symbol] = new_positions_list
        else:
            del self.positions[symbol]
            
        self.save_portfolio()
        
        # Guardar en audit_log.json solo si no está en modo memoria
        if not self.in_memory:
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