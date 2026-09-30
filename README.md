# 🚀 Trading Bot Profesional v2.0

Bot de trading automatizado con análisis técnico, gestión de riesgos y backtesting integrado.

## 📋 Índice

1. [Características](#características)
2. [Requisitos](#requisitos)
3. [Instalación](#instalación)
4. [Configuración](#configuración)
5. [Uso](#uso)
6. [Componentes](#componentes)
7. [Estrategia](#estrategia)
8. [Gestión de Riesgos](#gestión-de-riesgos)
9. [Brokers Soportados](#brokers-soportados)
10. [FAQ](#faq)

---

## 🎯 Características

✅ **Análisis Técnico Avanzado**
- SMA (20, 50, 200)
- RSI, MACD, Bandas de Bollinger
- ATR, ADX, OBV, Estocástico
- Sistema de scoring ponderado (0-10)

✅ **Gestión de Riesgos Automática**
- Posición sizing dinámico basado en volatilidad
- Stop-loss y take-profit automáticos
- Trailing stop
- Límite de máximo drawdown
- Máximo de posiciones simultáneas

✅ **Múltiples Fuentes de Datos**
- Criptomonedas: Binance (CCXT)
- Acciones: yfinance + AlphaVantage
- Caché local para optimizar descargas
- Validación automática de datos

✅ **Backtesting**
- Simula la estrategia en histórico
- Calcula métricas: Win Rate, Sharpe, Profit Factor, Drawdown
- Permite optimizar parámetros
- Genera gráficas de equity curve

✅ **Tres Modos de Ejecución**
- **Live**: Escanea mercado y ejecuta trades en tiempo real
- **Backtest**: Prueba estrategia en datos históricos
- **Analyze**: Análisis detallado de un símbolo

---

## 📦 Requisitos

**Python 3.9+** (recomendado 3.10 o 3.11)

```bash
# Dependencias principales
pandas
numpy
yfinance
ccxt
alpha-vantage
google-generativeai
requests
matplotlib
```

---

## 🔧 Instalación

### Paso 1: Clonar o Descargar

```bash
cd trading-bot
```

### Paso 2: Crear Entorno Virtual

```bash
python -m venv venv

# En Windows:
venv\Scripts\activate

# En Linux/Mac:
source venv/bin/activate
```

### Paso 3: Instalar Dependencias

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
pandas==2.1.0
numpy==1.24.0
yfinance==0.2.32
ccxt==2.91.61
alpha-vantage==2.3.1
google-generativeai==0.3.0
requests==2.31.0
matplotlib==3.8.0
python-dotenv==1.0.0
```

### Paso 4: Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto:

```bash
# .env
GEMINI_API_KEY=tu_clave_aqui
ALPHAVANTAGE_API_KEY=tu_clave_aqui
NEWSAPI_KEY=tu_clave_aqui
TELEGRAM_TOKEN=tu_clave_aqui  # Opcional
```

**Cómo obtener las claves:**

1. **GEMINI_API_KEY**: https://ai.google.dev/
   - Clic en "Get API Key"
   - Crea proyecto en Google Cloud
   - Copia tu clave

2. **ALPHAVANTAGE_API_KEY**: https://www.alphavantage.co/api/
   - Formulario simple
   - Te dan la clave al instante

3. **NEWSAPI_KEY**: https://newsapi.org/
   - Regístrate con email
   - Copia tu API key

4. **TELEGRAM_TOKEN** (opcional):
   - Habla con @BotFather en Telegram
   - Crea nuevo bot
   - Copia el token

---

## ⚙️ Configuración

### Archivo Principal: `config.py`

Todo se configura en un solo archivo. Ajusta según tus necesidades:

```python
# Activos a monitorear
ASSETS = {
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD"],
    "stocks": ["AAPL", "MSFT", "NVDA"],
}

# Gestión de riesgos
RISK_CONFIG = {
    "starting_capital": 10000,           # Capital inicial
    "risk_per_trade": 0.02,              # 2% por trade
    "max_positions": 5,                  # Max 5 trades simultáneos
    "stop_loss_pct": 0.03,               # 3% stop-loss
    "take_profit_pct": 0.08,             # 8% take-profit
    "trailing_stop_enabled": True,       # Seguir ganancias
    "max_drawdown": 0.15,                # Detener si pierde 15%
}

# Señales (scoring 0-10)
SIGNAL_CONFIG = {
    "buy_threshold": 6.0,                # Mínimo para comprar
    "sell_threshold": 6.0,               # Mínimo para vender
}
```

### Personalización de Indicadores

En `config.py`:

```python
INDICATORS = {
    "sma_periods": [20, 50, 200],        # SMAs
    "rsi_period": 14,                    # RSI
    "rsi_oversold": 30,                  # Compra potencial
    "rsi_overbought": 70,                # Venta potencial
    "macd_short": 12,                    # MACD corto
    "macd_long": 26,                     # MACD largo
    "bb_period": 20,                     # Bollinger
    "atr_period": 14,                    # ATR
}
```

---

## 🎮 Uso

### Modo 1: LIVE (En Vivo)

```bash
python main_improved.py --mode live
```

Esto:
1. Escanea todos los activos configurados
2. Calcula indicadores técnicos
3. Genera señales de compra/venta
4. Valida con gestión de riesgos
5. Imprime reporte de ejecución

**Salida esperada:**
```
===============================================
🚀 MODO EN VIVO - BOT TRADING
===============================================
🔍 INICIANDO ESCANEO DE MERCADO
Analizando 10 activos...

[BTC-USD ] Precio: $84278.00 | RSI: 65.21 | MACD: 0.0234 | ADX: 28.50 | Vol: 1.15x
[ETH-USD ] Precio: $2680.84 | RSI: 61.20 | MACD: 0.0089 | ADX: 22.31 | Vol: 0.98x

🔍 ESCANEO COMPLETADO - 2 oportunidad(es) detectada(s)

📊 RESUMEN DEL PORTFOLIO
===============================================
Capital Inicial:   $10,000.00
Capital Actual:    $10,156.32
P&L Total:         $156.32
...
```

### Modo 2: BACKTEST

```bash
python main_improved.py --mode backtest
```

Prueba la estrategia en datos históricos (últimos 2 años por defecto).

**Salida esperada:**
```
===============================================
📊 REPORTE DE BACKTEST: BTC-USD
===============================================
Período:              2023-01-01 → 2024-12-31
Capital Inicial:      $10,000.00
Capital Final:        $15,234.56
P&L Total:            $5,234.56
Retorno Total:        52.35%
-----------------------------------------------
Total Trades:         24
Trades Ganadores:     18
Trades Perdedores:    6
Win Rate:             75.0%
Profit Factor:        3.15x
Max Drawdown:         8.50%
Sharpe Ratio:         1.85
===============================================
```

### Modo 3: ANALYZE (Análisis Detallado)

```bash
python main_improved.py --mode analyze --symbol BTC-USD
```

Análisis profundo de un activo específico.

---

## 🔧 Componentes

### 1. `config.py` - Configuración Central
- Todos los parámetros del bot
- Fácil de ajustar sin tocar código

### 2. `data_fetcher.py` - Descarga de Datos
- Obtiene OHLCV de múltiples fuentes
- Caché local para optimizar
- Validación automática de datos

### 3. `technical_analysis.py` - Análisis Técnico
- Calcula 12+ indicadores
- Sistema de scoring ponderado
- Determina señales BUY/SELL

### 4. `risk_management.py` - Gestión de Riesgos
- Posición sizing automático
- Stop-loss y take-profit
- Trailing stop
- Protección de capital

### 5. `backtester.py` - Backtesting
- Simula trades históricos
- Calcula métricas profesionales
- Valida rentabilidad de estrategia

### 6. `main_improved.py` - Orquestador
- Integra todos los módulos
- Maneja los 3 modos
- Genera reportes

---

## 📊 Estrategia

### Cómo Genera Señales

1. **Descarga datos** de últimos 2 años (730 días)
2. **Calcula indicadores** en tiempo real
3. **Genera score** (0-10) basado en:
   - Tendencia (30%): SMA20/50/200
   - Momentum (20%): RSI + MACD
   - Soporte/Resistencia (20%): Bollinger Bands
   - Volumen (10%): Ratio volume/SMA
   - Multi-timeframe (20%): ADX + Fuerza

4. **Compara con umbrales**:
   - Score ≥ 6.0 → Señal VÁLIDA
   - Score < 6.0 → Espera

5. **Valida con gestión de riesgos**:
   - ¿Portfolio lleno? → Rechaza
   - ¿Drawdown máximo? → Detiene
   - ¿Capital suficiente? → Ejecuta

### Ejemplo de Señal BUY

```
[BTC-USD] SEÑAL DETECTADA:
   Tendencia: Alcista (Precio > SMA200)
   Momentum: RSI 35 (sobreventa), MACD positivo
   Soporte: Precio toca banda inferior de Bollinger
   Volumen: 1.2x del promedio
   ADX: 28 (tendencia fuerte)
   
   SCORE FINAL: 7.8/10 ✅ COMPRA
```

---

## 🛡️ Gestión de Riesgos

### Posición Sizing

Calcula automáticamente cuántas unidades comprar basado en:

```
Posición_Size = (Capital × Risk%) / (Entry_Price - Stop_Loss)
```

**Ejemplo:**
- Capital: $10,000
- Risk por trade: 2% = $200
- Entry: $100
- Stop Loss: $97
- Risk por unidad: $3
- Posición Size: 200 / 3 = 66.67 unidades

### Stop Loss y Take Profit

```
Configuración por defecto:
- Stop Loss: 3% por debajo del entry
- Take Profit: 8% por encima del entry
- Ratio Risk/Reward: 1:2.66

Ejemplo:
- Entry: $100
- Stop Loss: $97 (3% down)
- Take Profit: $108 (8% up)
```

### Trailing Stop

Sigue las ganancias automáticamente:

```
Si precio baja 2% desde máximo → Cierra la posición

Ejemplo:
- Entry: $100
- Máximo alcanzado: $110
- Trailing Stop: $110 × (1 - 2%) = $107.80
- Si cae a $107.80 → Vende automáticamente
```

### Límites de Portfolio

```python
max_positions: 5              # Máximo 5 trades simultáneos
risk_per_trade: 2%           # 2% del capital por trade
max_drawdown: 15%            # Detiene si pierde 15% del capital
max_hold_days: 30            # Cierra si pasa 1 mes
```

---

## 🏦 Brokers Soportados

### Fase 1: Backtesting (YA FUNCIONA)
✅ Cualquier activo con datos históricos

### Fase 2: Paper Trading (PRÓXIMA)
```
Binance (criptomonedas)
Interactive Brokers (acciones/forex)
Alpaca (acciones sin comisión)
OANDA (forex)
```

### Fase 3: Ejecución Real (FUTURA)
Integración completa con API de brokers

**Nota**: Puedes usar el bot ahora en **backtesting y análisis**. 
Para ejecución real, necesitarás integrar un broker específico.

---

## 🎓 Ejemplos de Uso

### Ejemplo 1: Testear Estrategia en Bitcoin

```bash
# Backtest
python main_improved.py --mode backtest

# Luego analiza qué pasó
python main_improved.py --mode analyze --symbol BTC-USD
```

### Ejemplo 2: Optimizar Parámetros

Edita `config.py`:

```python
# Prueba 1: Stop loss más conservador
RISK_CONFIG["stop_loss_pct"] = 0.02  # 2% en vez de 3%

# Prueba 2: Score mínimo más alto
SIGNAL_CONFIG["buy_threshold"] = 7.0  # En vez de 6.0

# Ejecuta backtest
python main_improved.py --mode backtest
```

Compara resultados y encuentra la mejor configuración.

### Ejemplo 3: Monitorear Solo Criptos

```python
# En config.py
ASSETS = {
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD"],
    # "stocks": [],  # Comentado
}

python main_improved.py --mode live
```

---

## 📈 Interpretación de Reportes

### Win Rate
```
Win Rate = (Trades Ganadores / Total Trades) × 100

Ejemplo: 18 ganadores / 24 totales = 75%
Interpretación: 3 de cada 4 trades son ganadores
```

### Profit Factor
```
Profit Factor = Total Ganancias / Total Pérdidas

Ejemplo: $5,000 ganancias / $1,600 pérdidas = 3.125x
Interpretación: Por cada $1 perdido, ganas $3.13
Mínimo aceptable: 1.5x
```

### Sharpe Ratio
```
Sharpe = Retorno Promedio / Volatilidad

Ejemplo: 1.85
Interpretación: Buen retorno ajustado por riesgo
Mínimo aceptable: 1.0
Excelente: > 2.0
```

### Max Drawdown
```
Max Drawdown = (Peak - Trough) / Peak

Ejemplo: 8.5%
Interpretación: Máxima pérdida desde pico fue 8.5%
Máximo aceptable: 15-20%
```

---

## 🚀 Próximos Pasos

### Para Comenzar
1. Instala dependencias ✅
2. Configura API keys
3. Ejecuta `python main_improved.py --mode backtest`
4. Optimiza parámetros
5. Estudia los reportes

### Para Producción
1. Implementa conexión con broker
2. Configura alertas de Telegram
3. Automatiza ejecución (cron job)
4. Implementa logging robusto
5. Configura alertas de stop emergencias

### Mejoras Futuras
- Integración con brokers reales
- Machine Learning para optimización
- Análisis de sentimiento en redes sociales
- Análisis multi-timeframe automático
- Dashboard web en tiempo real

---

## ❓ FAQ

**P: ¿Puedo usarlo con dinero real ahora?**
R: No. Por ahora solo funciona backtesting. Necesita integración con un broker.

**P: ¿Qué broker recomiendas?**
R: Para criptos: Binance. Para acciones: Interactive Brokers o Alpaca.

**P: ¿Cuál es la rentabilidad esperada?**
R: Depende del mercado y los parámetros. El backtest te lo dirá.

**P: ¿Cada cuánto ejecuta trades?**
R: Actualmente 1 vez por ejecución. En producción puede ser automático cada X minutos.

**P: ¿Cómo optimizo los parámetros?**
R: Modifica `config.py` y ejecuta backtests. Compara resultados.

**P: ¿Funciona en fin de semana?**
R: Criptos: Sí. Acciones: No (bolsa cerrada).

**P: ¿Puedo usar múltiples símbolos?**
R: Sí, edita `ASSETS` en `config.py`.

---

## 📞 Soporte

Si tienes problemas:

1. Revisa los logs en `trading_bot.log`
2. Verifica que tienes todas las dependencias: `pip list`
3. Valida tus API keys en `.env`
4. Ejecuta en modo `analyze` para un símbolo específico

---

## 📄 Licencia

Este proyecto es de código abierto. Úsalo libremente bajo tu responsabilidad.

---

**Última actualización:** Septiembre 2026
**Versión:** 2.0

¡Que ganes mucho! 🚀💰
