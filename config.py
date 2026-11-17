"""
==============================================
CONFIGURACION CENTRALIZADA - BOT TRADING
==============================================
Este archivo contiene TODOS los parametros del bot.
Ajusta aqui sin tocar el codigo principal.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# ACTIVOS A MONITOREAR
# ==========================================
ASSETS = {
    # CRIPTOMONEDAS (Binance)
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "BNB-USD", "XRP-USD", "DOT-USD"],
    
    # ACCIONES TECH Y ETFs (AlphaVantage/Tiingo)
    "stocks": ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "SPY", "QQQ", "NU"],
    
    # DIVISAS (Forex vía Tiingo - tickers sin guion)
    "forex": ["EURUSD=X", "GBPUSD=X", "JPY=X", "AUDUSD=X", "USDMXN=X", "EURMXN=X", "GBPMXN=X", "CADMXN=X" ],
}

# ==========================================
# SCREENER DINÁMICO
# ==========================================
SCREENER_CONFIG = {
    "enabled": True,
    "max_dynamic_stocks": 80, # Ampliado para soportar múltiples universos (Screener V2)
    # Asignación de cuotas por universo (Screener V2)
    "allocation": {
        "speculative": 15,    # Universo A: Movers, Gainers, Active
        "institutional": 40,  # Universo B: Momentum Real / Relative Strength
        "highs": 15,          # Universo C: 52 Week Highs
        "watchlist": 20       # Universo D: Lista fija institucional / Mid-Caps investigadas
    },
    "watchlist": [
        "CRGY", "FANG", "EOG", "OVV", "APA", "MTDR", 
        "NVDA", "META", "AMZN", "MSFT", "GOOGL", "AR",
        "BRZE", "GMAB", "ADT", "KPLT", "BMBL", "GDYN"
    ]
}

# Parámetros para el motor especulativo (Screener V2)
SPECULATIVE_CONFIG = {
    "min_volume_ratio": 2.5,         # Volumen relativo mínimo vs SMA
    "min_absolute_volume": 500000,   # Volumen absoluto mínimo (500k acciones) para evitar manipulación
    "min_price": 0.10,               # Evitar basura extrema
    "ignore_sma200": True,           # Permitir operar breakouts sin tendencia larga madura
    "calibration_mode": False,       # False para ejecutar compras reales en el broker
    "buy_threshold": 8.0,            # Exigir score alto (>= 8.0) para asegurar impulso explosivo
    "max_hold_days": 2,              # Salida rápida: máximo 2 días si no despega
}

# ==========================================
# PARAMETROS DE DESCARGA DE DATOS
# ==========================================
DATA_CONFIG = {
    "days_history": 730,           # 2 años de histórico
    "cache_enabled": False,         # Activado temporalmente para evadir limites de API
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
    "buy_threshold": 6.8,    # 0-10 (calibrado para breakouts y tendencias institucionales)
    "sell_threshold": 7.0,   # 0-10
    
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
# MICROESTRUCTURA Y CATEGORIZACION DE ACTIVOS
# ==========================================
MICROSTRUCTURE_CONFIG = {
    # Definición de riesgo, spread máximo y multiplicadores ATR por categoría
    "warrant": {
        "max_pos_pct": 0.02,        # Máximo 2% del capital en 1 warrant (~$970)
        "max_spread_pct": 0.15,     # Rechazar si el spread Bid/Ask > 15%
        "atr_sl_mult": 3.5,         # Stop Loss inicial a 3.5x ATR
        "atr_ts_mult": 3.0,         # Trailing Stop a 3.0x ATR
        "take_profit_pct": 0.20,    # Take Profit fijo al +20%
        "max_hold_days": 2          # Salida rápida: máx 2 días si no despega
    },
    "penny": {
        "max_pos_pct": 0.03,        # Máximo 3% del capital en 1 penny stock (~$1,450)
        "max_spread_pct": 0.08,     # Rechazar si el spread > 8%
        "atr_sl_mult": 2.5,
        "atr_ts_mult": 2.0,
        "take_profit_pct": 0.20,    # Take Profit fijo al +20%
        "max_hold_days": 3          # Salida rápida: máx 3 días para evitar dump
    },
    "normal": {
        "max_pos_pct": 0.08,        # Máximo 8% del capital (~$3,880) para dar liquidez a acciones líderes
        "max_spread_pct": 0.02,     # Rechazar si el spread > 2%
        "atr_sl_mult": 2.0,
        "atr_ts_mult": 1.5,
        "take_profit_pct": 0.20,    # Take profit objetivo +20%
        "max_hold_days": 30         # Dejar correr la tendencia institucional
    },
    "crypto": {
        "max_pos_pct": 0.05,
        "max_spread_pct": 0.03,
        "atr_sl_mult": 2.5,
        "atr_ts_mult": 2.0,
        "take_profit_pct": 0.20,
        "max_hold_days": 14
    },
    "forex": {
        "max_pos_pct": 0.05,
        "max_spread_pct": 0.01,
        "atr_sl_mult": 1.5,
        "atr_ts_mult": 1.0,
        "take_profit_pct": 0.05,
        "max_hold_days": 14
    }
}

# ==========================================
# GESTION DE RIESGOS
# ==========================================
RISK_CONFIG = {
    # Portfolio
    "starting_capital": 100000,
    "forex_starting_capital": 10000,
    "risk_per_trade": 0.01,         # Riesgamos 1% del capital base por trade (Risk Parity)
    "max_positions_per_type": {
        "stocks": 20,
        "crypto": 5,
        "forex": 5
    },
    "max_drawdown": 0.15,           # Detener si pierde 15% del capital

    # Los Stops fijos ya no se usan directamente, se usa ATR.
    # Se mantienen por si falla el calculo del ATR (Fallback)
    "stop_loss_pct": 0.10,          
    "take_profit_pct": 0.20,

    # Trailing Stop adaptativo basado en ATR activo por defecto
    "trailing_stop_enabled": True,
    
    # [FASE 2] Blindaje Especulativo (Sandbox Control)
    "max_speculative_dollars": 500.0,       # Máximo $500 USD por posición en warrants y pennys
    "max_speculative_portfolio_pct": 0.10,  # Máximo 10% del portafolio invertido en warrants y pennys
    
    # Time-based exit
    "max_hold_days": 30,            # Cerrar si pasa 1 mes
}

# ==========================================
# REGIMEN DE MERCADO (FILTRO MACRO)
# ==========================================
MARKET_REGIME_CONFIG = {
    "enabled": True,
    "benchmark": "SPY",                  # Índice rector de Wall Street
    "sma_period": 200,                   # Media móvil simple de 200 días
    "allow_stocks_if_bearish": False,    # Si SPY < SMA200, bloquear compras de acciones
}

# ==========================================
# FRENO DE EMERGENCIA POR PERDIDAS (CIRCUIT BREAKER)
# ==========================================
CIRCUIT_BREAKER_CONFIG = {
    "enabled": True,
    "max_daily_loss_pct": 0.02,          # Congelar compras si la cuenta pierde >= 2.0% en el día
    "max_weekly_loss_pct": 0.05,         # Congelar compras si la cuenta pierde >= 5.0% en la semana
}

# ==========================================
# HORARIOS (MODO AUTO) Y STOPS EN EL BROKER
# ==========================================
# El cron corre `python main.py --mode auto` cada 15 minutos y el código decide qué hacer:
# - Escaneo completo de acciones 1 vez al día cerca del cierre (vela diaria casi completa = igual que el backtest).
# - Escaneo de cripto/forex 1 vez al día después del cierre de la vela diaria UTC.
# - En las demás corridas: solo monitor de salidas (SL/TP/timeout) y actualización de stops en Alpaca.
SCHEDULE_CONFIG = {
    "timezone": "America/New_York",
    "stocks_scan_time": "15:40",          # Hora NY del escaneo diario de acciones
    "min_minutes_before_close": 5,        # No escanear si faltan menos minutos para el cierre
    "daily_24h_scan_time_utc": "00:05",   # Escaneo diario de cripto y forex (hora UTC)
    "state_file": "scheduler_state.json",
    # Stops de protección en Alpaca (opción A)
    "protective_stops_enabled": True,
    "stop_replace_min_pct": 0.002,        # Reemplazar el stop solo si sube al menos 0.2%
}

# ==========================================
# INTEGRACION CON IA (GEMINI)
# ==========================================
AI_CONFIG = {
    "enabled": True,
    "model": "gemini-3.5-flash-lite",
    "use_for_validation": True,    # Validar señales con IA
    "use_for_news": True,           # Analizar noticias
    "sentiment_impact": 0.15,       # Impacto del sentimiento (15%) para no hundir señales técnicas con 5.0 neutral
}

# ==========================================
# MONITOR EXTERNO (DEAD MAN'S SWITCH)
# ==========================================
HEALTHCHECK_CONFIG = {
    "enabled": True,
    "url": os.environ.get("HEALTHCHECK_URL", ""),  # URL ping de healthchecks.io o similar
}
# ==========================================
# NOTICIAS Y MACRO
# ==========================================
NEWS_CONFIG = {
    "enabled": True,
    "check_frequency_minutes": 60,  # Buscar noticias cada hora
    "language": "en",
    "news_lookback_days": 7,
}

# ==========================================
# BACKTESTING
# ==========================================
BACKTEST_CONFIG = {
    "enabled": False,
    "start_date": "2024-01-01",     # Fecha inicio backtest
    "end_date": "2026-12-31",       # Fecha fin backtest
    "initial_capital": 20000,
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
    "save_daily_report": False,
}

# ==========================================
# APIS Y CREDENCIALES
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ==========================================
# BROKERS (PAPER TRADING / REAL)
# ==========================================
BROKERS_CONFIG = {
    "live_trading_enabled": True,  # CAMBIA A TRUE PARA ENVIAR ORDENES REALES/PAPER

    # ALPACA (Para Acciones y Criptos - Paper Trading recomendado)
    "alpaca": {
        "api_key": os.environ.get("ALPACA_API_KEY", ""),
        "secret_key": os.environ.get("ALPACA_SECRET_KEY", ""),
        "paper": True, # True para usar https://paper-api.alpaca.markets
    }
}

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
