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
        self.max_stocks = SCREENER_CONFIG.get("max_dynamic_stocks", 10)
        self.sources = SCREENER_CONFIG.get("sources", ["alpaca", "yahoo"])
        
        self.alpaca_client = None
        self._init_alpaca()

    def _init_alpaca(self):
        """Inicializa Alpaca si hay credenciales disponibles."""
        try:
            # Reutilizamos las credenciales de trading para Screener
            api_key = BROKERS_CONFIG["alpaca"]["api_key"]
            secret_key = BROKERS_CONFIG["alpaca"]["secret_key"]
            if api_key and secret_key:
                from alpaca.data.historical import StockHistoricalDataClient
                # El cliente de datos es independiente del paper/live
                self.alpaca_client = StockHistoricalDataClient(api_key, secret_key)
        except Exception as e:
            logger.warning(f"Screener: No se pudo inicializar Alpaca Data Client: {e}")

    def get_dynamic_tickers(self) -> List[str]:
        """Obtiene una lista de símbolos interesantes del día usando fuentes híbridas."""
        if not self.enabled:
            return []

        tickers = set()
        
        for source in self.sources:
            if len(tickers) >= self.max_stocks:
                break
                
            logger.info(f"Screener buscando oportunidades vía: {source.upper()}")
            try:
                if source == "alpaca":
                    new_tickers = self._get_alpaca_movers()
                    tickers.update(new_tickers)
                elif source == "yahoo":
                    new_tickers = self._get_yahoo_gainers()
                    tickers.update(new_tickers)
            except Exception as e:
                logger.error(f"Screener {source} falló: {e}. Intentando siguiente fuente...")
                continue
                
        # Limitar al máximo configurado
        final_list = list(tickers)[:self.max_stocks]
        if final_list:
            logger.info(f"Screener encontró {len(final_list)} acciones activas: {', '.join(final_list)}")
        return final_list

    def _get_alpaca_movers(self) -> List[str]:
        """Usa Alpaca API para buscar Top Movers (requiere suscripción o usa endpoints básicos)"""
        # Nota: Alpaca Data API v2 top movers a veces requiere plan pago. 
        # Intentaremos obtener snapshot de algunas populares si el endpoint falla, 
        # pero la API de screener directa en python-sdk está limitada. 
        # Por seguridad de fallback, levantaremos Exception si no soporta movers gratis.
        if not self.alpaca_client:
            raise ValueError("Alpaca Client no configurado.")
            
        from alpaca.data.requests import StockLatestQuoteRequest
        
        # Como hack gratis en Alpaca, verificamos un pool de acciones volátiles conocidas
        # Si quisieramos el Top Movers Real de Alpaca, usaríamos la API REST directa.
        import requests
        headers = {
            "APCA-API-KEY-ID": BROKERS_CONFIG["alpaca"]["api_key"],
            "APCA-API-SECRET-KEY": BROKERS_CONFIG["alpaca"]["secret_key"]
        }
        # Endpoint de movers (solo US Equity)
        url = "https://data.alpaca.markets/v1beta1/screener/stocks/movers"
        resp = requests.get(url, headers=headers, params={"top": self.max_stocks})
        
        if resp.status_code == 200:
            data = resp.json()
            gainers = [item['symbol'] for item in data.get('gainers', [])]
            losers = [item['symbol'] for item in data.get('losers', [])]
            return gainers + losers
        else:
            raise Exception(f"Alpaca Screener error {resp.status_code}: {resp.text}")

    def _get_yahoo_gainers(self) -> List[str]:
        """Usa yahoo_fin para obtener Day Gainers y Most Active"""
        try:
            from yahoo_fin import stock_info as si
        except ImportError:
            logger.error("yahoo_fin no está instalado. Ejecuta: pip install yahoo_fin")
            return []
            
        tickers = []
        try:
            # Obtener las que más suben hoy
            df_gainers = si.get_day_gainers()
            if not df_gainers.empty:
                tickers.extend(df_gainers['Symbol'].head(self.max_stocks // 2).tolist())
        except Exception as e:
            logger.warning(f"No se pudieron obtener Yahoo Gainers: {e}")
            
        try:
            # Obtener las más activas
            df_active = si.get_day_most_active()
            if not df_active.empty:
                tickers.extend(df_active['Symbol'].head(self.max_stocks // 2).tolist())
        except Exception as e:
            logger.warning(f"No se pudieron obtener Yahoo Active: {e}")

        # Limpiar sufijos extraños si los hay y asegurar formato estándar
        clean_tickers = [t.split('.')[0] for t in tickers if isinstance(t, str)]
        return clean_tickers

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    screener = DynamicScreener()
    print(screener.get_dynamic_tickers())