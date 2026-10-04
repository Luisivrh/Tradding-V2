"""
==============================================
ANALIZADOR DE IA (GEMINI) - BOT TRADING
==============================================
Módulo para obtener noticias financieras y analizar
el sentimiento del mercado utilizando Google Gemini.
"""

import os
import json
import logging
import requests
import urllib3
from typing import Dict, Optional, Tuple
import google.generativeai as genai

# Ocultar advertencia de seguridad SSL (necesario en Windows sin certificados)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import GEMINI_API_KEY, NEWSAPI_KEY, AI_CONFIG

logger = logging.getLogger(__name__)

class AIAnalyzer:
    """Analizador de sentimiento utilizando Google Gemini y NewsAPI."""

    def __init__(self):
        self.enabled = AI_CONFIG["enabled"]
        self.model_name = AI_CONFIG["model"]
        self.gemini_key = GEMINI_API_KEY
        self.newsapi_key = NEWSAPI_KEY
        
        self.model = None
        self._setup()

    def _setup(self):
        """Configura el modelo de Gemini si está habilitado."""
        if not self.enabled:
            logger.info("IA deshabilitada en la configuración.")
            return

        if not self.gemini_key:
            logger.warning("GEMINI_API_KEY no encontrada. La IA será desactivada.")
            self.enabled = False
            return

        try:
            genai.configure(api_key=self.gemini_key)
            # Utilizamos gemini-1.5-flash por defecto o el que venga en la config
            model_to_use = self.model_name if "gemini" in self.model_name else "gemini-1.5-flash"
            self.model = genai.GenerativeModel(model_to_use)
            logger.info(f"Modelo Gemini '{model_to_use}' configurado correctamente.")
        except Exception as e:
            logger.error(f"Error al configurar Gemini: {e}")
            self.enabled = False

    def fetch_news(self, symbol: str) -> str:
        """Obtiene las últimas noticias para un símbolo usando NewsAPI."""
        if not self.newsapi_key:
            logger.warning("NEWSAPI_KEY no encontrada. No se usarán noticias recientes en el análisis.")
            return "No se pudieron obtener noticias recientes (falta API Key)."

        # Limpiar el símbolo para la búsqueda (ej. BTC-USD -> Bitcoin o BTC)
        query = symbol.split("-")[0]
        if query == "BTC": query = "Bitcoin"
        if query == "ETH": query = "Ethereum"

        url = f"https://newsapi.org/v2/everything?q={query}&language=en&sortBy=publishedAt&pageSize=5&apiKey={self.newsapi_key}"
        
        try:
            # verify=False added to fix SSL certificate errors on some Windows machines
            response = requests.get(url, timeout=5, verify=False)
            data = response.json()
            
            if data.get("status") == "ok" and data.get("articles"):
                headlines = [article["title"] for article in data["articles"]]
                return "\n".join(f"- {h}" for h in headlines)
            else:
                logger.warning(f"Error en NewsAPI para {symbol}: {data.get('message', 'Sin artículos')}")
                return "No se encontraron noticias recientes."
        except Exception as e:
            logger.error(f"Error al conectar con NewsAPI: {e}")
            return f"Error al obtener noticias: {e}"

    def analyze_batch_sentiment(self, asset_group_name: str, symbols_data: Dict[str, Dict]) -> Dict[str, Tuple[float, str]]:
        """
        Analiza el sentimiento de un grupo entero de activos en una sola llamada a Gemini.
        Esto ahorra cuota de la API y mantiene el contexto del mercado.
        Retorna: Diccionario { "BTC-USD": (score, "explicacion"), ... }
        """
        if not self.enabled or not self.model or not symbols_data:
            return {sym: (5.0, "IA deshabilitada.") for sym in symbols_data.keys()}

        # 1. Obtener noticias generales del grupo (ej. "crypto", "stocks")
        query_map = {"crypto": "cryptocurrency", "stocks": "stock market", "forex": "forex", "commodities": "commodities"}
        search_query = query_map.get(asset_group_name, "finance")
        news_text = self.fetch_news(search_query)

        # 2. Preparar el resumen técnico de todos los activos
        tech_summary = ""
        for sym, data in symbols_data.items():
            tech_summary += f"- {sym}: Precio ${data.get('price', 0):.2f}, RSI {data.get('rsi', 0):.1f}, MACD {data.get('macd', 0):.4f}\n"

        prompt = f"""
        Eres un analista financiero experto evaluando el sector: {asset_group_name.upper()}.
        
        ### Noticias del Sector:
        {news_text}
        
        ### Datos Técnicos Actuales:
        {tech_summary}
        
        Evalúa el sentimiento de CADA activo basándote en su estado técnico y las noticias macro del sector.
        DEBES responder EXCLUSIVAMENTE con un objeto JSON con este formato exacto:
        {{
            "ACTIVO_1": {{"score": 7.5, "explanation": "Razon breve"}},
            "ACTIVO_2": {{"score": 4.0, "explanation": "Razon breve"}}
        }}
        El score debe ser un float entre 0.0 (muy bajista) y 10.0 (muy alcista).
        """

        try:
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # Limpiar posible formato markdown que envuelva el JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
                
            result = json.loads(response_text)
            
            # Formatear salida asegurando que todos los símbolos existan
            final_scores = {}
            for sym in symbols_data.keys():
                if sym in result:
                    score = float(result[sym].get("score", 5.0))
                    score = max(0.0, min(10.0, score))
                    expl = result[sym].get("explanation", "Sin explicación.")
                    final_scores[sym] = (score, expl)
                else:
                    final_scores[sym] = (5.0, "Omitido por la IA.")
            
            return final_scores
            
        except Exception as e:
            logger.error(f"Error en análisis batch de Gemini para {asset_group_name}: {e}")
            return {sym: (5.0, f"Error IA: {e}") for sym in symbols_data.keys()}
        """
        Analiza el sentimiento combinando datos técnicos y noticias recientes.
        Retorna:
            - score (float): Puntaje de 0.0 (Bearish) a 10.0 (Bullish). Neutro es 5.0.
            - explanation (str): Breve explicación generada por la IA.
        """
        if not self.enabled or not self.model:
            return 5.0, "IA deshabilitada."

        news_text = self.fetch_news(symbol)
        
        prompt = f"""
        Eres un analista financiero experto. Necesito que analices el sentimiento actual para el activo {symbol}.
        
        ### Datos Técnicos Recientes:
        - Precio Actual: {technical_details.get('price', 'N/A')}
        - RSI: {technical_details.get('rsi', 'N/A'):.2f} (Considera sobrecomprado > 70, sobrevendido < 30)
        - MACD: {technical_details.get('macd', 'N/A'):.4f}
        
        ### Titulares de Noticias Recientes:
        {news_text}
        
        En base a estos datos técnicos y las noticias, proporciona tu análisis.
        DEBES responder EXCLUSIVAMENTE con un objeto JSON en el siguiente formato, sin markdown extra:
        {{
            "score": <float entre 0.0 y 10.0 donde 0 es muy bajista, 10 es muy alcista y 5 es neutro>,
            "explanation": "<tu explicación breve y directa, máximo 2 oraciones>"
        }}
        """

        try:
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # Limpiar posible formato markdown que envuelva el JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
                
            result = json.loads(response_text)
            
            score = float(result.get("score", 5.0))
            # Limitar score entre 0 y 10
            score = max(0.0, min(10.0, score))
            explanation = result.get("explanation", "Sin explicación.")
            
            return score, explanation
            
        except Exception as e:
            logger.error(f"Error en análisis de Gemini para {symbol}: {e}")
            return 5.0, f"Error en análisis de IA: {e}"

if __name__ == "__main__":
    # Prueba rápida aislada
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv()
    
    analyzer = AIAnalyzer()
    dummy_technical = {"price": 95000, "rsi": 65, "macd": 150}
    score, expl = analyzer.analyze_sentiment("BTC-USD", dummy_technical)
    print(f"Score: {score}/10")
    print(f"Razón: {expl}")
