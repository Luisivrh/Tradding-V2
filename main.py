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
from datetime import datetime
from typing import Dict, List
import json
import codecs

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

import ccxt

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
        self.signals_detected = []
        self.trades_executed = []
        
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
            except Exception as e:
                logger.error(f"Error conectando a Brokers: {e}")
                self.live_trading = False
    
    def scan_market(self) -> List[Dict]:
        """
        Escanea el mercado en busca de oportunidades.
        Analiza todos los activos configurados divididos por grupos (batch).
        """
        logger.info("="*70)
        logger.info("🔍 INICIANDO ESCANEO DE MERCADO (POR LOTES)")
        logger.info("="*70)
        
        opportunities = []
        
        for asset_group, symbols in ASSETS.items():
            if not symbols:
                continue
                
            logger.info(f"\n📁 Analizando sector: {asset_group.upper()} ({len(symbols)} activos)...")
            
            group_technical_data = {}
            group_analyzers = {}
            
            # 1. Fase Técnica: Descargar y analizar matemáticamente todo el grupo
            for symbol in symbols:
                df = self.data_fetcher.fetch_market_data(symbol)
                
                if df.empty:
                    logger.warning(f"[{symbol}] No se obtuvieron datos")
                    continue
                
                if not self.preprocessor.validate_data(df, symbol):
                    continue
                
                try:
                    analyzer = SignalAnalyzer(df, symbol)
                    signal_type, tech_score, details = analyzer.evaluate_signal()
                    details["tech_score_raw"] = tech_score
                    details["signal_type_raw"] = signal_type
                    
                    group_technical_data[symbol] = details
                    group_analyzers[symbol] = analyzer
                except Exception as e:
                    logger.error(f"[{symbol}] Error en análisis técnico: {e}")
            
            if not group_technical_data:
                continue

            # 2. Fase de IA: Evaluar todo el grupo en 1 sola llamada a Gemini
            logger.info(f"🧠 Solicitando análisis de IA en lote para {asset_group.upper()}...")
            ai_batch_results = {}
            if self.ai_analyzer.enabled:
                ai_batch_results = self.ai_analyzer.analyze_batch_sentiment(asset_group, group_technical_data)
            
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
        - Tendencia global del mercado
        - Eventos macroeconómicos (opcional)
        - Correlaciones entre activos
        """
        logger.info("📋 Validando señales con análisis de contexto...\n")
        
        validated = []
        
        for opp in opportunities:
            is_valid = True
            reasons = []
            
            # Validación 1: Score mínimo
            if opp["signal_type"] == "BUY":
                if opp["score"] < SIGNAL_CONFIG["buy_threshold"]:
                    is_valid = False
                    reasons.append(f"Score insuficiente ({opp['score']:.2f} < {SIGNAL_CONFIG['buy_threshold']:.2f})")
            else:
                if opp["score"] < SIGNAL_CONFIG["sell_threshold"]:
                    is_valid = False
                    reasons.append(f"Score insuficiente ({opp['score']:.2f} < {SIGNAL_CONFIG['sell_threshold']:.2f})")
            
            # Validación 2: Riesgo del portfolio
            if is_valid and len(self.risk_manager.positions) >= RISK_CONFIG["max_positions"]:
                is_valid = False
                reasons.append(f"Portfolio lleno ({len(self.risk_manager.positions)} posiciones)")
            
            # Validación 3: Drawdown máximo
            if is_valid and self.risk_manager.get_drawdown() < -RISK_CONFIG["max_drawdown"]:
                is_valid = False
                reasons.append(f"Drawdown máximo alcanzado ({self.risk_manager.get_drawdown()*100:.2f}%)")
            
            if is_valid:
                validated.append(opp)
                logger.info(f"✅ {opp['symbol']}: VÁLIDO (Score: {opp['score']:.2f})")
            else:
                logger.info(f"❌ {opp['symbol']}: RECHAZADO ({', '.join(reasons)})")
        
        logger.info("")
        return validated
    
    def execute_trades(self, validated_opportunities: List[Dict]) -> List[Dict]:
        """
        Ejecuta trades basado en oportunidades validadas.
        Usa Alpaca para Criptos y Acciones si el Live Trading está habilitado.
        Usa simulación interna (RiskManager) para Forex o si Live Trading es False.
        """
        logger.info("📊 Ejecutando trades...\n")
        
        executed_trades = []
        
        for opp in validated_opportunities:
            symbol = opp["symbol"]
            price = opp["current_price"]
            signal_type = opp["signal_type"]
            
            try:
                # 1. Simulación Interna (Paper Trading Interno)
                # Siempre registramos la posición en el RiskManager local para llevar control
                if signal_type == "BUY":
                    position = self.risk_manager.open_position(
                        symbol,
                        price,
                        side="BUY",
                        entry_date=str(datetime.now())
                    )
                    
                    if not position:
                        continue # No se pudo abrir (ej. sin fondos)
                        
                    qty_to_buy = position.quantity
                    
                    # 2. Ejecución Real en el Broker (Alpaca)
                    # Solo enviamos la orden si está activado el live_trading Y NO es Forex
                    is_forex = symbol in ASSETS.get("forex", [])
                    
                    if self.live_trading and self.alpaca_client and not is_forex:
                        from alpaca.trading.requests import MarketOrderRequest
                        from alpaca.trading.enums import OrderSide, TimeInForce
                        
                        # Limpiar símbolo para Alpaca (Ej. BTC-USD -> BTC/USD)
                        alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                        
                        logger.info(f"Enviando orden a Alpaca: BUY {qty_to_buy:.4f} {alpaca_symbol}")
                        
                        market_order_data = MarketOrderRequest(
                            symbol=alpaca_symbol,
                            qty=qty_to_buy,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.GTC
                        )
                        
                        # Ejecutar orden
                        market_order = self.alpaca_client.submit_order(order_data=market_order_data)
                        logger.info(f"Orden Alpaca ejecutada. ID: {market_order.id}")
                    
                    # 3. Registrar éxito
                    executed_trades.append({
                        "timestamp": datetime.now(),
                        "symbol": symbol,
                        "action": "BUY",
                        "price": price,
                        "quantity": qty_to_buy,
                        "status": "REAL (Alpaca)" if (self.live_trading and not is_forex) else "SIMULADO"
                    })
                
                elif signal_type == "SELL":
                    # En producción: buscar posición abierta y cerrar
                    if symbol in self.risk_manager.positions:
                        qty_to_sell = self.risk_manager.positions[symbol].quantity
                        
                        # Cerrar en el simulador interno
                        self.risk_manager.close_position(
                            symbol,
                            price,
                            reason="SELL_SIGNAL",
                            exit_date=str(datetime.now())
                        )
                        
                        # Cerrar en Alpaca
                        is_forex = symbol in ASSETS.get("forex", [])
                        if self.live_trading and self.alpaca_client and not is_forex:
                            from alpaca.trading.requests import MarketOrderRequest
                            from alpaca.trading.enums import OrderSide, TimeInForce
                            
                            alpaca_symbol = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol
                            logger.info(f"Enviando orden a Alpaca: SELL {qty_to_sell:.4f} {alpaca_symbol}")
                            
                            market_order_data = MarketOrderRequest(
                                symbol=alpaca_symbol,
                                qty=qty_to_sell,
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.GTC
                            )
                            self.alpaca_client.submit_order(order_data=market_order_data)
                        
                        executed_trades.append({
                            "timestamp": datetime.now(),
                            "symbol": symbol,
                            "action": "SELL",
                            "price": price,
                            "quantity": qty_to_sell,
                            "status": "REAL (Alpaca)" if (self.live_trading and not is_forex) else "SIMULADO"
                        })
            
            except Exception as e:
                logger.error(f"Error ejecutando trade en {symbol}: {e}")
        
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
        self.risk_manager.print_summary()


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
        """
    )
    
    parser.add_argument(
        "--mode",
        choices=["live", "backtest", "analyze"],
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
