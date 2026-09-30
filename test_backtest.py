import sys
import os

# ← ESTO DEBE IR PRIMERO
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ← AHORA sí importa
import logging
from data_fetcher import DataFetcher, DataPreprocessor
from backtester import BacktestRunner

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Activos a testear
SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]

if __name__ == "__main__":
    logger.info("🔄 Iniciando backtest simple...")
    
    # Descargar datos
    fetcher = DataFetcher()
    results = {}
    
    for symbol in SYMBOLS:
        logger.info(f"\n📥 Descargando {symbol}...")
        df = fetcher.fetch_market_data(symbol)
        
        if not df.empty:
            logger.info(f"✅ {symbol}: {len(df)} velas")
            results[symbol] = df
        else:
            logger.error(f"❌ {symbol}: Sin datos")
    
    # Ejecutar backtest
    if results:
        logger.info("\n⏳ Ejecutando backtests...")
        runner = BacktestRunner()
        
        for symbol, df in results.items():
            logger.info(f"\n🔄 Backtestando {symbol}...")
            result = runner.run_backtest(symbol, df)
            
            if result:
                logger.info(f"✅ {symbol} completado")
        
        # Mostrar resumen
        logger.info("\n" + "="*70)
        runner.print_summary()
    else:
        logger.error("No hay datos para testear")