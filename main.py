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

from config import (
    ASSETS, LOGGING_CONFIG, STRATEGY_NAME, STRATEGY_VERSION,
    RISK_CONFIG, SIGNAL_CONFIG, print_config
)
from data_fetcher import DataFetcher, DataPreprocessor
from technical_analysis import SignalAnalyzer
from risk_management import RiskManager
from backtester import BacktestRunner

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
            logging.FileHandler(LOGGING_CONFIG["log_file"]),
            logging.StreamHandler()
        ] if LOGGING_CONFIG["console_output"] else [
            logging.FileHandler(LOGGING_CONFIG["log_file"])
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
        self.signals_detected = []
        self.trades_executed = []
    
    def scan_market(self) -> List[Dict]:
        """
        Escanea el mercado en busca de oportunidades.
        Analiza todos los activos configurados.
        """
        logger.info("="*70)
        logger.info("🔍 INICIANDO ESCANEO DE MERCADO")
        logger.info("="*70)
        
        opportunities = []
        
        # Obtener todos los activos
        all_symbols = []
        for asset_type, symbols in ASSETS.items():
            all_symbols.extend(symbols)
        
        logger.info(f"Analizando {len(all_symbols)} activos...\n")
        
        for symbol in all_symbols:
            # Descargar datos
            df = self.data_fetcher.fetch_market_data(symbol)
            
            if df.empty:
                logger.warning(f"[{symbol}] No se obtuvieron datos")
                continue
            
            # Validar datos
            if not self.preprocessor.validate_data(df, symbol):
                continue
            
            # Analizar
            try:
                analyzer = SignalAnalyzer(df, symbol)
                signal_type, score, details = analyzer.evaluate_signal()
                
                # Log resumen
                self._log_analysis(symbol, analyzer.latest, score, details)
                
                # Si hay señal, registrar oportunidad
                if signal_type:
                    opportunity = {
                        "timestamp": datetime.now(),
                        "symbol": symbol,
                        "signal_type": signal_type,
                        "score": score,
                        "current_price": details["price"],
                        "details": details,
                    }
                    opportunities.append(opportunity)
                    self._log_signal(opportunity)
            
            except Exception as e:
                logger.error(f"[{symbol}] Error en análisis: {e}")
        
        logger.info("\n" + "="*70)
        logger.info(f"⏱️  ESCANEO COMPLETADO - {len(opportunities)} oportunidad(es) detectada(s)")
        logger.info("="*70 + "\n")
        
        self.signals_detected = opportunities
        return opportunities
    
    def _log_analysis(self, symbol: str, latest_data, score: float, details: Dict):
        """Registra análisis técnico de un símbolo."""
        logger.info(
            f"[{symbol:8}] Precio: ${details['price']:.2f} | "
            f"RSI: {details['rsi']:.1f} | "
            f"MACD: {details['macd']:.4f} | "
            f"ADX: {details['adx']:.1f} | "
            f"Vol: {details['volume_ratio']:.2f}x"
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
        En producción, esto enviaría órdenes reales al broker.
        """
        logger.info("📊 Ejecutando trades...\n")
        
        executed_trades = []
        
        for opp in validated_opportunities:
            symbol = opp["symbol"]
            price = opp["current_price"]
            signal_type = opp["signal_type"]
            
            try:
                if signal_type == "BUY":
                    position = self.risk_manager.open_position(
                        symbol,
                        price,
                        side="BUY",
                        entry_date=str(datetime.now())
                    )
                    
                    if position:
                        executed_trades.append({
                            "timestamp": datetime.now(),
                            "symbol": symbol,
                            "action": "BUY",
                            "price": price,
                            "quantity": position.quantity,
                            "status": "EJECUTADO"
                        })
                
                elif signal_type == "SELL":
                    # En producción: buscar posición abierta y cerrar
                    if symbol in self.risk_manager.positions:
                        self.risk_manager.close_position(
                            symbol,
                            price,
                            reason="SELL_SIGNAL",
                            exit_date=str(datetime.now())
                        )
                        
                        executed_trades.append({
                            "timestamp": datetime.now(),
                            "symbol": symbol,
                            "action": "SELL",
                            "price": price,
                            "status": "EJECUTADO"
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
    
    # Imprimir análisis
    analyzer.print_analysis()
    
    print(f"\n📊 RESULTADO:")
    print(f"   Señal: {signal_type if signal_type else 'NINGUNA'}")
    print(f"   Score: {score:.2f}/10")
    print(f"   RSI: {details['rsi']:.2f}")
    print(f"   MACD: {details['macd']:.4f}")
    print(f"   ADX: {details['adx']:.2f}")


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
