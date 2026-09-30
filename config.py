"""
==============================================
CONFIGURACION CENTRALIZADA - BOT TRADING
==============================================
Este archivo contiene TODOS los parametros del bot.
Ajusta aqui sin tocar el codigo principal.
"""

import os
from datetime import timedelta

# ==========================================
# ACTIVOS A MONITOREAR
# ==========================================
ASSETS = {
    # CRIPTOMONEDAS (Binance)
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "BNB-USD"],
    
    # ACCIONES TECH (AlphaVantage)
    "stocks": ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL"],
    
    # OPCIONALES - Descomenta cuando tengas datos
    # "forex": ["EUR-USD", "GBP-USD"],
    # "commodities": ["XAU-USD", "WTI-USD"]
}

# ==========================================
# PARAMETROS DE DESCARGA DE DATOS
# ==========================================
DATA_CONFIG = {
    "days_history": 730,           # 2 años de histórico
    "cache_enabled": True,          # Guardar datos localmente
    "cache_dir": "./data_cache",    # Directorio de cache
}

# ==========================================
# INDICADORES TECNICOS
# ==========================================
INDICATORS = {
    # SMA (Media Móvil Simple)
    "sma_periods": [20, 50, 200],
    
    # RSI (Índice de Fuerza Relativa)
    "rsi_period": 14,
    "rsi_oversold": 30,     # RSI < 30 = posible compra
    "rsi_overbought": 70,   # RSI > 70 = posible venta
    
    # MACD (Moving Average Convergence Divergence)
    "macd_short": 12,
    "macd_long": 26,
    "macd_signal": 9,
    
    # Bollinger Bands
    "bb_period": 20,
    "bb_std_dev": 2,
    
    # ATR (Average True Range) - para volatilidad
    "atr_period": 14,
    
    # Volume
    "volume_period": 20,
}

# ==========================================
# LOGICA DE SEÑALES (SCORING)
# ==========================================
SIGNAL_CONFIG = {
    # Umbral minimo de puntuacion para generar alerta
    "buy_threshold": 7,      # 0-10
    "sell_threshold": 7,     # 0-10
    
    # Pesos para cada indicador (suma = 10)
    "weights": {
        "trend": 3,           # Tendencia (precio > SMA50/200)
        "momentum": 2,        # RSI + MACD
        "support_resistance": 2,  # Bollinger Bands
        "volume": 1,          # Volumen
        "multi_timeframe": 2,  # Validación en multiples timeframes
    }
}

# ==========================================
# GESTION DE RIESGOS
# ==========================================
RISK_CONFIG = {
    # Portfolio
    "starting_capital": 10000,      # Capital inicial simulado
    "risk_per_trade": 0.02,         # Riesgo por trade = 2% del capital
    "max_positions": 5,             # Maximo de posiciones simultaneas
    "max_drawdown": 0.15,           # Detener si pierde 15% del capital
    
    # Stop Loss y Take Profit
    "stop_loss_pct": 0.02,          # 3% por debajo del entry
    "take_profit_pct": 0.10,        # 8% por encima del entry (risk/reward 1:2.66)
    
    # Trailing Stop
    "trailing_stop_enabled": True,
    "trailing_stop_pct": 0.02,      # Seguir ganancias con 2%
    
    # Time-based exit
    "max_hold_days": 30,            # Cerrar si pasa 1 mes
}

# ==========================================
# INTEGRACION CON IA (GEMINI)
# ==========================================
AI_CONFIG = {
    "enabled": False,
    "model": "gemini-flash",
    "use_for_validation": False,    # Validar señales con IA
    "use_for_news": False,           # Analizar noticias
    "sentiment_impact": 0.3,        # Impacto del sentimiento (0.0-1.0)
}
# ==========================================
# NOTICIAS Y MACRO
# ==========================================
NEWS_CONFIG = {
    "enabled": False,
    "check_frequency_minutes": 60,  # Buscar noticias cada hora
    "language": "en",
    "news_lookback_days": 7,
}

# ==========================================
# BACKTESTING
# ==========================================
BACKTEST_CONFIG = {
    "enabled": True,
    "start_date": "2024-01-01",     # Fecha inicio backtest
    "end_date": "2026-12-31",       # Fecha fin backtest
    "initial_capital": 10000,
    "commission": 0.001,            # 0.1% comisión por trade
    "slippage_pct": 0.002,          # 0.2% slippage
}

# ==========================================
# LOGGING Y ALERTAS
# ==========================================
LOGGING_CONFIG = {
    "log_file": "trading_bot.log",
    "log_level": "INFO",
    "console_output": True,
    "save_daily_report": True,
}

# ==========================================
# APIS Y CREDENCIALES
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")  # Opcional

# ==========================================
# TIMEFRAMES PARA ANALISIS
# ==========================================
TIMEFRAMES = {
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",  # Principal
    "1w": "1W",
}

# ==========================================
# ESTRATEGIA (Combina todo)
# ==========================================
STRATEGY_NAME = "MultiTF_Risk_Managed_v1"
STRATEGY_VERSION = "1.0"

def print_config():
    """Imprime la configuración actual para debugging."""
    print("=" * 50)
    print("CONFIGURACION ACTIVA DEL BOT")
    print("=" * 50)
    print(f"Activos: {sum(len(v) for v in ASSETS.values())}")
    print(f"Capital inicial: ${RISK_CONFIG['starting_capital']}")
    print(f"Riesgo por trade: {RISK_CONFIG['risk_per_trade']*100}%")
    print(f"Stop Loss: {RISK_CONFIG['stop_loss_pct']*100}%")
    print(f"Take Profit: {RISK_CONFIG['take_profit_pct']*100}%")
    print(f"Señal compra threshold: {SIGNAL_CONFIG['buy_threshold']}/10")
    print(f"IA Enabled: {AI_CONFIG['enabled']}")
    print("=" * 50)
