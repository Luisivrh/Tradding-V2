"""
==============================================
SCREENER DINÁMICO HÍBRIDO (ALPACA + YAHOO)
==============================================
Encuentra oportunidades en tiempo real buscando
acciones con alto volumen o grandes movimientos.
"""

import logging
import requests
import urllib3
from typing import List
from config import BROKERS_CONFIG, SCREENER_CONFIG

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# Parche global para evitar errores SSL en Windows/VMs sin certificados
original_request = requests.Session.request
def patched_request(self, method, url, **kwargs):
    kwargs['verify'] = False
    return original_request(self, method, url, **kwargs)
requests.Session.request = patched_request

logger = logging.getLogger(__name__)

class DynamicScreener:
    def __init__(self):
        self.enabled = SCREENER_CONFIG.get("enabled", False)
        # Leer configuración V2
        self.max_stocks = SCREENER_CONFIG.get("max_dynamic_stocks", 40)
        self.allocation = SCREENER_CONFIG.get("allocation", {
            "speculative": 15, "institutional": 10, "highs": 5, "watchlist": 10
        })
        self.watchlist = SCREENER_CONFIG.get("watchlist", [])
        
        self.alpaca_client = None
        self._init_alpaca()

    def _init_alpaca(self):
        """Inicializa Alpaca si hay credenciales disponibles."""
        try:
            api_key = BROKERS_CONFIG["alpaca"]["api_key"]
            secret_key = BROKERS_CONFIG["alpaca"]["secret_key"]
            if api_key and secret_key:
                from alpaca.data.historical import StockHistoricalDataClient
                self.alpaca_client = StockHistoricalDataClient(api_key, secret_key)
        except Exception as e:
            logger.warning(f"Screener: No se pudo inicializar Alpaca Data Client: {e}")

    def get_dynamic_tickers(self) -> List[dict]:
        """Obtiene candidatos etiquetados desde 4 universos (Screener V2)."""
        if not self.enabled:
            return []

        candidates = []
        seen = set()

        # Universo D: Watchlist (Prioridad más alta)
        for sym in self.watchlist:
            if sym not in seen:
                candidates.append({"symbol": sym, "source": "watchlist"})
                seen.add(sym)

        # Universo A: Especulativo (Movers/Gainers)
        try:
            spec_quota = self.allocation.get("speculative", 15)
            spec_tickers = self._get_alpaca_movers() + self._get_yahoo_gainers()
            added_spec = 0
            for sym in spec_tickers:
                if sym not in seen and added_spec < spec_quota:
                    candidates.append({"symbol": sym, "source": "speculative"})
                    seen.add(sym)
                    added_spec += 1
        except Exception as e:
            logger.error(f"Error obteniendo Universo A (Especulativo): {e}")

        # Universo C: 52 Week Highs
        try:
            highs_quota = self.allocation.get("highs", 5)
            highs_tickers = self._get_52_week_highs()
            added_highs = 0
            for sym in highs_tickers:
                if sym not in seen and added_highs < highs_quota:
                    candidates.append({"symbol": sym, "source": "52_week_highs"})
                    seen.add(sym)
                    added_highs += 1
        except Exception as e:
            logger.error(f"Error obteniendo Universo C (52W Highs): {e}")

        # Universo B: Institucional (Momentum / Relative Strength proxies)
        try:
            inst_quota = self.allocation.get("institutional", 10)
            inst_tickers = self._get_institutional_momentum_proxy(inst_quota * 2)
            added_inst = 0
            for sym in inst_tickers:
                if sym not in seen and added_inst < inst_quota:
                    candidates.append({"symbol": sym, "source": "institutional"})
                    seen.add(sym)
                    added_inst += 1
        except Exception as e:
            logger.error(f"Error obteniendo Universo B (Institucional): {e}")

        logger.info(f"Screener V2 generó {len(candidates)} candidatos.")
        return candidates

    def _get_alpaca_movers(self) -> List[str]:
        if not self.alpaca_client:
            return []
        try:
            import requests
            headers = {
                "APCA-API-KEY-ID": BROKERS_CONFIG["alpaca"]["api_key"],
                "APCA-API-SECRET-KEY": BROKERS_CONFIG["alpaca"]["secret_key"]
            }
            url = "https://data.alpaca.markets/v1beta1/screener/stocks/movers"
            resp = requests.get(url, headers=headers, params={"top": 15})
            if resp.status_code == 200:
                data = resp.json()
                gainers = [item['symbol'] for item in data.get('gainers', [])]
                return gainers
        except Exception as e:
            logger.debug(f"Alpaca movers error: {e}")
        return []

    def _get_yahoo_gainers(self) -> List[str]:
        try:
            from yahoo_fin import stock_info as si
            tickers = []
            df_gainers = si.get_day_gainers()
            if not df_gainers.empty:
                tickers.extend(df_gainers['Symbol'].head(15).tolist())
            clean_tickers = [t.split('.')[0] for t in tickers if isinstance(t, str)]
            return clean_tickers
        except Exception as e:
            logger.debug(f"Yahoo gainers error: {e}")
            return []
            
    def _get_52_week_highs(self) -> List[str]:
        """Intenta obtener 52-week highs (o usa un proxy estático robusto si falla yahoo_fin)."""
        try:
            # En producción real, requeriría parsear Finviz o similar si yahoo_fin no tiene endpoint directo
            # Por ahora simulamos con líderes de momentum conocidos si no hay endpoint nativo
            import pandas as pd
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            tables = pd.read_html(url)
            df = tables[4] # Indice de componentes suele ser la tabla 4
            return df['Ticker'].head(15).tolist() # Proxy temporal
        except Exception:
            return ["AAPL", "MSFT", "COST", "LRCX", "V"] # Fallback

    def _get_institutional_momentum_proxy(self, count: int) -> List[str]:
        """
        Devuelve líderes institucionales basados en Momentum Real y Fuerza Relativa.
        Descarga historial reciente y filtra Precio > SMA50 ordenando por retorno.
        """
        pool = [
            "AAPL", "MSFT", "NVDA", "AVGO", "META", "AMZN", "GOOGL", "NFLX", 
            "JPM", "V", "MA", "UNH", "LLY", "JNJ", "ABBV", "MRK", "PG", "HD",
            "XOM", "CVX", "COP", "EOG", "SLB", "CAT", "LIN", "GE", "HON",
            "BA", "LMT", "RTX", "COST", "WMT", "KO", "PEP", "MCD", "TMO",
            "DIS", "NKE", "SBUX", "PFE", "TMO", "ABT", "DHR", "ISRG", "SYK",
            "AMD", "INTC", "TXN", "QCOM", "AMAT", "MU", "ADI", "LRCX", "KLAC",
            "CRM", "ORCL", "ADBE", "IBM", "NOW", "INTU", "NOW", "UBER", "CSCO",
            "GS", "BAC", "WFC", "C", "MS", "AXP", "BLK", "SCHW", "CB", "MMC",
            "UNP", "UPS", "RTX", "BA", "LMT", "GD", "NOC", "TDG", "WM", "TGT",
            "VZ", "KMB", "MO", "GIS", "EL", "SYY", "CL", "K", "MDLZ", "STZ"
        ]
        # Remove duplicates
        pool = list(set(pool))
        try:
            import yfinance as yf
            import pandas as pd
            logger.info("Calculando momentum institucional en tiempo real...")
            # Descargamos últimos 3 meses para calcular momentum simple
            data = yf.download(pool, period="3mo", progress=False)
            if data.empty or 'Close' not in data:
                import random
                return random.sample(pool, min(count, len(pool)))
                
            closes = data['Close']
            momentum_list = []
            
            for ticker in pool:
                if ticker in closes.columns:
                    ticker_closes = closes[ticker].dropna()
                    if len(ticker_closes) > 20:
                        current = ticker_closes.iloc[-1]
                        sma50 = ticker_closes.tail(50).mean() if len(ticker_closes) >= 50 else ticker_closes.mean()
                        past = ticker_closes.iloc[0]
                        # Filtrar: Precio debe estar sobre su SMA50 (tendencia alcista)
                        if current > sma50:
                            momentum = (current - past) / past
                            momentum_list.append((ticker, momentum))
            
            # Ordenar por momentum (fuerza relativa) de mayor a menor
            momentum_list.sort(key=lambda x: x[1], reverse=True)
            selected = [t[0] for t in momentum_list[:count]]
            
            if not selected:
                import random
                return random.sample(pool, min(count, len(pool)))
            return selected
            
        except Exception as e:
            logger.error(f"Error en filtro de momentum institucional: {e}")
            import random
            return random.sample(pool, min(count, len(pool)))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    screener = DynamicScreener()
    cands = screener.get_dynamic_tickers()
    for c in cands:
        print(c)