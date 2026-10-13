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
from typing import Dict, List
import json
import codecs
import pandas as pd

# Forzar salida en consola a UTF-8 para evitar errores con emojis en Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
if sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from config import (
    ASSETS, LOGGING_CONFIG, STRATEGY_NAME, STRATEGY_VERSION,
    RISK_CONFIG, SIGNAL_CONFIG, BROKERS_CONFIG, print_config
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
import urllib3

# Evitar errores de certificado SSL en todo el sistema (Windows)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
original_request = requests.Session.request
def patched_request(self, method, url, **kwargs):
    kwargs['verify'] = False
    return original_request(self, method, url, **kwargs)
requests.Session.request = patched_request

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
    
    def __init__(self):
        self.risk_manager = RiskManager(RISK_CONFIG["starting_capital"])
        self.data_fetcher = DataFetcher()
        self.preprocessor = DataPreprocessor()
        self.ai_analyzer = AIAnalyzer()
        self.telegram = TelegramNotifier()
        self.signals_detected = []
        self.trades_executed = []
        self.evaluations_log = []
        self.post_mortem = PostMortemAnalyzer(self.data_fetcher)
        
        # Configurar clientes de Brokers si está habilitado el Live Trading
        self.live_trading = BROKERS_CONFIG["live_trading_enabled"]
        self.alpaca_client = None
        
        if self.live_trading:
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
                    
                    # Sincronizar portafolio interno con broker
                    self.risk_manager.sync_with_broker(self.alpaca_client)
            except Exception as e:
                logger.error(f"Error conectando a Brokers: {e}")
                self.live_trading = False
    
    def scan_market(self) -> List[Dict]:
        """
        Escanea el mercado en busca de oportunidades.
        Analiza todos los activos configurados divididos por grupos (batch).
        """
        logger.info("="*70)
        logger.info("🔍 INICIANDO ESCANEO DE MERCADO (POR LOTES + SCREENER)")
        logger.info("="*70)
        
        opportunities = []
        self.evaluations_log.clear() # Limpiar log de evaluaciones de Telegram
        
        # --- 1. INTEGRACIÓN DE SCREENER Y PORTFOLIO ---
        # Hacemos una copia de los activos configurados para poder inyectar dinámicamente
        dynamic_assets = {k: list(v) for k, v in ASSETS.items()}
        
        # A. Asegurar que las posiciones abiertas SIEMPRE se analicen (Regla de Oro)
        if hasattr(self.risk_manager, 'positions'):
            for pos_symbol in self.risk_manager.positions.keys():
                # Agregar a "stocks" por defecto si no está en ningún grupo (o detectar cripto)
                is_crypto = "-USD" in pos_symbol or "/" in pos_symbol
                group_key = "crypto" if is_crypto else "stocks"
                if pos_symbol not in dynamic_assets.get(group_key, []):
                    if group_key not in dynamic_assets:
                        dynamic_assets[group_key] = []
                    dynamic_assets[group_key].append(pos_symbol)
                    logger.info(f"📌 Activo en Portfolio inyectado al análisis: {pos_symbol}")
                    
        # B. Obtener activos del Screener Híbrido (Alpaca + Yahoo)
        try:
            from screener import DynamicScreener
            screener = DynamicScreener()
            if screener.enabled:
                new_tickers = screener.get_dynamic_tickers()
                for ticker in new_tickers:
                    # Lo metemos al grupo "stocks" si no existe ya
                    if ticker not in dynamic_assets.get("stocks", []):
                        dynamic_assets.setdefault("stocks", []).append(ticker)
        except ImportError:
            logger.warning("Módulo screener no encontrado o dependencias faltantes. Continuando con lista estática.")
        except Exception as e:
            logger.error(f"Error ejecutando Screener: {e}")
        # ----------------------------------------------

        for asset_group, symbols in dynamic_assets.items():
            if not symbols:
                continue
                
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

            # 2. Fase de IA: Evaluar todo el grupo en 1 sola llamada a Gemini
            logger.info(f"🧠 Solicitando análisis de IA en lote para {asset_group.upper()}...")
            ai_batch_results = {}
            if self.ai_analyzer.enabled:
                ai_batch_results = self.ai_analyzer.analyze_batch_sentiment(asset_group, group_technical_data)
                
                # Checar si Gemini falló (El ai_analyzer devuelve 5.0 y un string de Error)
                first_result = next(iter(ai_batch_results.values()), None)
                if first_result and "Error IA" in first_result[1]:
                    self.telegram.send_error("Gemini AI API", first_result[1])
            
            # 3. Fase de Ponderación: Mezclar resultados e identificar señales
            from config import AI_CONFIG, SIGNAL_CONFIG
            sentiment_impact = AI_CONFIG.get("sentiment_impact", 0.3)
            
            for symbol, details in group_technical_data.items():
                tech_score = details["tech_score_raw"]
                
                ai_score = 5.0
                ai_explanation = "IA deshabilitada."
                
                if symbol in ai_batch_results:
                    ai_score, ai_explanation = ai_batch_results[symbol]
                
                # Ponderación
                final_score = (tech_score * (1 - sentiment_impact)) + (ai_score * sentiment_impact)
                
                details["ai_score"] = ai_score
                details["ai_explanation"] = ai_explanation
                
                # Guardar evaluación para Telegram
                self.evaluations_log.append(f"📊 {symbol:6} | Fin:{final_score:.1f} | T:{tech_score:.1f} | IA:{ai_score:.1f}")
                
                # Log del resultado final para este activo
                self._log_analysis(symbol, group_analyzers[symbol].latest, final_score, details)
                
                # Determinar si el score final supera los umbrales
                final_signal = None
                if final_score >= SIGNAL_CONFIG.get("buy_threshold", 6.5):
                    final_signal = "BUY"
                elif final_score <= (10 - SIGNAL_CONFIG.get("sell_threshold", 6.5)):
                    final_signal = "SELL"
                
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
            if opp["signal_type"] == "BUY":
                if opp["score"] < SIGNAL_CONFIG["buy_threshold"]:
                    is_valid = False
                    reasons.append(f"Score insuficiente ({opp['score']:.2f} < {SIGNAL_CONFIG['buy_threshold']:.2f})")
            else: # SELL orgánico
                # Solo vender si ya tenemos el activo
                if opp["symbol"] not in self.risk_manager.positions:
                    is_valid = False
                    reasons.append(f"No hay posición abierta para vender")
                else:
                    sell_thresh = 10.0 - SIGNAL_CONFIG.get("sell_threshold", 6.5)
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
        
        logger.info("")
        return validated
    
    def _get_real_time_quote(self, symbol: str) -> dict:
        """Obtiene la cotización en tiempo real (Bid/Ask) desde Alpaca para calcular el spread."""
        if not self.live_trading or not self.alpaca_client:
            return {"bid": 0, "ask": 0, "spread_pct": 0.0, "price": 0}
            
        try:
            # Reutilizamos el data_client inicializado en data_fetcher
            from alpaca.data.requests import StockLatestQuoteRequest
            alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
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

    def execute_trades(self, validated_opportunities: List[Dict]) -> List[Dict]:
        """
        Ejecuta trades basado en oportunidades validadas usando Órdenes Limitadas.
        """
        logger.info("📊 Ejecutando trades...\n")
        
        executed_trades = []
        
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
            
            try:
                if signal_type == "BUY":
                    position = self.risk_manager.open_position(
                        symbol=symbol,
                        price=execution_price,
                        side="BUY",
                        atr=atr,
                        spread_pct=spread_pct,
                        score=score,
                        entry_date=str(datetime.now())
                    )
                    
                    if not position:
                        continue 
                        
                    qty_to_buy = position.quantity
                    cost = qty_to_buy * execution_price
                    is_forex = symbol in ASSETS.get("forex", [])
                    is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])
                    
                    if self.live_trading and self.alpaca_client and not is_forex:
                        try:
                            from alpaca.trading.requests import LimitOrderRequest
                            from alpaca.trading.enums import OrderSide, TimeInForce
                            
                            alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                            qty_final = float(qty_to_buy) if is_crypto else float(math.floor(qty_to_buy))
                            
                            if qty_final >= (0.0001 if is_crypto else 1.0):
                                logger.info(f"Enviando LIMIT ORDER a Alpaca: BUY {qty_final} {alpaca_symbol} @ {execution_price:.4f}")
                                
                                limit_order_data = LimitOrderRequest(
                                    symbol=alpaca_symbol,
                                    qty=qty_final,
                                    side=OrderSide.BUY,
                                    time_in_force=TimeInForce.DAY,
                                    limit_price=round(execution_price, 4)
                                )
                                market_order = self.alpaca_client.submit_order(order_data=limit_order_data)
                                logger.info(f"✅ Orden Limitada Confirmada. ID: {market_order.id}")
                        except Exception as e_alpaca:
                            logger.error(f"❌ Error en Alpaca para {symbol}: {e_alpaca}")
                    
                    executed_trades.append({
                        "timestamp": datetime.now(),
                        "symbol": symbol,
                        "action": "BUY",
                        "price": execution_price,
                        "quantity": qty_to_buy,
                        "status": "REAL (Limit)" if (self.live_trading and not is_forex) else "SIMULADO"
                    })
                    
                    self.telegram.send_buy_alert(
                        symbol=symbol, 
                        asset_type=position.asset_type,
                        price=execution_price, 
                        qty=qty_to_buy,
                        cost=cost,
                        score=score,
                        atr=atr,
                        spread=spread_pct,
                        reason="SEÑAL_TÉCNICA",
                        is_real=(self.live_trading and not is_forex)
                    )
                
                elif signal_type == "SELL":
                    if symbol in self.risk_manager.positions:
                        pos_list = self.risk_manager.positions[symbol]
                        total_qty_to_sell = sum(pos.quantity for pos in pos_list)
                        avg_entry = sum(pos.entry_price * pos.quantity for pos in pos_list) / total_qty_to_sell
                        
                        # Si es salida forzosa (Stop Loss / TP / Timeout), reason vendrá en details
                        reason = details.get("reason", "SEÑAL_TÉCNICA_SELL")
                        
                        # Vender a Limit (Midpoint) o Mercado dependiendo de la urgencia
                        limit_price = quote["bid"] if reason.startswith("STOP") else execution_price
                        
                        pnl = self.risk_manager.close_position(
                            symbol=symbol,
                            price=limit_price,
                            reason=reason,
                            exit_date=str(datetime.now()),
                            exit_atr=atr,
                            exit_spread=spread_pct,
                            exit_score=score
                        )
                        
                        is_forex = symbol in ASSETS.get("forex", [])
                        is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])
                        
                        # Fix para Alpaca: No enviar decimales en acciones
                        qty_final_sell = float(total_qty_to_sell) if is_crypto else float(math.floor(total_qty_to_sell))
                        
                        if self.live_trading and self.alpaca_client and not is_forex:
                            try:
                                from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
                                from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
                                
                                alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                                
                                # 1. Buscar y evaluar órdenes abiertas previas para este símbolo
                                skip_new_order = False
                                try:
                                    logger.info(f"Buscando órdenes pendientes para {alpaca_symbol}...")
                                    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[alpaca_symbol])
                                    open_orders = self.alpaca_client.get_orders(filter=req)
                                    for order in open_orders:
                                        # Solo analizamos si la orden abierta es de Venta
                                        if str(order.side).lower() == "sell" or order.side == OrderSide.SELL:
                                            # Si es emergencia, destruir y escapar
                                            if "STOP" in reason:
                                                logger.info(f"Emergencia: Cancelando orden previa {order.id} para escapar a mercado.")
                                                self.alpaca_client.cancel_order_by_id(order.id)
                                            else:
                                                # Venta técnica, evaluar si vale la pena perder el lugar en la fila
                                                if order.limit_price is not None:
                                                    old_limit = float(order.limit_price)
                                                    diff_pct = abs(old_limit - limit_price) / old_limit if old_limit > 0 else 1.0
                                                    
                                                    if diff_pct <= 0.02: # 2% de tolerancia
                                                        logger.info(f"Orden existente ({order.id}) a ${old_limit:.4f} es óptima (Dif: {diff_pct*100:.1f}%). Manteniendo prioridad en Order Book.")
                                                        skip_new_order = True
                                                    else:
                                                        logger.info(f"Orden a ${old_limit:.4f} obsoleta (Nuevo: ${limit_price:.4f}). Cancelando...")
                                                        self.alpaca_client.cancel_order_by_id(order.id)
                                                else:
                                                    logger.info(f"Orden de mercado/stop previa detectada. Cancelando para limpiar...")
                                                    self.alpaca_client.cancel_order_by_id(order.id)
                                except Exception as e_cancel:
                                    logger.warning(f"Error evaluando órdenes previas para {alpaca_symbol}: {e_cancel}")
                                
                                # 2. Enviar la nueva orden de venta (Solo si no decidimos conservar la vieja)
                                if not skip_new_order:
                                    # Si es un Stop Loss de emergencia en un Warrant, a veces es mejor Limit al Bid.
                                    # Por seguridad para asegurar salida de emergencia usamos Market si es STOP, sino Limit.
                                    if "STOP" in reason:
                                        logger.info(f"Enviando MARKET ORDER a Alpaca (Emergencia): SELL {qty_final_sell} {alpaca_symbol}")
                                        order_data = MarketOrderRequest(
                                            symbol=alpaca_symbol,
                                            qty=qty_final_sell,
                                            side=OrderSide.SELL,
                                            time_in_force=TimeInForce.DAY
                                        )
                                    else:
                                        logger.info(f"Enviando LIMIT ORDER a Alpaca: SELL {qty_final_sell} {alpaca_symbol} @ {limit_price:.4f}")
                                        order_data = LimitOrderRequest(
                                            symbol=alpaca_symbol,
                                            qty=qty_final_sell,
                                            side=OrderSide.SELL,
                                            time_in_force=TimeInForce.DAY,
                                            limit_price=round(limit_price, 4)
                                        )
                                    
                                    market_order = self.alpaca_client.submit_order(order_data=order_data)
                                    logger.info(f"✅ Orden de Venta Confirmada. ID: {market_order.id}")
                            except Exception as e_alpaca:
                                logger.error(f"❌ Error en Alpaca (Venta) para {symbol}: {e_alpaca}")
                        
                        executed_trades.append({
                            "timestamp": datetime.now(),
                            "symbol": symbol,
                            "action": "SELL",
                            "price": limit_price,
                            "quantity": total_qty_to_sell,
                            "status": "REAL" if (self.live_trading and not is_forex) else "SIMULADO"
                        })
                        
                        cost_basis = avg_entry * total_qty_to_sell
                        pnl_pct = pnl / cost_basis if cost_basis > 0 else 0
                        dt_in = datetime.fromisoformat(pos_list[0].entry_date)
                        hold_time = (datetime.now() - dt_in).total_seconds() / 3600.0
                        
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
                            is_real=(self.live_trading and not is_forex)
                        )
            
            except Exception as e:
                logger.error(f"Error interno ejecutando trade en {symbol}: {e}")
        
        self.trades_executed = executed_trades
        return executed_trades
    
    def print_report(self):
        """Imprime reporte completo."""
        print("\n" + "="*70)
        print(f"📈 REPORTE DE EJECUCION - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        print(f"\n✨ Estrategia: {STRATEGY_NAME} v{STRATEGY_VERSION}")
        
        if self.signals_detected:
            print(f"\n🔍 Señales Detectadas: {len(self.signals_detected)}")
            for sig in self.signals_detected:
                print(f"   • {sig['symbol']:8} - {sig['signal_type']:4} (Score: {sig['score']:.2f}/10)")
        else:
            print("\n🔍 Señales Detectadas: 0")
        
        if self.trades_executed:
            print(f"\n✅ Trades Ejecutados: {len(self.trades_executed)}")
            for trade in self.trades_executed:
                print(f"   • {trade['symbol']:8} - {trade['action']:4} @ ${trade['price']:.2f}")
        else:
            print("\n✅ Trades Ejecutados: 0")
        
        # Resumen portfolio
        portfolio_str = self.risk_manager.print_summary()
        self.portfolio_summary_str = portfolio_str # Guardar para que telegram lo pueda acceder

# ==========================================
# MODOS DE EJECUCION
# ==========================================

def mode_live():
    """Modo en vivo - escanea y ejecuta."""
    print("\n" + "="*70)
    print("🚀 MODO EN VIVO - BOT TRADING")
    print("="*70)
    print_config()

    bot = TradingBot()

    # Escanear mercado
    opportunities = bot.scan_market()

    # Validar
    validated = bot.validate_signals_with_context(opportunities)

    # Ejecutar
    if validated:
        executed = bot.execute_trades(validated)

    # Reporte
    bot.print_report()
    
    # Evaluar operaciones cerradas en el pasado
    logger.info("🕒 Actualizando evaluación Post-Mortem de operaciones pasadas...")
    bot.post_mortem.update_logs()

    # Enviar resumen completo por Telegram
    from config import AI_CONFIG, RISK_CONFIG, SIGNAL_CONFIG, BROKERS_CONFIG, ASSETS
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
        executed=len(bot.trades_executed),
        capital=bot.risk_manager.current_capital,
        config_details=config_details,
        evaluations=bot.evaluations_log,
        validations=getattr(bot, 'validation_log', []),
        portfolio_summary=getattr(bot, 'portfolio_summary_str', "")
    )
    
    # Enviar reporte semanal Post-Mortem de forma automática los viernes después de las 6PM
    now = datetime.now()
    if now.weekday() == 4 and now.hour >= 18:
        logger.info("📅 Viernes por la tarde: Enviando reporte estadístico semanal Post-Mortem...")
        report_text = bot.post_mortem.generate_weekly_report()
        bot.telegram.send_post_mortem_report(report_text)


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
  python main_improved.py --mode live
  python main_improved.py --mode backtest
  python main_improved.py --mode analyze --symbol BTC-USD
  python main_improved.py --mode report
        """
    )
    
    parser.add_argument(
        "--mode",
        choices=["live", "backtest", "analyze", "report"],
        default="live",
        help="Modo de ejecución"
    )
    
    parser.add_argument(
        "--symbol",
        type=str,
        help="Símbolo para modo análisis (ej: BTC-USD, AAPL)"
    )
    
    args = parser.parse_args()
    
    try:
        if args.mode == "live":
            mode_live()
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
