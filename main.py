"""
==============================================
BOT TRADING MEJORADO - MAIN
==============================================
Integra todos los módulos:
- Descarga de datos
- Análisis técnico
- Gestión de riesgos
- Backtesting
- Reporting

USO:
    python main_improved.py --mode live
    python main_improved.py --mode backtest
    python main_improved.py --mode analyze --symbol BTC-USD
"""

import logging
import argparse
import sys
import math
from datetime import datetime
from typing import Dict, List, Optional, Set
import json
import codecs
import pandas as pd

from pricing_utils import round_order_price, resolve_exit_price
from order_manager import OrderManager
from protective_stops import ProtectiveStopManager, is_stock_symbol

# Forzar salida en consola a UTF-8 para evitar errores con emojis en Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
if sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from config import (
    ASSETS, LOGGING_CONFIG, STRATEGY_NAME, STRATEGY_VERSION,
    RISK_CONFIG, SIGNAL_CONFIG, BROKERS_CONFIG, MICROSTRUCTURE_CONFIG,
    print_config
)
from data_fetcher import DataFetcher, DataPreprocessor
from technical_analysis import SignalAnalyzer
from risk_management import RiskManager
from backtester import BacktestRunner
from ai_analyzer import AIAnalyzer
from telegram_bot import TelegramNotifier
from post_mortem import PostMortemAnalyzer

import ccxt
import requests

# Configurar certifi si está disponible
try:
    import certifi
    import os
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass

# ==========================================
# CONFIGURAR LOGGING
# ==========================================

def setup_logging():
    """Configura logging a archivo y consola."""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=LOGGING_CONFIG["log_level"],
        format=log_format,
        handlers=[
            logging.FileHandler(LOGGING_CONFIG["log_file"], encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ] if LOGGING_CONFIG["console_output"] else [
            logging.FileHandler(LOGGING_CONFIG["log_file"], encoding='utf-8')
        ]
    )
    
    return logging.getLogger(__name__)

logger = setup_logging()

# ==========================================
# TRADING BOT
# ==========================================

class TradingBot:
    """Bot principal de trading."""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        # [BLOQUEANTE 2 CORREGIDO] En dry-run, RiskManager se inicia in_memory para NO leer ni sobrescribir archivos
        # En dry-run se carga portfolio.json como snapshot de solo lectura para evaluar salidas de posiciones reales
        self.risk_manager = RiskManager(RISK_CONFIG["starting_capital"], in_memory=self.dry_run, load_snapshot=self.dry_run)
        self.data_fetcher = DataFetcher()
        self.preprocessor = DataPreprocessor()
        self.ai_analyzer = AIAnalyzer()
        self.telegram = TelegramNotifier()
        self.signals_detected = []
        self.trades_executed = []
        self.evaluations_log = []
        self.post_mortem = PostMortemAnalyzer(self.data_fetcher)
        self.order_manager = OrderManager()
        self.stop_manager = None  # Stops de protección en Alpaca (solo en live real)
        
        self.daily_stats = {
            "inst_analyzed": 0, "inst_score_7": 0, "inst_buys": 0,
            "spec_analyzed": 0, "spec_score_7": 0, "spec_buys": 0,
            "rej_spread": 0, "rej_volume": 0, "rej_atr": 0, "rej_liquidity": 0,
            "top_scores": []
        }
        
        # Configurar clientes de Brokers si está habilitado el Live Trading y NO es dry-run
        self.live_trading = BROKERS_CONFIG["live_trading_enabled"] and not self.dry_run
        self.alpaca_client = None
        
        if self.dry_run:
            logger.info("🧪 MODO DRY-RUN ACTIVADO DESDE INICIALIZACIÓN: Portafolio en memoria, sin escribir archivos ni enviar órdenes.")
        elif self.live_trading:
            logger.info("INICIALIZANDO CONEXION A BROKERS (MODO EJECUCION)")
            try:
                from alpaca.trading.client import TradingClient
                
                # Alpaca (Stocks & Crypto)
                if BROKERS_CONFIG["alpaca"]["api_key"]:
                    self.alpaca_client = TradingClient(
                        api_key=BROKERS_CONFIG["alpaca"]["api_key"],
                        secret_key=BROKERS_CONFIG["alpaca"]["secret_key"],
                        paper=BROKERS_CONFIG["alpaca"]["paper"]
                    )
                    
                    # Validar conexión
                    account = self.alpaca_client.get_account()
                    logger.info(f"[OK] Alpaca Client conectado. Capital disponible: ${account.cash}")
                    
                    # 1. Reconciliar órdenes pendientes previas con Alpaca (Fase 0)
                    self.order_manager.reconcile_orders(self.alpaca_client, self.risk_manager, self.telegram)

                    # 2. Sincronizar portafolio interno con broker
                    self.risk_manager.sync_with_broker(self.alpaca_client)

                    # 3. Gestor de stops de protección en Alpaca (opción A)
                    self.stop_manager = ProtectiveStopManager(
                        self.alpaca_client, self.order_manager, self.risk_manager, self.telegram
                    )
            except Exception as e:
                logger.error(f"Error conectando a Brokers: {e}")
                self.live_trading = False
    
    def scan_market(self, groups: Optional[Set[str]] = None) -> List[Dict]:
        """
        Escanea el mercado en busca de oportunidades.
        Analiza todos los activos configurados divididos por grupos (batch).
        groups: subconjunto de grupos a escanear ({'stocks'}, {'crypto', 'forex'}...). None = todos.
        """
        logger.info("="*70)
        logger.info("🔍 INICIANDO ESCANEO DE MERCADO (POR LOTES + SCREENER)")
        logger.info("="*70)
        
        opportunities = []
        self.evaluations_log.clear() # Limpiar log de evaluaciones de Telegram
        
        # --- 1. INTEGRACIÓN DE SCREENER Y PORTFOLIO ---
        # Hacemos una copia de los activos configurados para poder inyectar dinámicamente
        dynamic_assets = {k: list(v) for k, v in ASSETS.items()}
        if groups is not None:
            dynamic_assets = {k: v for k, v in dynamic_assets.items() if k in groups}
        
        # A. Asegurar que las posiciones abiertas SIEMPRE se analicen (Regla de Oro)
        if hasattr(self.risk_manager, 'positions'):
            for pos_symbol in self.risk_manager.positions.keys():
                # Agregar a "stocks" por defecto si no está en ningún grupo (o detectar cripto)
                is_crypto = "-USD" in pos_symbol or "/" in pos_symbol
                group_key = "crypto" if is_crypto else "stocks"
                if pos_symbol in ASSETS.get("forex", []):
                    continue  # Forex ya está en su propio grupo
                if groups is not None and group_key not in groups:
                    continue
                if pos_symbol not in dynamic_assets.get(group_key, []):
                    if group_key not in dynamic_assets:
                        dynamic_assets[group_key] = []
                    dynamic_assets[group_key].append(pos_symbol)
                    logger.info(f"📌 Activo en Portfolio inyectado al análisis: {pos_symbol}")
                    
        # B. Obtener activos del Screener Híbrido (Alpaca + Yahoo)
        try:
            from screener import DynamicScreener
            screener = DynamicScreener()
            dynamic_sources = {} # Guardar el origen de cada ticker dinámico
            if screener.enabled and (groups is None or "stocks" in groups):
                new_tickers_data = screener.get_dynamic_tickers()
                for t_data in new_tickers_data:
                    ticker = t_data["symbol"]
                    source = t_data["source"]
                    dynamic_sources[ticker] = source
                    
                    # Lo metemos al grupo "stocks" si no existe ya
                    if ticker not in dynamic_assets.get("stocks", []):
                        dynamic_assets.setdefault("stocks", []).append(ticker)
        except ImportError:
            logger.warning("Módulo screener no encontrado o dependencias faltantes. Continuando con lista estática.")
        except Exception as e:
            logger.error(f"Error ejecutando Screener: {e}")
            dynamic_sources = {}
        # ----------------------------------------------

        for asset_group, symbols in dynamic_assets.items():
            if not symbols:
                continue
                
            # [HOTFIX DÍA 1] Saltear stocks si el mercado de Wall Street está cerrado (evita órdenes huérfanas y llamadas API)
            if asset_group == "stocks" and self.alpaca_client:
                try:
                    clock = self.alpaca_client.get_clock()
                    if not clock.is_open:
                        logger.info(f"🕒 Mercado de acciones CERRADO (Próxima apertura: {clock.next_open}). Omitiendo escaneo de stocks.")
                        continue
                except Exception as e_clk:
                    logger.warning(f"Aviso consultando reloj de Alpaca: {e_clk}")
                
            logger.info(f"\n📁 Analizando sector: {asset_group.upper()} ({len(symbols)} activos)...")
            
            group_technical_data = {}
            group_analyzers = {}
            current_prices_dict = {}
            
            # 1. Fase Técnica: Descargar y analizar matemáticamente todo el grupo
            # Si el grupo es 'stocks', usamos la descarga en lote (batch) de Alpaca para evitar baneos
            if asset_group == "stocks" and hasattr(self.data_fetcher, 'fetch_stocks_batch'):
                logger.info(f"Descargando lote de {len(symbols)} acciones usando Alpaca Batch API...")
                batch_data = self.data_fetcher.fetch_stocks_batch(symbols)
                for symbol in symbols:
                    df = batch_data.get(symbol, pd.DataFrame())
                    
                    if df.empty:
                        logger.warning(f"[{symbol}] No se obtuvieron datos")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Sin Datos")
                        continue
                    
                    if not self.preprocessor.validate_data(df, symbol):
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Datos Inválidos")
                        continue
                    
                    try:
                        # Determinar source y tipo real (ej. penny vs warrant)
                        source = dynamic_sources.get(symbol, "static_config")
                        price = df.iloc[-1]['Close']
                        asset_type = self.risk_manager.categorize_asset(symbol, price) # Reusando logica de risk_manager
                        
                        analyzer = SignalAnalyzer(df, symbol, asset_type=asset_type, source=source)
                        signal_type, tech_score, details = analyzer.evaluate_signal()
                        details["tech_score_raw"] = tech_score
                        details["signal_type_raw"] = signal_type
                        
                        group_technical_data[symbol] = details
                        group_analyzers[symbol] = analyzer
                        current_prices_dict[symbol] = details
                    except Exception as e:
                        logger.error(f"[{symbol}] Error en análisis técnico: {e}")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Técnico")
            elif asset_group == "forex" and hasattr(self.data_fetcher, 'fetch_forex_batch'):
                logger.info(f"Descargando lote de {len(symbols)} divisas usando Tiingo API...")
                batch_data = self.data_fetcher.fetch_forex_batch(symbols)
                for symbol in symbols:
                    df = batch_data.get(symbol, pd.DataFrame())
                    
                    if df.empty:
                        logger.warning(f"[{symbol}] No se obtuvieron datos")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Sin Datos")
                        continue
                    
                    if not self.preprocessor.validate_data(df, symbol):
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Datos Inválidos")
                        continue
                    
                    try:
                        analyzer = SignalAnalyzer(df, symbol)
                        signal_type, tech_score, details = analyzer.evaluate_signal()
                        details["tech_score_raw"] = tech_score
                        details["signal_type_raw"] = signal_type
                        
                        group_technical_data[symbol] = details
                        group_analyzers[symbol] = analyzer
                        current_prices_dict[symbol] = details
                    except Exception as e:
                        logger.error(f"[{symbol}] Error en análisis técnico: {e}")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Técnico")
            else:
                # Cripto o si no hay batch disponible, descargar uno a uno
                for symbol in symbols:
                    df = self.data_fetcher.fetch_market_data(symbol)
                    
                    if df.empty:
                        logger.warning(f"[{symbol}] No se obtuvieron datos")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Sin Datos")
                        continue
                    
                    if not self.preprocessor.validate_data(df, symbol):
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Datos Inválidos")
                        continue
                    
                    try:
                        analyzer = SignalAnalyzer(df, symbol)
                        signal_type, tech_score, details = analyzer.evaluate_signal()
                        details["tech_score_raw"] = tech_score
                        details["signal_type_raw"] = signal_type
                        
                        group_technical_data[symbol] = details
                        group_analyzers[symbol] = analyzer
                        current_prices_dict[symbol] = details
                    except Exception as e:
                        logger.error(f"[{symbol}] Error en análisis técnico: {e}")
                        self.evaluations_log.append(f"❌ {symbol:6} | ERROR: Técnico")
            
            if not group_technical_data:
                continue
                
            # 1.5 Revisión de Gestión de Riesgo (Stop Loss / Take Profit dinámico)
            forced_exits = self.risk_manager.check_exit_conditions(current_prices_dict)
            if forced_exits:
                for exit_opp in forced_exits:
                    logger.warning(f"🚨 SALIDA DE EMERGENCIA: {exit_opp['symbol']} -> {exit_opp['details']['reason']}")
                    opportunities.append(exit_opp)
                    self._log_signal(exit_opp)
                    
                    # Lo quitamos de group_technical_data para que la IA no lo analice para una "nueva compra" hoy
                    if exit_opp['symbol'] in group_technical_data:
                        del group_technical_data[exit_opp['symbol']]

            # 2. Identificación de señales técnicas puras (sin distorsión de ponderación artificial)
            from config import SIGNAL_CONFIG, SPECULATIVE_CONFIG
            
            for symbol, details in group_technical_data.items():
                tech_score = details["tech_score_raw"]
                final_score = tech_score
                
                details["ai_score"] = 5.0
                details["ai_explanation"] = "Auditado en Fase de Veto"
                
                # Guardar evaluación para Telegram
                self.evaluations_log.append(f"📊 {symbol:6} | Score:{final_score:.1f}")
                
                # Log del resultado final para este activo
                self._log_analysis(symbol, group_analyzers[symbol].latest, final_score, details)
                
                # Determinar si el score final supera los umbrales
                is_spec = details.get("asset_type") in ["warrant", "penny"] or "speculative" in details.get("source", "")
                min_buy_threshold = SPECULATIVE_CONFIG.get("buy_threshold", 8.0) if is_spec else SIGNAL_CONFIG.get("buy_threshold", 6.8)

                final_signal = None
                if final_score >= min_buy_threshold:
                    final_signal = "BUY"
                elif final_score <= (10 - SIGNAL_CONFIG.get("sell_threshold", 7.0)) and not is_spec:
                    # En modo especulativo, nunca vender por score bajo; las salidas las gestiona SL/TP/Trailing/MaxHold
                    final_signal = "SELL"
                
                # --- ACTUALIZAR ESTADÍSTICAS DIARIAS ---
                cat = "spec" if is_spec else "inst"
                
                self.daily_stats[f"{cat}_analyzed"] += 1
                if final_score >= 7.0:
                    self.daily_stats[f"{cat}_score_7"] += 1
                if final_signal == "BUY":
                    self.daily_stats[f"{cat}_buys"] += 1
                    
                if details.get("reason") == "Low absolute volume or price":
                    self.daily_stats["rej_volume"] += 1
                    
                self.daily_stats["top_scores"].append({
                    "ticker": symbol,
                    "source": details.get("source", "unknown"),
                    "type": details.get("asset_type", "normal"),
                    "tech": tech_score,
                    "ai": details.get("ai_score", 5.0),
                    "final": final_score
                })
                # ---------------------------------------

                if final_signal:
                    opportunity = {
                        "timestamp": datetime.now(),
                        "symbol": symbol,
                        "signal_type": final_signal,
                        "score": final_score,
                        "current_price": details["price"],
                        "details": details,
                    }
                    opportunities.append(opportunity)
                    self._log_signal(opportunity)
        
        logger.info("\n" + "="*70)
        logger.info(f"⏱️  ESCANEO COMPLETADO - {len(opportunities)} oportunidad(es) detectada(s)")
        logger.info("="*70 + "\n")
        
        self.signals_detected = opportunities
        return opportunities
    
    def _log_analysis(self, symbol: str, latest_data, score: float, details: Dict):
        """Registra análisis técnico de un símbolo."""
        tech_score = details.get("total", 0.0)  # El score técnico antes de la ponderación
        ai_score = details.get("ai_score", 0.0)
        
        logger.info(
            f"[{symbol:8}] Precio: ${details['price']:.2f} | "
            f"Tech Score: {tech_score:.1f} | IA Score: {ai_score:.1f} | Final: {score:.2f} "
            f"-> RSI: {details['rsi']:.1f}"
        )
    
    def _log_signal(self, opportunity: Dict):
        """Registra una señal detectada."""
        logger.warning(
            f"\n🚨 SEÑAL DETECTADA:\n"
            f"   Símbolo: {opportunity['symbol']}\n"
            f"   Tipo: {opportunity['signal_type']}\n"
            f"   Score: {opportunity['score']:.2f}/10\n"
            f"   Precio: ${opportunity['current_price']:.2f}\n"
        )
    def validate_signals_with_context(self, opportunities: List[Dict]) -> List[Dict]:
        """
        Valida señales con análisis de contexto:
        - Las ventas de emergencia NUNCA se filtran.
        - Las ventas técnicas se validan por su umbral invertido (ej. <= 3.5).
        - Las compras se validan por score, drawdown y límite de posiciones.
        """
        # [FASE 2.2] Sincronizar costo de órdenes especulativas pendientes antes de validar
        self.risk_manager.pending_speculative_cost = self.order_manager.get_pending_speculative_cost()

        logger.info("📋 Validando señales con análisis de contexto...\n")
        
        validated = []
        self.validation_log = [] # Guardar para Telegram
        
        for opp in opportunities:
            is_valid = True
            reasons = []
            
            # 1. Bypass para salidas forzosas (Stop Loss, Take Profit, Timeout)
            if opp["signal_type"] == "SELL" and opp.get("details", {}).get("reason") is not None:
                validated.append(opp)
                msg = f"✅ {opp['symbol']}: VENTA FORZOSA AUTORIZADA ({opp['details']['reason']})"
                logger.info(msg)
                self.validation_log.append(msg)
                continue
            
            # 2. Validación de umbrales para señales orgánicas
            is_spec = opp.get("details", {}).get("asset_type") in ["warrant", "penny"] or "speculative" in opp.get("details", {}).get("source", "")
            from config import SPECULATIVE_CONFIG
            min_buy = SPECULATIVE_CONFIG.get("buy_threshold", 8.0) if is_spec else SIGNAL_CONFIG.get("buy_threshold", 6.8)

            if opp["signal_type"] == "BUY":
                if opp["score"] < min_buy:
                    is_valid = False
                    reasons.append(f"Score insuficiente ({opp['score']:.2f} < {min_buy:.2f})")
            else: # SELL orgánico
                # Solo vender si ya tenemos el activo
                if opp["symbol"] not in self.risk_manager.positions:
                    is_valid = False
                    reasons.append(f"No hay posición abierta para vender")
                elif is_spec:
                    # En especulativas no permitimos venta técnica prematura
                    is_valid = False
                    reasons.append("Venta técnica desactivada para especulativas (gestión por SL/TP/Trailing/MaxHold)")
                else:
                    sell_thresh = 10.0 - SIGNAL_CONFIG.get("sell_threshold", 7.0)
                    if opp["score"] > sell_thresh:
                        is_valid = False
                        reasons.append(f"Score insuficiente para venta bajista ({opp['score']:.2f} > {sell_thresh:.2f})")
            
            # 3. Validación de Capacidad (Solo aplica para COMPRAS)
            if is_valid and opp["signal_type"] == "BUY":
                total_max_positions = sum(RISK_CONFIG.get("max_positions_per_type", {}).values())
                if len(self.risk_manager.positions) >= total_max_positions:
                    is_valid = False
                    reasons.append(f"Portfolio lleno ({len(self.risk_manager.positions)} posiciones globales)")
                
                # Validación de Drawdown
                if self.risk_manager.get_drawdown() < -RISK_CONFIG["max_drawdown"]:
                    is_valid = False
                    reasons.append(f"Drawdown máximo alcanzado ({self.risk_manager.get_drawdown()*100:.2f}%)")
            
            if is_valid:
                validated.append(opp)
                msg = f"✅ {opp['symbol']}: VÁLIDO (Score: {opp['score']:.2f})"
                logger.info(msg)
                self.validation_log.append(msg)
            else:
                msg = f"❌ {opp['symbol']}: RECHAZADO ({', '.join(reasons)})"
                logger.info(msg)
                self.validation_log.append(msg)
        
        # [FASE 3] Comité de Riesgo de IA (Poder de Veto)
        # Se auditan exclusivamente los top candidatos BUY validados técnicamente (máximo 3)
        buy_candidates = [opp for opp in validated if opp["signal_type"] == "BUY"]
        other_candidates = [opp for opp in validated if opp["signal_type"] != "BUY"]

        buy_candidates.sort(key=lambda x: x["score"], reverse=True)

        final_approved_buys = []
        max_buys_to_audit = 3

        if buy_candidates and getattr(self, "ai_analyzer", None) and self.ai_analyzer.enabled:
            logger.info(f"\n🧠 Auditando riesgo fundamental con IA para los mejores {min(len(buy_candidates), max_buys_to_audit)} candidatos BUY...")
            for opp in buy_candidates[:max_buys_to_audit]:
                sym = opp["symbol"]
                atype = opp.get("details", {}).get("asset_type", "normal")
                approved, veto_reason = self.ai_analyzer.audit_risk_veto(sym, atype, opp.get("details", {}))
                if approved:
                    logger.info(f"🛡️ [IA APROBADO] {sym}: {veto_reason}")
                    final_approved_buys.append(opp)
                else:
                    logger.warning(f"🚨 [IA VETO] {sym} RECHAZADO: {veto_reason}")
                    self.validation_log.append(f"🚨 {sym:6} | VETO IA: {veto_reason}")
        else:
            final_approved_buys = buy_candidates[:max_buys_to_audit]

        validated = final_approved_buys + other_candidates
        logger.info("")
        return validated
    
    def _get_real_time_quote(self, symbol: str) -> dict:
        """Obtiene la cotización en tiempo real (Bid/Ask) desde Alpaca para calcular el spread."""
        if not self.live_trading or not self.alpaca_client:
            return {"bid": 0, "ask": 0, "spread_pct": 0.0, "price": 0}
            
        try:
            is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])
            alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
            
            if is_crypto:
                try:
                    from alpaca.data.requests import CryptoLatestQuoteRequest
                    from alpaca.data.historical import CryptoHistoricalDataClient
                    crypto_client = CryptoHistoricalDataClient(
                        api_key=BROKERS_CONFIG["alpaca"]["api_key"],
                        secret_key=BROKERS_CONFIG["alpaca"]["secret_key"]
                    )
                    req = CryptoLatestQuoteRequest(symbol_or_symbols=alpaca_symbol)
                    quote = crypto_client.get_crypto_latest_quote(req)
                    if alpaca_symbol in quote:
                        q = quote[alpaca_symbol]
                        bid = float(q.bid_price)
                        ask = float(q.ask_price)
                        if bid > 0 and ask > bid:
                            spread = (ask - bid) / bid
                            return {"bid": bid, "ask": ask, "spread_pct": spread, "price": (bid + ask) / 2.0}
                except Exception as e_crypto:
                    logger.warning(f"Error en quote cripto para {symbol}: {e_crypto}")
            else:
                from alpaca.data.requests import StockLatestQuoteRequest
                req = StockLatestQuoteRequest(symbol_or_symbols=alpaca_symbol)
                quote = self.data_fetcher.alpaca_client.get_stock_latest_quote(req)
                if alpaca_symbol in quote:
                    q = quote[alpaca_symbol]
                    bid = float(q.bid_price)
                    ask = float(q.ask_price)
                    if bid > 0 and ask > bid:
                        spread = (ask - bid) / bid
                        return {"bid": bid, "ask": ask, "spread_pct": spread, "price": ask}
        except Exception as e:
            logger.warning(f"No se pudo obtener quote en vivo para {symbol}: {e}")
        return {"bid": 0, "ask": 0, "spread_pct": 0.0, "price": 0}

    def get_market_clock(self):
        """Reloj de Alpaca. En dry-run usa un cliente de solo lectura (no envía órdenes)."""
        try:
            client = self.alpaca_client
            if client is None and BROKERS_CONFIG["alpaca"]["api_key"]:
                from alpaca.trading.client import TradingClient
                client = TradingClient(
                    api_key=BROKERS_CONFIG["alpaca"]["api_key"],
                    secret_key=BROKERS_CONFIG["alpaca"]["secret_key"],
                    paper=BROKERS_CONFIG["alpaca"]["paper"]
                )
            return client.get_clock() if client else None
        except Exception as e:
            logger.warning(f"No se pudo consultar el reloj de Alpaca: {e}")
            return None

    def monitor_exits(self, include_stocks: bool, include_crypto: bool) -> List[Dict]:
        """
        Monitor ligero de salidas: revisa SL / TP / timeout de las posiciones abiertas con
        cotizaciones en vivo. No corre screener, indicadores ni IA.
        No mueve el trailing stop (se recalcula en el escaneo diario con la vela diaria,
        igual que en el backtest).
        """
        prices = {}
        for symbol, pos_list in self.risk_manager.positions.items():
            if not pos_list or symbol in ASSETS.get("forex", []):
                continue
            stock = is_stock_symbol(symbol)
            if (stock and not include_stocks) or (not stock and not include_crypto):
                continue
            quote = self._get_real_time_quote(symbol)
            if quote["bid"] <= 0 or quote["ask"] <= 0:
                continue
            prices[symbol] = {
                "price": (quote["bid"] + quote["ask"]) / 2.0,
                "atr": 0.0,
                "spread": quote["spread_pct"],
                "asset_type": pos_list[0].asset_type,
            }

        if not prices:
            logger.info("👁️ Monitor de salidas: sin posiciones con cotización disponible.")
            return []

        logger.info(f"👁️ Monitor de salidas: revisando {len(prices)} posición(es)...")
        exits = self.risk_manager.check_exit_conditions(prices, update_trailing=False)
        for exit_opp in exits:
            exit_opp["details"]["asset_type"] = prices[exit_opp["symbol"]]["asset_type"]
            logger.warning(f"🚨 SALIDA (MONITOR): {exit_opp['symbol']} -> {exit_opp['details']['reason']}")
        if not exits:
            return []
        validated = self.validate_signals_with_context(exits)
        return self.execute_trades(validated) if validated else []

    def execute_trades(self, validated_opportunities: List[Dict]) -> List[Dict]:
        """
        Ejecuta trades basado en oportunidades validadas usando Órdenes Limitadas.
        Prioriza por Score de mayor a menor para asignar capital a las mejores señales.
        """
        logger.info("📊 Ejecutando trades...\n")
        
        executed_trades = []
        
        # Priorizar oportunidades por mayor score
        validated_opportunities = sorted(validated_opportunities, key=lambda x: x.get("score", 0), reverse=True)
        
        for opp in validated_opportunities:
            symbol = opp["symbol"]
            signal_type = opp["signal_type"]
            score = opp["score"]
            details = opp.get("details", {})
            
            # Obtener cotización en tiempo real para evaluar Spread
            quote = self._get_real_time_quote(symbol)
            spread_pct = quote["spread_pct"] if quote["spread_pct"] > 0 else 0.001
            
            # Determinar precio de ejecución: Midpoint para no pagar el spread completo
            if quote["bid"] > 0 and quote["ask"] > 0:
                execution_price = (quote["bid"] + quote["ask"]) / 2.0
            else:
                execution_price = opp["current_price"]
                
            atr = details.get("atr", execution_price * 0.05)
            asset_type = details.get("asset_type", "normal")
            
            try:
                if signal_type == "BUY":
                    rules = MICROSTRUCTURE_CONFIG.get(asset_type, MICROSTRUCTURE_CONFIG["normal"])
                    if spread_pct > rules["max_spread_pct"]:
                        self.daily_stats["rej_spread"] += 1
                        logger.warning(f"Rechazado por spread excesivo: {symbol} ({spread_pct*100:.2f}%)")
                        continue
                        
                    if details.get("is_calibration"):
                        self.post_mortem.log_speculative_signal(symbol, execution_price, score, spread_pct)
                        logger.info(f"👻 SIGNAL GHOST TRACKED: {symbol} @ {execution_price:.4f} (Score: {score:.2f})")
                        continue

                    is_forex = symbol in ASSETS.get("forex", [])
                    is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])

                    # [FASE 0] Evitar duplicados: Si ya hay orden pendiente de compra en Alpaca, no reenviar
                    if self.order_manager.has_pending_order(symbol, "BUY"):
                        logger.info(f"⏳ Omitiendo compra de {symbol}: ya tiene una orden BUY pendiente.")
                        continue

                    if not self.risk_manager.can_open_position(symbol, execution_price, spread_pct, asset_type):
                        self.daily_stats["rej_liquidity"] += 1
                        continue

                    safe_atr = atr if atr > 0 else execution_price * 0.05
                    qty_to_buy = self.risk_manager._calculate_position_size(execution_price, safe_atr, asset_type, is_forex)
                    if qty_to_buy <= 0:
                        continue

                    qty_final = float(qty_to_buy) if is_crypto else float(math.floor(qty_to_buy))
                    if qty_final < (0.0001 if is_crypto else 1.0):
                        continue

                    limit_price_to_send = round_order_price(execution_price, is_crypto=is_crypto)

                    # [FASE 0] En Live Trading con Alpaca:
                    # NO abrimos la posición localmente aquí. Se envía la orden a Alpaca:
                    # - Si llena inmediatamente (fill), se registra de inmediato.
                    # - Si queda pendiente, se guarda en pending_orders.json para reconciliación.
                    if self.live_trading and self.alpaca_client and not is_forex:
                        try:
                            from alpaca.trading.requests import LimitOrderRequest
                            from alpaca.trading.enums import OrderSide, TimeInForce

                            alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                            client_order_id = f"TR2_BUY_{symbol.replace('/', '_').replace('-', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

                            logger.info(f"Enviando LIMIT ORDER a Alpaca: BUY {qty_final} {alpaca_symbol} @ {limit_price_to_send:.4f}")

                            limit_order_data = LimitOrderRequest(
                                symbol=alpaca_symbol,
                                qty=qty_final,
                                side=OrderSide.BUY,
                                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                                limit_price=limit_price_to_send,
                                client_order_id=client_order_id
                            )
                            market_order = self.alpaca_client.submit_order(order_data=limit_order_data)
                            status_str = str(market_order.status).lower()
                            logger.info(f"✅ Orden BUY Enviada a Alpaca. ID: {market_order.id}, Estado: {status_str}")

                            if "filled" in status_str and "partially" not in status_str:
                                filled_p = float(market_order.filled_avg_price or limit_price_to_send)
                                filled_q = float(market_order.filled_qty or qty_final)
                                position = self.risk_manager.open_position(
                                    symbol=symbol,
                                    price=filled_p,
                                    side="BUY",
                                    atr=atr,
                                    spread_pct=spread_pct,
                                    score=score,
                                    entry_date=str(market_order.filled_at or datetime.now()),
                                    forced_quantity=filled_q
                                )
                                executed_trades.append({
                                    "timestamp": datetime.now(),
                                    "symbol": symbol,
                                    "action": "BUY",
                                    "price": filled_p,
                                    "quantity": filled_q,
                                    "status": "REAL (Filled)"
                                })
                                self.telegram.send_buy_alert(
                                    symbol=symbol,
                                    asset_type=asset_type,
                                    price=filled_p,
                                    qty=filled_q,
                                    cost=filled_q * filled_p,
                                    score=score,
                                    atr=atr,
                                    spread=spread_pct,
                                    reason="SEÑAL_TÉCNICA",
                                    is_real=True
                                )
                            else:
                                self.order_manager.add_order(
                                    order_id=str(market_order.id),
                                    client_order_id=client_order_id,
                                    symbol=symbol,
                                    side="BUY",
                                    qty=qty_final,
                                    limit_price=limit_price_to_send,
                                    asset_type=asset_type,
                                    details={"atr": atr, "spread": spread_pct, "score": score, "reason": "SEÑAL_TÉCNICA"}
                                )
                                executed_trades.append({
                                    "timestamp": datetime.now(),
                                    "symbol": symbol,
                                    "action": "BUY",
                                    "price": limit_price_to_send,
                                    "quantity": qty_final,
                                    "status": "REAL (Pendiente)"
                                })
                        except Exception as e_alpaca:
                            logger.error(f"❌ Error en Alpaca para {symbol}: {e_alpaca}")
                    else:
                        position = self.risk_manager.open_position(
                            symbol=symbol,
                            price=execution_price,
                            side="BUY",
                            atr=atr,
                            spread_pct=spread_pct,
                            score=score,
                            entry_date=str(datetime.now()),
                            forced_quantity=qty_final
                        )
                        if position:
                            cost = qty_final * execution_price
                            executed_trades.append({
                                "timestamp": datetime.now(),
                                "symbol": symbol,
                                "action": "BUY",
                                "price": execution_price,
                                "quantity": qty_final,
                                "status": "SIMULADO"
                            })
                            if not self.dry_run:
                                self.telegram.send_buy_alert(
                                    symbol=symbol,
                                    asset_type=asset_type,
                                    price=execution_price,
                                    qty=qty_final,
                                    cost=cost,
                                    score=score,
                                    atr=atr,
                                    spread=spread_pct,
                                    reason="SEÑAL_TÉCNICA",
                                    is_real=False
                                )
                
                elif signal_type == "SELL":
                    if symbol in self.risk_manager.positions:
                        # [FASE 0] Evitar duplicados: Si ya hay orden pendiente de salida en Alpaca, no reenviar
                        if self.order_manager.has_pending_order(symbol, "SELL"):
                            logger.info(f"⏳ Omitiendo venta de {symbol}: ya tiene una orden SELL pendiente.")
                            continue

                        pos_list = self.risk_manager.positions[symbol]
                        total_qty_to_sell = sum(pos.quantity for pos in pos_list)
                        avg_entry = sum(pos.entry_price * pos.quantity for pos in pos_list) / total_qty_to_sell
                        
                        # Si es salida forzosa (Stop Loss / TP / Timeout), reason vendrá en details
                        reason = details.get("reason", "SEÑAL_TÉCNICA_SELL")
                        
                        # [HOTFIX DÍA 1] Cadena de respaldo de precio estricta: bid -> current_price / quote["price"] -> last_known_price
                        last_known = pos_list[-1].last_known_price if pos_list else 0.0
                        limit_price = resolve_exit_price(
                            reason=reason,
                            execution_price=execution_price,
                            quote=quote,
                            opp_current_price=opp.get("current_price", 0.0),
                            last_known_price=last_known
                        )
                        
                        if limit_price <= 0:
                            logger.warning(f"⚠️ [{symbol}] Precio de salida no disponible (bid/current/last_known <= 0). Omitiendo venta en este ciclo para evitar PnL erróneo.")
                            continue
                        
                        is_forex = symbol in ASSETS.get("forex", [])
                        is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])
                        
                        # Fix para Alpaca: No enviar decimales en acciones
                        qty_final_sell = float(total_qty_to_sell) if is_crypto else float(math.floor(total_qty_to_sell))
                        
                        # [FASE 0] Desacoplamiento de venta:
                        # En Live Trading, solo llamamos close_position() si la orden se llena inmediatamente (fill).
                        # Si queda pendiente o se envía con close_position asíncrono, se guarda en order_manager.
                        if self.live_trading and self.alpaca_client and not is_forex:
                            try:
                                from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
                                from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
                                
                                alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                                client_order_id = f"TR2_SELL_{symbol.replace('/', '_').replace('-', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

                                # [OPCIÓN A] Liberar las acciones apartadas por el stop de protección en Alpaca.
                                # Si el stop ya se ejecutó, NO vender de nuevo: la reconciliación registra esa venta.
                                if self.stop_manager:
                                    stop_state = self.stop_manager.cancel_for_symbol(symbol)
                                    if stop_state == "filled":
                                        logger.warning(f"🛡️ [{symbol}] El stop de Alpaca ya vendió la posición. Se omite la venta del bot.")
                                        continue
                                    if stop_state == "unknown":
                                        logger.warning(f"🛡️ [{symbol}] No se confirmó la cancelación del stop en Alpaca. Se reintenta en la siguiente corrida.")
                                        continue

                                # Evaluar órdenes abiertas previas
                                skip_new_order = False
                                try:
                                    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[alpaca_symbol])
                                    open_orders = self.alpaca_client.get_orders(filter=req)
                                    for order in open_orders:
                                        if str(order.side).lower() == "sell" or order.side == OrderSide.SELL:
                                            if "STOP" in reason:
                                                logger.info(f"Emergencia: Cancelando orden previa {order.id} para escapar a mercado.")
                                                self.alpaca_client.cancel_order_by_id(order.id)
                                            else:
                                                if order.limit_price is not None:
                                                    old_limit = float(order.limit_price)
                                                    diff_pct = abs(old_limit - limit_price) / old_limit if old_limit > 0 else 1.0
                                                    if diff_pct <= 0.02:
                                                        logger.info(f"Orden existente ({order.id}) a ${old_limit:.4f} es óptima. Manteniendo.")
                                                        skip_new_order = True
                                                    else:
                                                        self.alpaca_client.cancel_order_by_id(order.id)
                                                else:
                                                    self.alpaca_client.cancel_order_by_id(order.id)
                                except Exception as e_cancel:
                                    logger.warning(f"Error evaluando órdenes previas para {alpaca_symbol}: {e_cancel}")
                                
                                if not skip_new_order:
                                    if total_qty_to_sell < 1.0 or "STOP" in reason or "TAKE_PROFIT" in reason or "MAX_TIME" in reason:
                                        logger.info(f"Liquidando posición completa en Alpaca (Zero-Dust) para {alpaca_symbol}...")
                                        try:
                                            market_order = self.alpaca_client.close_position(alpaca_symbol)
                                            status_str = str(getattr(market_order, 'status', 'accepted')).lower()
                                            logger.info(f"✅ Orden de cierre enviada a Alpaca: {alpaca_symbol} (ID: {getattr(market_order, 'id', 'N/A')}, Estado: {status_str})")
                                            
                                            if "filled" in status_str and "partially" not in status_str:
                                                filled_p = float(getattr(market_order, 'filled_avg_price', None) or limit_price)
                                                pnl = self.risk_manager.close_position(
                                                    symbol=symbol,
                                                    price=filled_p,
                                                    reason=reason,
                                                    exit_date=str(getattr(market_order, 'filled_at', None) or datetime.now()),
                                                    exit_atr=atr,
                                                    exit_spread=spread_pct,
                                                    exit_score=score
                                                )
                                                executed_trades.append({
                                                    "timestamp": datetime.now(),
                                                    "symbol": symbol,
                                                    "action": "SELL",
                                                    "price": filled_p,
                                                    "quantity": total_qty_to_sell,
                                                    "status": "REAL (Filled)"
                                                })
                                                cost_basis = avg_entry * total_qty_to_sell
                                                pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
                                                dt_in = datetime.fromisoformat(pos_list[0].entry_date)
                                                hold_time = (datetime.now() - dt_in).total_seconds() / 3600.0
                                                if not self.dry_run:
                                                    self.telegram.send_sell_alert(
                                                        symbol=symbol,
                                                        entry_price=avg_entry,
                                                        exit_price=filled_p,
                                                        qty=total_qty_to_sell,
                                                        pnl=pnl,
                                                        pnl_pct=pnl_pct,
                                                        hold_time=hold_time,
                                                        reason=reason,
                                                        atr=atr,
                                                        spread=spread_pct,
                                                        is_real=True
                                                    )
                                            else:
                                                order_id_str = str(getattr(market_order, 'id', ''))
                                                if order_id_str:
                                                    self.order_manager.add_order(
                                                        order_id=order_id_str,
                                                        client_order_id=client_order_id,
                                                        symbol=symbol,
                                                        side="SELL",
                                                        qty=total_qty_to_sell,
                                                        limit_price=limit_price,
                                                        asset_type=asset_type,
                                                        details={"reason": reason, "atr": atr, "spread": spread_pct, "score": score}
                                                    )
                                        except Exception as e_close:
                                            logger.warning(f"Aviso al liquidar posición directa en Alpaca: {e_close}")
                                    else:
                                        limit_price_to_send = round_order_price(limit_price, is_crypto=is_crypto)
                                        logger.info(f"Enviando LIMIT ORDER a Alpaca: SELL {qty_final_sell} {alpaca_symbol} @ {limit_price_to_send:.4f}")
                                        order_data = LimitOrderRequest(
                                            symbol=alpaca_symbol,
                                            qty=qty_final_sell,
                                            side=OrderSide.SELL,
                                            time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                                            limit_price=limit_price_to_send,
                                            client_order_id=client_order_id
                                        )
                                        market_order = self.alpaca_client.submit_order(order_data=order_data)
                                        status_str = str(market_order.status).lower()
                                        logger.info(f"✅ Orden SELL Enviada a Alpaca. ID: {market_order.id}, Estado: {status_str}")
                                        
                                        if "filled" in status_str and "partially" not in status_str:
                                            filled_p = float(market_order.filled_avg_price or limit_price_to_send)
                                            pnl = self.risk_manager.close_position(
                                                symbol=symbol,
                                                price=filled_p,
                                                reason=reason,
                                                exit_date=str(market_order.filled_at or datetime.now()),
                                                exit_atr=atr,
                                                exit_spread=spread_pct,
                                                exit_score=score
                                            )
                                            executed_trades.append({
                                                "timestamp": datetime.now(),
                                                "symbol": symbol,
                                                "action": "SELL",
                                                "price": filled_p,
                                                "quantity": qty_final_sell,
                                                "status": "REAL (Filled)"
                                            })
                                            cost_basis = avg_entry * total_qty_to_sell
                                            pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
                                            dt_in = datetime.fromisoformat(pos_list[0].entry_date)
                                            hold_time = (datetime.now() - dt_in).total_seconds() / 3600.0
                                            if not self.dry_run:
                                                self.telegram.send_sell_alert(
                                                    symbol=symbol,
                                                    entry_price=avg_entry,
                                                    exit_price=filled_p,
                                                    qty=qty_final_sell,
                                                    pnl=pnl,
                                                    pnl_pct=pnl_pct,
                                                    hold_time=hold_time,
                                                    reason=reason,
                                                    atr=atr,
                                                    spread=spread_pct,
                                                    is_real=True
                                                )
                                        else:
                                            self.order_manager.add_order(
                                                order_id=str(market_order.id),
                                                client_order_id=client_order_id,
                                                symbol=symbol,
                                                side="SELL",
                                                qty=qty_final_sell,
                                                limit_price=limit_price_to_send,
                                                asset_type=asset_type,
                                                details={"reason": reason, "atr": atr, "spread": spread_pct, "score": score}
                                            )
                            except Exception as e_alpaca:
                                logger.error(f"❌ Error en Alpaca (Venta) para {symbol}: {e_alpaca}")
                        else:
                            # Modo Simulado o Forex
                            pnl = self.risk_manager.close_position(
                                symbol=symbol,
                                price=limit_price,
                                reason=reason,
                                exit_date=str(datetime.now()),
                                exit_atr=atr,
                                exit_spread=spread_pct,
                                exit_score=score
                            )
                            executed_trades.append({
                                "timestamp": datetime.now(),
                                "symbol": symbol,
                                "action": "SELL",
                                "price": limit_price,
                                "quantity": total_qty_to_sell,
                                "status": "SIMULADO"
                            })
                            cost_basis = avg_entry * total_qty_to_sell
                            pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
                            dt_in = datetime.fromisoformat(pos_list[0].entry_date)
                            hold_time = (datetime.now() - dt_in).total_seconds() / 3600.0
                            if not self.dry_run:
                                self.telegram.send_sell_alert(
                                    symbol=symbol,
                                    entry_price=avg_entry,
                                    exit_price=limit_price,
                                    qty=total_qty_to_sell,
                                    pnl=pnl,
                                    pnl_pct=pnl_pct,
                                    hold_time=hold_time,
                                    reason=reason,
                                    atr=atr,
                                    spread=spread_pct,
                                    is_real=False
                                )
            
            except Exception as e:
                logger.error(f"Error interno ejecutando trade en {symbol}: {e}")
        
        self.trades_executed = executed_trades
        return executed_trades
    
    def print_report(self):
        """Imprime reporte completo y detallado."""
        print("\n" + "="*70)
        print(f"📈 REPORTE DE EJECUCION - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        print(f"\n✨ Estrategia: {STRATEGY_NAME} v{STRATEGY_VERSION}")
        
        # Resumen Plain Text Específico
        print("\nUNIVERSO INSTITUCIONAL")
        print(f"- Analizados: {self.daily_stats['inst_analyzed']}")
        print(f"- Score > 7: {self.daily_stats['inst_score_7']}")
        print(f"- Señales BUY: {self.daily_stats['inst_buys']}")
        
        print("\nUNIVERSO ESPECULATIVO")
        print(f"- Analizados: {self.daily_stats['spec_analyzed']}")
        print(f"- Score > 7: {self.daily_stats['spec_score_7']}")
        print(f"- Señales BUY: {self.daily_stats['spec_buys']}")
        
        print("\nRECHAZADOS POR RIESGO")
        print(f"- Spread: {self.daily_stats['rej_spread']}")
        print(f"- Volumen: {self.daily_stats['rej_volume']}")
        print(f"- ATR: {self.daily_stats['rej_atr']}")
        print(f"- Liquidez/Portfolio: {self.daily_stats['rej_liquidity']}")
        
        print("\nTOP 10 SCORES DEL DÍA")
        print(f"{'Ticker':<8} | {'Source':<15} | {'Type':<8} | {'Tech':<6} | {'AI':<4} | {'Final':<6}")
        print("-" * 65)
        top10 = sorted(self.daily_stats['top_scores'], key=lambda x: x['final'], reverse=True)[:10]
        for s in top10:
            print(f"{s['ticker']:<8} | {s['source']:<15} | {s['type']:<8} | {s['tech']:<6.1f} | {s['ai']:<4.1f} | {s['final']:<6.2f}")
            
        try:
            with open(self.post_mortem.speculative_tracker_file, 'r', encoding='utf-8') as f:
                ghost_data = json.load(f)
                if ghost_data:
                    print("\nSEGUIMIENTO DE SEÑALES ESPECULATIVAS (GHOST TRADES)")
                    print(f"{'Ticker':<8} | {'Date':<10} | {'Price':<8} | {'Score':<6} | {'Spread':<8} | {'+1D':<8} | {'+3D':<8} | {'+5D':<8}")
                    print("-" * 85)
                    for g in ghost_data[-20:]: # Muestra últimos 20
                        p1 = f"${g['plus_1d']:.2f}" if g['plus_1d'] is not None else "Pending"
                        p3 = f"${g['plus_3d']:.2f}" if g['plus_3d'] is not None else "Pending"
                        p5 = f"${g['plus_5d']:.2f}" if g['plus_5d'] is not None else "Pending"
                        date_str = g['date'][:10]
                        print(f"{g['ticker']:<8} | {date_str:<10} | ${g['price_signal']:<7.2f} | {g['score_final']:<6.1f} | {g['spread']*100:>5.1f}%   | {p1:<8} | {p3:<8} | {p5:<8}")
        except Exception:
            pass

        if self.signals_detected:
            print(f"\n🔍 Total Señales Detectadas: {len(self.signals_detected)}")
        
        if self.trades_executed:
            print(f"\n✅ Trades Ejecutados en Broker: {len(self.trades_executed)}")
            for trade in self.trades_executed:
                print(f"   • {trade['symbol']:8} - {trade['action']:4} @ ${trade['price']:.2f}")
        else:
            print("\n✅ Trades Ejecutados en Broker: 0")
        
        # Resumen portfolio
        portfolio_str = self.risk_manager.print_summary()
        self.portfolio_summary_str = portfolio_str # Guardar para que telegram lo pueda acceder

# ==========================================
# MODOS DE EJECUCION
# ==========================================

def run_full_cycle(bot: "TradingBot", groups: Optional[Set[str]], dry_run: bool, weekly_report: bool = False) -> List[Dict]:
    """Escaneo completo (señales + validación + ejecución), reporte, post-mortem y resumen."""
    opportunities = bot.scan_market(groups=groups)
    validated = bot.validate_signals_with_context(opportunities)

    executed = []
    if validated:
        executed = bot.execute_trades(validated)

    bot.print_report()

    # En dry-run, NUNCA tocar audit_log.json ni enviar resúmenes a Telegram
    if dry_run:
        logger.info("🧪 [DRY-RUN] Simulación en memoria finalizada: audit_log.json, portfolio.json y Telegram intactos.")
        return executed

    logger.info("🕒 Actualizando evaluación Post-Mortem de operaciones pasadas...")
    bot.post_mortem.update_logs()

    from config import AI_CONFIG
    total_assets = sum(len(v) for v in ASSETS.values())
    config_details = {
        "ia_enabled": AI_CONFIG["enabled"],
        "live_trading": BROKERS_CONFIG["live_trading_enabled"],
        "buy_threshold": SIGNAL_CONFIG["buy_threshold"],
        "risk_pct": RISK_CONFIG["risk_per_trade"] * 100,
        "tp_pct": RISK_CONFIG["take_profit_pct"] * 100,
        "sl_pct": RISK_CONFIG["stop_loss_pct"] * 100,
        "total_assets": total_assets
    }
    bot.telegram.send_summary(
        detected=len(bot.signals_detected),
        executed=len(executed),
        capital=bot.risk_manager.current_capital,
        config_details=config_details,
        evaluations=bot.evaluations_log,
        validations=bot.validation_log,
        portfolio_summary=bot.risk_manager.print_summary()
    )

    if weekly_report:
        logger.info("📅 Enviando reporte estadístico semanal Post-Mortem...")
        bot.telegram.send_post_mortem_report(bot.post_mortem.generate_weekly_report())
    return executed


def _ensure_protective_stops(bot: "TradingBot", dry_run: bool):
    """Coloca/actualiza los stops de protección en Alpaca al final de cada corrida."""
    if dry_run or not bot.stop_manager:
        return
    try:
        bot.stop_manager.ensure_all()
    except Exception as e:
        logger.error(f"Error asegurando stops de protección: {e}")


def mode_live(dry_run: bool = False):
    """Modo en vivo manual: escanea TODOS los grupos y ejecuta (sin planificador)."""
    print("\n" + "="*70)
    print("🚀 MODO EN VIVO - BOT TRADING" + (" [🧪 DRY-RUN ACTIVO]" if dry_run else ""))
    print("="*70)
    print_config()

    bot = TradingBot(dry_run=dry_run)
    now = datetime.now()
    weekly = (not dry_run) and now.weekday() == 4 and now.hour >= 18
    run_full_cycle(bot, groups=None, dry_run=dry_run, weekly_report=weekly)
    _ensure_protective_stops(bot, dry_run)


def mode_auto(dry_run: bool = False):
    """
    Modo planificado: el cron lo ejecuta cada 15 minutos y el código decide qué hacer
    (ver scheduler.py): escaneo diario de acciones cerca del cierre, escaneo diario de
    cripto/forex, monitor de salidas y actualización de stops en Alpaca.
    """
    from datetime import timezone
    from scheduler import decide_tasks, load_state, save_state

    bot = TradingBot(dry_run=dry_run)
    clock = bot.get_market_clock()
    market_open = bool(clock and clock.is_open)
    next_close = getattr(clock, "next_close", None) if clock else None
    if next_close is not None and next_close.tzinfo is None:
        next_close = next_close.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    state = load_state()
    tasks = decide_tasks(now_utc, market_open, next_close, state)
    logger.info(
        f"🗓️ MODO AUTO{' [DRY-RUN]' if dry_run else ''} | Mercado acciones: {'ABIERTO' if market_open else 'CERRADO'} | "
        f"Escaneo acciones: {tasks['stocks_scan']} | Escaneo cripto/forex: {tasks['daily_24h_scan']} | "
        f"Monitor acciones: {tasks['monitor_stocks']} | Monitor cripto: {tasks['monitor_crypto']}"
    )

    groups: Set[str] = set()
    if tasks["stocks_scan"]:
        groups.add("stocks")
    if tasks["daily_24h_scan"]:
        groups.add("crypto")
        if tasks["include_forex"]:
            groups.add("forex")

    if groups:
        # Reporte semanal: en el escaneo de cripto del sábado 00:05 UTC (viernes por la tarde en NY)
        weekly = tasks["daily_24h_scan"] and now_utc.weekday() == 5
        run_full_cycle(bot, groups=groups, dry_run=dry_run, weekly_report=weekly and not dry_run)
        if not dry_run:
            if tasks["stocks_scan"]:
                state["stocks_scan_date"] = tasks["today_ny"]
            if tasks["daily_24h_scan"]:
                state["daily_24h_scan_date"] = tasks["today_utc"]
            save_state(state)

    if tasks["monitor_stocks"] or tasks["monitor_crypto"]:
        bot.monitor_exits(include_stocks=tasks["monitor_stocks"], include_crypto=tasks["monitor_crypto"])

    _ensure_protective_stops(bot, dry_run)


def mode_report():
    """Fuerza la generación y envío del reporte Post-Mortem estadístico."""
    print("\n" + "="*70)
    print("📋 MODO REPORTE - ESTADISTICAS POST-MORTEM")
    print("="*70)
    
    bot = TradingBot()
    logger.info("🕒 Actualizando métricas con precios actuales...")
    bot.post_mortem.update_logs()
    
    logger.info("Generando reporte...")
    report_text = bot.post_mortem.generate_weekly_report()
    print("\n" + report_text)
    
    logger.info("Enviando a Telegram...")
    bot.telegram.send_post_mortem_report(report_text)
    logger.info("✅ Enviado.")


def mode_backtest():
    """Modo backtest - prueba estrategia en histórico."""
    print("\n" + "="*70)
    print("⏳ MODO BACKTEST")
    print("="*70)
    print_config()
    
    runner = BacktestRunner()
    
    # Obtener símbolos
    all_symbols = []
    for asset_type, symbols in ASSETS.items():
        all_symbols.extend(symbols)
    
    # Ejecutar backtests
    results = runner.run_all_backtests(all_symbols)
    
    # Resumen
    runner.print_summary()
    
    # Mejores performers
    best_symbol, best_results = runner.get_best_performer()
    if best_results:
        print(f"🏆 Mejor desempeño: {best_symbol}")
        print(f"   Retorno: {best_results['total_return']:.2f}%")
        print(f"   Win Rate: {best_results['win_rate']:.1f}%")


def mode_analyze(symbol: str):
    """Modo análisis - análisis detallado de un símbolo."""
    print("\n" + "="*70)
    print(f"🔬 MODO ANALISIS - {symbol}")
    print("="*70)
    
    fetcher = DataFetcher()
    preprocessor = DataPreprocessor()
    ai_analyzer = AIAnalyzer()
    
    # Descargar
    df = fetcher.fetch_market_data(symbol)
    
    if df.empty:
        logger.error(f"No se pudieron obtener datos para {symbol}")
        return
    
    # Validar
    if not preprocessor.validate_data(df, symbol):
        logger.error(f"Datos inválidos para {symbol}")
        return
    
    # Analizar
    analyzer = SignalAnalyzer(df, symbol)
    signal_type, score, details = analyzer.evaluate_signal()
    
    # Análisis IA
    ai_score = 5.0
    ai_explanation = "IA deshabilitada."
    if ai_analyzer.enabled:
        print("\n🧠 Solicitando análisis de sentimiento a Gemini (puede demorar unos segundos)...")
        ai_score, ai_explanation = ai_analyzer.analyze_sentiment(symbol, details)
        
        from config import AI_CONFIG
        sentiment_impact = AI_CONFIG.get("sentiment_impact", 0.3)
        score = (score * (1 - sentiment_impact)) + (ai_score * sentiment_impact)
    
    # Imprimir análisis
    analyzer.print_analysis()
    
    print(f"\n📊 RESULTADO TÉCNICO:")
    print(f"   RSI: {details['rsi']:.2f}")
    print(f"   MACD: {details['macd']:.4f}")
    print(f"   ADX: {details['adx']:.2f}")

    print(f"\n🧠 ANÁLISIS DE IA (GEMINI):")
    print(f"   Score de IA: {ai_score:.2f}/10")
    print(f"   Razón: {ai_explanation}")
    
    print(f"\n🎯 VEREDICTO FINAL:")
    print(f"   Señal: {signal_type if signal_type else 'NINGUNA'}")
    print(f"   Score Ponderado: {score:.2f}/10")


# ==========================================
# MAIN
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Bot de Trading con IA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --mode auto            (cron cada 15 min: el bot decide qué hacer)
  python main.py --mode auto --dry-run  (simula la decisión sin enviar órdenes ni escribir archivos)
  python main_improved.py --mode live
  python main_improved.py --mode backtest
  python main_improved.py --mode analyze --symbol BTC-USD
  python main_improved.py --mode report
        """
    )
    
    parser.add_argument(
        "--mode",
        choices=["live", "auto", "backtest", "analyze", "report"],
        default="live",
        help="Modo de ejecución"
    )
    
    parser.add_argument(
        "--symbol",
        type=str,
        help="Símbolo para modo análisis (ej: BTC-USD, AAPL)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta el ciclo de escaneo y validación sin enviar órdenes reales a brokers"
    )
    
    args = parser.parse_args()
    
    try:
        if args.mode == "live":
            mode_live(dry_run=args.dry_run)
        elif args.mode == "auto":
            mode_auto(dry_run=args.dry_run)
        elif args.mode == "report":
            mode_report()
        elif args.mode == "backtest":
            mode_backtest()
        elif args.mode == "analyze":
            if not args.symbol:
                logger.error("--symbol requerido para modo analyze")
                sys.exit(1)
            mode_analyze(args.symbol)
    
    except KeyboardInterrupt:
        logger.info("\n⏹️  Bot detenido por el usuario")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
