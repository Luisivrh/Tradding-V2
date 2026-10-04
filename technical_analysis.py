"""
==============================================
ANALISIS TECNICO - BOT TRADING
==============================================
Calcula indicadores avanzados y genera señales
basadas en puntuación ponderada.
"""

import pandas as pd
import numpy as np
import logging
from typing import Tuple, Dict, Optional
from config import INDICATORS, SIGNAL_CONFIG

logger = logging.getLogger(__name__)

# ==========================================
# INDICADORES TECNICOS
# ==========================================

class TechnicalAnalyzer:
    """Calcula todos los indicadores técnicos."""
    
    @staticmethod
    def calculate_sma(df: pd.DataFrame, period: int) -> pd.Series:
        """Media Móvil Simple."""
        return df['Close'].rolling(window=period).mean()
    
    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Índice de Fuerza Relativa (RSI).
        Rango: 0-100
        < 30: Sobreventa (potencial compra)
        > 70: Sobrecompra (potencial venta)
        """
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def calculate_macd(df: pd.DataFrame, 
                      fast: int = 12, 
                      slow: int = 26, 
                      signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD (Moving Average Convergence Divergence).
        Returns: MACD line, Signal line, Histogram
        """
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
        
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - macd_signal
        
        return macd, macd_signal, histogram
    
    @staticmethod
    def calculate_bollinger_bands(df: pd.DataFrame, 
                                  period: int = 20, 
                                  std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bandas de Bollinger.
        Returns: Upper band, Middle line, Lower band
        """
        sma = df['Close'].rolling(period).mean()
        std = df['Close'].rolling(period).std()
        
        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)
        
        return upper, sma, lower
    
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average True Range - Volatilidad.
        Valores altos = mercado volátil.
        """
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        
        return atr
    
    @staticmethod
    def calculate_obv(df: pd.DataFrame) -> pd.Series:
        """
        On-Balance Volume - Confirmación de tendencia.
        """
        obv = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
        return obv
    
    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average Directional Index - Fuerza de la tendencia.
        > 25: Tendencia fuerte
        < 20: Tendencia débil
        """
        high_diff = df['High'].diff()
        low_diff = -df['Low'].diff()
        
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
        
        tr = (df['High'] - df['Low']).rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / tr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / tr
        
        adx_raw = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = adx_raw.rolling(period).mean()
        
        return adx.fillna(0)
    
    @staticmethod
    def calculate_stochastic(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series]:
        """
        Estocástico - Momentum.
        Returns: %K y %D (signal)
        """
        low_min = df['Low'].rolling(period).min()
        high_max = df['High'].rolling(period).max()
        
        k_percent = 100 * (df['Close'] - low_min) / (high_max - low_min)
        d_percent = k_percent.rolling(3).mean()
        
        return k_percent, d_percent


# ==========================================
# ANÁLISIS Y SCORING DE SEÑALES
# ==========================================

class SignalAnalyzer:
    """Analiza señales y genera score de compra/venta."""
    
    def __init__(self, df: pd.DataFrame, symbol: str):
        self.df = df.copy()
        self.symbol = symbol
        self.latest = self.df.iloc[-1]
        self.score = {}
        
        # Calcular todos los indicadores
        self._calculate_all_indicators()
    
    def _calculate_all_indicators(self):
        """Calcula todos los indicadores una sola vez."""
        # SMA
        for period in INDICATORS["sma_periods"]:
            self.df[f"SMA_{period}"] = TechnicalAnalyzer.calculate_sma(self.df, period)
        
        # RSI
        self.df["RSI"] = TechnicalAnalyzer.calculate_rsi(self.df, INDICATORS["rsi_period"])
        
        # MACD
        macd, signal, histogram = TechnicalAnalyzer.calculate_macd(
            self.df,
            INDICATORS["macd_short"],
            INDICATORS["macd_long"],
            INDICATORS["macd_signal"]
        )
        self.df["MACD"] = macd
        self.df["MACD_Signal"] = signal
        self.df["MACD_Histogram"] = histogram
        
        # Bollinger Bands
        upper, middle, lower = TechnicalAnalyzer.calculate_bollinger_bands(
            self.df,
            INDICATORS["bb_period"],
            INDICATORS["bb_std_dev"]
        )
        self.df["BB_Upper"] = upper
        self.df["BB_Middle"] = middle
        self.df["BB_Lower"] = lower
        
        # ATR
        self.df["ATR"] = TechnicalAnalyzer.calculate_atr(self.df, INDICATORS["atr_period"])
        
        # Volume
        self.df["Volume_SMA"] = self.df["Volume"].rolling(INDICATORS["volume_period"]).mean()
        
        # OBV
        self.df["OBV"] = TechnicalAnalyzer.calculate_obv(self.df)
        
        # ADX
        self.df["ADX"] = TechnicalAnalyzer.calculate_adx(self.df, 14)
        
        # Stochastic
        k, d = TechnicalAnalyzer.calculate_stochastic(self.df, 14)
        self.df["Stoch_K"] = k
        self.df["Stoch_D"] = d
        
        self.latest = self.df.iloc[-1]
    
    def _score_trend(self) -> float:
        """Evalúa la tendencia (0-3 puntos)."""
        score = 0
        price = self.latest["Close"]
        sma20 = self.latest["SMA_20"]
        sma50 = self.latest["SMA_50"]
        sma200 = self.latest["SMA_200"]
        
        # Uptrend: precio > SMAs
        if price > sma20:
            score += 1
        if price > sma50:
            score += 1
        if price > sma200:
            score += 1
        
        # Ordenamiento correcto de SMAs
        if sma20 > sma50 > sma200:
            score += 1
        
        return min(score / 4 * 3, 3)  # Max 3 puntos
    
    def _score_momentum(self) -> float:
        """Evalúa el momentum (0-2 puntos)."""
        score = 0
        rsi = self.latest["RSI"]
        macd = self.latest["MACD"]
        macd_signal = self.latest["MACD_Signal"]
        
        # RSI: 30-70 es zona neutral, extremos indican oportunidad
        if 30 < rsi < 70:
            score += 0.5
        elif rsi < 30 or rsi > 70:
            score += 1  # Posible reversión
        
        # MACD: crossing indica momentum
        if macd > macd_signal:
            score += 1
        
        return min(score, 2)  # Max 2 puntos
    
    def _score_support_resistance(self) -> float:
        """Evalúa soporte/resistencia (0-2 puntos)."""
        score = 0
        price = self.latest["Close"]
        bb_upper = self.latest["BB_Upper"]
        bb_lower = self.latest["BB_Lower"]
        bb_middle = self.latest["BB_Middle"]
        
        # Precio cercano a bandas
        if price <= bb_lower * 1.02:
            score += 1  # Soporte (posible compra)
        if price >= bb_upper * 0.98:
            score -= 0.5  # Resistencia (posible venta)
        
        # Precio cerca del medio
        if abs(price - bb_middle) / bb_middle < 0.05:
            score += 0.5
        
        return min(max(score, 0), 2)  # Max 2 puntos
    
    def _score_volume(self) -> float:
        """Evalúa el volumen (0-1 punto)."""
        score = 0
        volume = self.latest["Volume"]
        volume_sma = self.latest["Volume_SMA"]
        
        if volume > volume_sma * 1.2:
            score = 1  # Volumen alto = confirmación
        elif volume > volume_sma:
            score = 0.5
        
        return min(score, 1)
    
    def _score_multi_timeframe(self) -> float:
        """Validación multi-timeframe (0-2 puntos)."""
        # En producción, esto analizaría múltiples timeframes
        # Por ahora, usamos ADX para validar fuerza de tendencia
        adx = self.latest["ADX"]
        
        if adx > 25:
            return 2  # Tendencia fuerte confirmada
        elif adx > 20:
            return 1  # Tendencia moderada
        else:
            return 0  # Tendencia débil
    
    def calculate_signal_score(self, signal_type: str = "BUY") -> float:
        """
        Calcula score total de la señal (0-10).
        signal_type: "BUY" o "SELL"
        """
        trend_score = self._score_trend()
        momentum_score = self._score_momentum()
        support_score = self._score_support_resistance()
        volume_score = self._score_volume()
        multi_tf_score = self._score_multi_timeframe()
        
        # Aplicar pesos
        weights = SIGNAL_CONFIG["weights"]
        total_weight = sum(weights.values())
        
        final_score = (
            (trend_score / 3 * weights["trend"]) +
            (momentum_score / 2 * weights["momentum"]) +
            (support_score / 2 * weights["support_resistance"]) +
            (volume_score / 1 * weights["volume"]) +
            (multi_tf_score / 2 * weights["multi_timeframe"])
        ) / total_weight * 10
        
        # Invertir score para SELL
        if signal_type == "SELL":
            final_score = 10 - final_score
        
        self.score = {
            "total": final_score,
            "trend": trend_score,
            "momentum": momentum_score,
            "support_resistance": support_score,
            "volume": volume_score,
            "multi_timeframe": multi_tf_score,
        }
        
        return final_score
    
    def evaluate_signal(self) -> Tuple[Optional[str], float, dict]:
        """
        Estrategia Profesional de Scoring (0-10):
        Calcula un puntaje basado en múltiples factores técnicos.
        Si el score >= 6.5 es BUY. Si el score <= 3.5 es SELL.
        """
        price = self.latest["Close"]
        
        details = {
            "price": price,
            "rsi": self.latest["RSI"],
            "macd": self.latest["MACD"],
            "adx": self.latest["ADX"],
            "volume_ratio": self.latest["Volume"] / self.latest["Volume_SMA"] if self.latest["Volume_SMA"] > 0 else 0,
        }
        
        # Calcular el score ponderado real (0 a 10) usando la función interna
        buy_score = self.calculate_signal_score(signal_type="BUY")
        sell_score = self.calculate_signal_score(signal_type="SELL")
        
        details.update(self.score) # Añade el desglose del score (trend, momentum, etc)
        
        # Lógica de decisión dinámica basada en umbrales configurables
        from config import SIGNAL_CONFIG
        buy_threshold = SIGNAL_CONFIG.get("buy_threshold", 6.5)
        sell_threshold = SIGNAL_CONFIG.get("sell_threshold", 6.5) # Asumimos simetría si no está especificado
        
        if buy_score >= buy_threshold:
            return "BUY", buy_score, details
            
        if sell_score >= sell_threshold:
            return "SELL", sell_score, details
            
        # Si no llega al umbral, devolvemos NINGUNA, pero entregamos el score actual (el más alto)
        max_score = max(buy_score, sell_score)
        return None, max_score, details
    
    def print_analysis(self):
        """Imprime análisis detallado sin emojis."""
        print(f"\n{'='*60}")
        print(f"ANALISIS TECNICO: {self.symbol}")
        print(f"{'='*60}")
        print(f"Precio: {self.latest['Close']:.2f}")
        print(f"SMA20:  {self.latest['SMA_20']:.2f} | SMA50:  {self.latest['SMA_50']:.2f} | SMA200: {self.latest['SMA_200']:.2f}")
        print(f"RSI:    {self.latest['RSI']:.2f} | ADX: {self.latest['ADX']:.2f}")
        print(f"MACD:   {self.latest['MACD']:.4f} | Signal: {self.latest['MACD_Signal']:.4f}")
        print(f"Volume: {self.latest['Volume']:.0f} (SMA: {self.latest['Volume_SMA']:.0f})")
        print(f"{'='*60}\n")
