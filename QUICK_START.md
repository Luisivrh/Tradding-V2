# ⚡ INICIO RÁPIDO - 5 MINUTOS

Comienza a usar el bot de trading en 5 minutos.

## Paso 1: Instalar (2 min)

```bash
# Clonar/descargar archivos
cd trading-bot

# Crear entorno virtual
python -m venv venv

# Activar (Windows)
venv\Scripts\activate

# Activar (Linux/Mac)
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

## Paso 2: Configurar APIs (2 min)

1. Copia `.env.example` a `.env`
   ```bash
   cp .env.example .env
   ```

2. Abre `.env` y pega tus claves:
   - GEMINI_API_KEY: https://ai.google.dev/
   - ALPHAVANTAGE_API_KEY: https://www.alphavantage.co/api/

   *Sin estas claves, el bot igual funciona en backtesting*

## Paso 3: Ejecutar (1 min)

### Opción A: Backtest Rápido
```bash
python main_improved.py --mode backtest
```

Esto ejecuta la estrategia en datos históricos de Bitcoin y Ethereum.

**Esperado:**
```
⏳ MODO BACKTEST
Descargando datos...
📊 REPORTE DE BACKTEST: BTC-USD
Capital Inicial:      $10,000.00
Capital Final:        $15,234.56
Retorno Total:        52.35%
Win Rate:             75.0%
```

### Opción B: Análisis de 1 Símbolo
```bash
python main_improved.py --mode analyze --symbol BTC-USD
```

Muestra análisis técnico detallado.

### Opción C: Escanear Mercado
```bash
python main_improved.py --mode live
```

Escanea todos los activos y muestra oportunidades.

---

## 📊 Interpretar Resultados

### Backtest: Qué significa cada métrica

| Métrica | Qué es | Bueno |
|---------|--------|-------|
| **Win Rate** | % de trades ganadores | > 50% |
| **Profit Factor** | Ganancias / Pérdidas | > 1.5x |
| **Sharpe Ratio** | Retorno ajustado por riesgo | > 1.0 |
| **Max Drawdown** | Máxima pérdida desde pico | < 20% |

### Ejemplo de Backtest Bueno:
```
✅ Win Rate: 75%        (3 de 4 trades ganan)
✅ Profit Factor: 3.1x  (Por cada $1 perdido, ganas $3.10)
✅ Sharpe: 1.85         (Buen retorno ajustado)
✅ Drawdown: 8.5%       (Baja volatilidad)
```

---

## ⚙️ Personalizaciones Rápidas

### Cambiar Activos a Monitorear

Edita `config.py`:

```python
# Línea ~40
ASSETS = {
    "crypto": ["BTC-USD", "ETH-USD"],      # Edita aquí
    "stocks": ["AAPL", "MSFT"],            # O aquí
}
```

### Cambiar Capital del Backtest

```python
# Línea ~80
BACKTEST_CONFIG = {
    "initial_capital": 50000,  # Cambia aquí (ej: $50k)
}
```

### Cambiar Riesgo por Trade

```python
# Línea ~75
RISK_CONFIG = {
    "risk_per_trade": 0.05,    # 5% en vez de 2%
}
```

### Cambiar Período Histórico

```python
# Línea ~78
BACKTEST_CONFIG = {
    "start_date": "2022-01-01",  # Cambiar año
    "end_date": "2024-12-31",    # Cambiar año
}
```

---

## 🛠️ Solución de Problemas

### Error: "ModuleNotFoundError"
```bash
# Verificar que pip instaló todo
pip list

# Si falta algo:
pip install -r requirements.txt
```

### Error: "API Key not found"
```bash
# Verificar archivo .env existe
dir .env    # Windows
ls .env     # Linux/Mac

# Si no existe:
cp .env.example .env
# Luego pega tus claves en .env
```

### Sin datos para acciones (nan)
```
Problema: AlphaVantage tiene límite de 5 requests/minuto
Solución: Usa yfinance (automático, más rápido)
Ya está implementado por defecto
```

---

## 🎯 Próximos Pasos

1. **Ejecuta un backtest** y estudia los resultados
2. **Optimiza parámetros** en `config.py`
3. **Compara resultados** entre ejecuciones
4. **Prueba con diferentes síbolos**
5. **Lee el README completo** para opciones avanzadas

---

## 📚 Aprender Más

- **Indicadores técnicos**: Ver `technical_analysis.py`
- **Gestión de riesgos**: Ver `risk_management.py`
- **Backtesting avanzado**: Ver `backtester.py`
- **Todas las opciones**: Leer `README.md`

---

## 💡 Tips Pro

1. **Siempre backtest primero** antes de cambios
2. **Win rate no lo es todo** - Profit Factor importa más
3. **Prueba con diferentes períodos** (1 año, 2 años, 5 años)
4. **Documenta tus cambios** - Sé qué funcionó
5. **Especialmente**: Valida en múltiples activos

---

## 🚀 Cuando Funcione Bien

Si tu backtest muestra:
- ✅ Win Rate > 60%
- ✅ Profit Factor > 2.0x
- ✅ Sharpe > 1.5
- ✅ Drawdown < 15%

¡Tu estrategia es SÓLIDA! 

Próximo paso sería:
1. Paper trading (simular con dinero real pero sin gastar)
2. Integración con broker real
3. Ejecución en vivo

---

**¿Listo?** 
```bash
python main_improved.py --mode backtest
```

¡Que ganes mucho! 💰
