"""
==============================================
DESCARGA DE DATOS - BOT TRADING
==============================================
Obtiene datos históricos de múltiples fuentes:
- Criptomonedas: Binance (CCXT)
- Acciones: AlphaVantage
- Divisas: Disponibles en futuras versiones
"""

import pandas as pd
import os
import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime, timedelta
import requests
import yfinance as yf
import time
import random
try:
    import ccxt
except ImportError:
    ccxt = None

from config import DATA_CONFIG, TIINGO_API_KEY, ASSETS

logger = logging.getLogger(__name__)

# Configurar Tiingo API Key en la variable de entorno global que lee pandas-datareader
if TIINGO_API_KEY:
    os.environ["TIINGO_API_KEY"] = TIINGO_API_KEY
import pandas_datareader as pdr

# ==========================================
# DATA FETCHER CON CACHE
# ==========================================

class DataFetcher:
    """Descarga y cachea datos de mercado."""
    
    def __init__(self):
        self.cache_dir = Path(DATA_CONFIG["cache_dir"])
        self.cache_enabled = DATA_CONFIG["cache_enabled"]
        self.days_history = DATA_CONFIG["days_history"]
        
        if self.cache_enabled:
            self.cache_dir.mkdir(exist_ok=True)
        
        # Inicializar APIs
        self.binance = ccxt.binance() if ccxt else None
        
        # Limpieza automática del caché viejo
        if self.cache_enabled:
            self._cleanup_old_cache()
            
    def _cleanup_old_cache(self, max_days_old: int = 7):
        """Elimina archivos de caché que tengan más de X días de antigüedad para ahorrar espacio."""
        try:
            now = datetime.now()
            deleted_count = 0
            for file_path in self.cache_dir.glob("*.parquet"):
                # Revisar la fecha de modificación del archivo
                modified_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                if (now - modified_time).days > max_days_old:
                    file_path.unlink()
                    deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"🧹 Limpieza automática: {deleted_count} archivos de caché viejos eliminados.")
        except Exception as e:
            logger.error(f"Error durante limpieza de caché: {e}")
    
    def _get_cache_path(self, symbol: str) -> Path:
        """Ruta del archivo de caché."""
        safe_symbol = symbol.replace("/", "_").replace("-", "_")
        return self.cache_dir / f"{safe_symbol}_data.parquet"
    
    def _load_from_cache(self, symbol: str) -> pd.DataFrame:
        """Carga datos del caché si existen."""
        if not self.cache_enabled:
            return None
        
        cache_path = self._get_cache_path(symbol)
        
        if not cache_path.exists():
            return None
        
        try:
            df = pd.read_parquet(cache_path)
            # Validar que el caché no sea muy antiguo (menos de 24 horas)
            if 'last_updated' in df.columns:
                last_update = pd.to_datetime(df['last_updated'].iloc[-1])
                if datetime.now() - last_update < timedelta(hours=24):
                    logger.info(f"[CACHE] {symbol} - Datos cargados desde caché")
                    return df.drop(columns=['last_updated'], errors='ignore')
        except Exception as e:
            logger.warning(f"Error al cargar caché de {symbol}: {e}")
        
        return None
    
    def _save_to_cache(self, symbol: str, df: pd.DataFrame):
        """Guarda datos en caché."""
        if not self.cache_enabled or df.empty:
            return
        
        try:
            cache_path = self._get_cache_path(symbol)
            df['last_updated'] = datetime.now()
            df.to_parquet(cache_path)
        except Exception as e:
            logger.warning(f"Error al guardar caché de {symbol}: {e}")
    
    def fetch_crypto(self, symbol: str) -> pd.DataFrame:
        """
        Descarga datos de criptomonedas desde Binance.
        Symbol format: BTC-USD, ETH-USD, etc.
        """
        # Intentar caché
        cached_df = self._load_from_cache(symbol)
        if cached_df is not None:
            return cached_df
        
        if not self.binance:
            logger.error("CCXT no está instalado. Instala: pip install ccxt")
            return pd.DataFrame()
        
        try:
            logger.info(f"Descargando datos de cripto: {symbol}")
            market = symbol.replace("-USD", "/USDT")
            
            # Descargar OHLCV
            ohlcv = self.binance.fetch_ohlcv(market, timeframe='1d', limit=self.days_history)
            
            df = pd.DataFrame(
                ohlcv,
                columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]
            )
            
            df["Date"] = pd.to_datetime(df["Timestamp"], unit="ms")
            df = df.set_index("Date")
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            
            self._save_to_cache(symbol, df)
            logger.info(f"[OK] {symbol}: {len(df)} velas descargadas")
            
            return df
        
        except Exception as e:
            logger.error(f"Error descargando {symbol}: {e}")
            return pd.DataFrame()
    
    def fetch_stock(self, symbol: str) -> pd.DataFrame:
        """
        Descarga datos de acciones usando yfinance para evitar límites de API.
        """
        # Intentar caché
        cached_df = self._load_from_cache(symbol)
        if cached_df is not None:
            return cached_df
        
        try:
            logger.info(f"Descargando datos de accion (yfinance): {symbol}")
            
            # yfinance usa sufijos diferentes o ninguno. 
            # Aseguramos un formato limpio
            yf_symbol = symbol.replace("/", "-")
            
            # Formato de periodo (ej. 730d para 2 años aprox)
            period_str = f"{self.days_history}d"
            
            # Pausa aleatoria para no ser baneado por Yahoo Finance (RateLimitError)
            time.sleep(random.uniform(1.0, 2.5))
            
            # Crear una sesión HTTP que ignore la verificación SSL para saltar el error de "Crumb/Cookie"
            session = requests.Session()
            session.verify = False
            
            # suppress yfinance prints
            df = yf.download(yf_symbol, period=period_str, progress=False, session=session)
            
            if df.empty:
                logger.warning(f"No se encontraron datos en Yahoo Finance para {symbol}")
                return pd.DataFrame()
            
            # yfinance devuelve DataFrames multi-index a veces. Aplanamos si es necesario.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
                
            # Limpiar el índice y asegurar nombres estándar
            df.index.name = "Date"
            df = df.rename(columns={
                'Open': 'Open',
                'High': 'High',
                'Low': 'Low',
                'Close': 'Close',
                'Volume': 'Volume'
            })
            
            # Quitamos tz si existe
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
                
            df = df.sort_index(ascending=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            
            # Validar que tengamos suficientes datos
            if len(df) < 50:
                logger.warning(f"Insuficientes datos para {symbol} (Solo {len(df)} velas)")
                return pd.DataFrame()
                
            self._save_to_cache(symbol, df)
            logger.info(f"[OK] {symbol}: {len(df)} velas descargadas")
            
            return df
        
        except Exception as e:
            logger.error(f"Error descargando {symbol} con yfinance: {e}")
            return pd.DataFrame()
    
    def fetch_market_data(self, symbol: str) -> pd.DataFrame:
        """
        Descarga datos de cualquier símbolo.
        Auto-detecta el tipo (crypto/stock/forex) y usa el método apropiado.
        """
        # Criptomonedas (tienen guion medio en nuestro formato de Binance, ej BTC-USD)
        if "-USD" in symbol or "/" in symbol:
            return self.fetch_crypto(symbol)
        else:
            # Acciones y Forex pasan por Tiingo
            return self.fetch_stock(symbol)
    
    def fetch_all_assets(self) -> dict:
        """Descarga datos de todos los activos configurados."""
        data = {}
        
        for asset_type, symbols in ASSETS.items():
            logger.info(f"\n[+] Descargando sector {asset_type}...")
            
            for symbol in symbols:
                df = self.fetch_market_data(symbol)
                
                if not df.empty:
                    data[symbol] = df
                else:
                    logger.warning(f"No se obtuvieron datos para {symbol}")
        
        return data


# ==========================================
# PREPARACION DE DATOS
# ==========================================

class DataPreprocessor:
    """Prepara y valida datos."""
    
    @staticmethod
    def validate_data(df: pd.DataFrame, symbol: str, min_candles: int = 200) -> bool:
        """Valida que los datos sean suficientes y válidos."""
        if df.empty:
            logger.warning(f"{symbol}: DataFrame vacío")
            return False
        
        if len(df) < min_candles:
            logger.warning(f"{symbol}: Insuficientes datos ({len(df)} < {min_candles})")
            return False
        
        if df['Close'].isna().sum() > 0:
            logger.warning(f"{symbol}: Valores NaN encontrados")
            df = df.dropna()
        
        if (df['High'] < df['Low']).any():
            logger.warning(f"{symbol}: High < Low detectado")
            return False
        
        return True
    
    @staticmethod
    def remove_outliers(df: pd.DataFrame, column: str = 'Close', std_dev: float = 3) -> pd.DataFrame:
        """Elimina outliers usando desviación estándar."""
        mean = df[column].mean()
        std = df[column].std()
        
        mask = (df[column] >= mean - std_dev * std) & (df[column] <= mean + std_dev * std)
        removed_count = len(df) - mask.sum()
        
        if removed_count > 0:
            logger.info(f"Outliers removidos: {removed_count}")
        
        return df[mask]
    
    @staticmethod
    def fill_gaps(df: pd.DataFrame) -> pd.DataFrame:
        """Rellena posibles gaps en los datos."""
        df = df.fillna(method='ffill')  # Forward fill
        df = df.dropna()
        return df


# ==========================================
# UTILIDADES
# ==========================================

def get_current_price(symbol: str) -> float:
    """Obtiene el precio actual de un símbolo."""
    try:
        df = yf.download(symbol, period="1d", progress=False)
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except:
        pass
    
    return None


def compare_timeframes(symbol: str) -> dict:
    """
    Compara un activo en diferentes timeframes.
    Útil para análisis multi-timeframe.
    """
    try:
        data_1d = yf.download(symbol, period="60d", interval="1d", progress=False)
        data_1h = yf.download(symbol, period="30d", interval="1h", progress=False)
        
        return {
            "1d": data_1d[-20:],  # Últimas 20 velas diarias
            "1h": data_1h[-168:],  # Últimas 168 velas horarias
        }
    except:
        return {}


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    fetcher = DataFetcher()
    
    # Descargar datos
    data = fetcher.fetch_all_assets()
    
    # Validar
    preprocessor = DataPreprocessor()
    for symbol, df in data.items():
        is_valid = preprocessor.validate_data(df, symbol)
        print(f"{symbol}: {'✅ Válido' if is_valid else '❌ Inválido'}")
