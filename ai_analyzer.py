"""
==============================================
ANALIZADOR DE IA (GEMINI) - BOT TRADING
==============================================
Módulo para auditoría de riesgo, noticias financieras
y veto de operaciones utilizando Google Gemini.
Opera como Comité de Riesgos (Risk Veto) con reintentos
exponenciales ante saturación 503 y salida JSON estructurada.
"""

import os
import sys
import json
import time
import logging
import requests
from typing import Dict, Optional, Tuple
from google import genai

from config import GEMINI_API_KEY, NEWSAPI_KEY, AI_CONFIG

logger = logging.getLogger(__name__)


RISK_AUDIT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "approved": {"type": "BOOLEAN"},
        "bankruptcy_risk": {"type": "BOOLEAN"},
        "dilution_risk": {"type": "BOOLEAN"},
        "delisting_risk": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"}
    },
    "required": ["approved", "reason"]
}


class AIAnalyzer:
    """Comité de Riesgos y Auditoría de Sentimiento con Google Gemini."""

    def __init__(self):
        self.enabled = AI_CONFIG.get("enabled", True)
        self.model_name = AI_CONFIG.get("model", "gemini-flash-lite-latest")
        self.gemini_key = GEMINI_API_KEY
        self.newsapi_key = NEWSAPI_KEY
        self.client = None
        self._setup()

    def _setup(self):
        """Configura el cliente de Gemini con opciones seguras."""
        if not self.enabled:
            logger.info("IA deshabilitada en la configuración.")
            return

        if not self.gemini_key:
            logger.warning("GEMINI_API_KEY no encontrada. La IA será desactivada.")
            self.enabled = False
            return

        try:
            # En Windows con proxy/certificados locales, client_args permite operar
            opts = {"client_args": {"verify": False}} if sys.platform == "win32" else {}
            self.client = genai.Client(api_key=self.gemini_key, http_options=opts)
            self.model_name = self.model_name if "gemini" in self.model_name else "gemini-flash-lite-latest"
            logger.info(f"Cliente Gemini configurado correctamente. Usando modelo: '{self.model_name}'")
        except Exception as e:
            logger.error(f"Error al configurar Gemini: {e}")
            self.enabled = False

    def fetch_news(self, symbol: str) -> str:
        """Obtiene las últimas noticias para un símbolo usando NewsAPI."""
        if not self.newsapi_key:
            return "Sin noticias de NewsAPI (falta API Key)."

        query = symbol.split("-")[0]
        if query == "BTC": query = "Bitcoin"
        if query == "ETH": query = "Ethereum"

        url = f"https://newsapi.org/v2/everything?q={query}&language=en&sortBy=publishedAt&pageSize=5&apiKey={self.newsapi_key}"
        
        try:
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get("status") == "ok" and data.get("articles"):
                headlines = [article["title"] for article in data["articles"] if article.get("title")]
                return "\n".join(f"- {h}" for h in headlines[:5])
            return "No se encontraron noticias recientes específicas."
        except Exception as e:
            logger.warning(f"Error consultando NewsAPI para {symbol}: {e}")
            return "No disponibles."

    def audit_risk_veto(self, symbol: str, asset_type: str, technical_details: Dict) -> Tuple[bool, str]:
        """
        [FASE 3] Comité de Riesgo de IA (Poder de Veto).
        Evalúa si el activo tiene riesgo inminente de quiebra, desliste, dilución o fraude.
        Retorna:
            - approved (bool): True si la compra está aprobada; False si está VETADA.
            - reason (str): Explicación técnica/fundamental del veredicto.
        """
        if not self.enabled or not getattr(self, "client", None):
            if asset_type in ["warrant", "penny"]:
                return False, "VETO_PREVENTIVO: IA deshabilitada; prohibido operar activos especulativos sin auditoría."
            return True, "APROBADO: IA deshabilitada; permitido para activo institucional."

        news_text = self.fetch_news(symbol)

        prompt = f"""
        Eres el Director de Gestión de Riesgo de un fondo de inversión algorítmico.
        Tu trabajo es auditar y vetar compras en empresas que presenten riesgo existencial de pérdida total.

        Activo a auditar: {symbol} (Categoría: {asset_type.upper()})
        Precio actual: ${technical_details.get('price', 0):.4f}
        RSI: {technical_details.get('rsi', 0):.1f}

        Titulares de Noticias Recientes:
        {news_text}

        EVALUACIÓN DE BANDERAS ROJAS CRÍTICAS:
        1. ¿Existe riesgo inminente de bancarrota, quiebra, Chapter 11 o insolvencia?
        2. ¿Hay riesgo severo de dilución masiva de acciones (reverse splits continuos, ofertas tóxicas convertibles)?
        3. ¿Hay notificación formal de desliste inminente de la bolsa (delisting notice)?
        4. ¿Hay investigación criminal por fraude contable o auditoría rechazada por la SEC?

        REGLA DE DECISIÓN ESTRICTA:
        - Si detectas CUALQUIERA de esos 4 riesgos, approved DEBE ser false (VETO).
        - Si es una empresa sana o sin riesgo catastrófico inminente, approved DEBE ser true.

        Debes responder EXCLUSIVAMENTE con este formato JSON:
        {{
            "approved": true o false,
            "bankruptcy_risk": true o false,
            "dilution_risk": true o false,
            "delisting_risk": true o false,
            "reason": "Explicación directa de 1 a 2 oraciones."
        }}
        """

        candidate_models = [self.model_name, "gemini-flash-lite-latest", "gemini-flash-latest", "gemini-3.5-flash-lite"]
        seen = set()
        candidate_models = [m for m in candidate_models if not (m in seen or seen.add(m))]

        max_retries = 3
        for attempt in range(max_retries):
            for model_id in candidate_models:
                try:
                    resp = self.client.models.generate_content(
                        model=model_id,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": RISK_AUDIT_SCHEMA
                        }
                    )
                    text = resp.text.strip()
                    data = json.loads(text)
                    # [VETO POR DEFECTO] Si falta el campo approved, asumir False por seguridad
                    approved = bool(data.get("approved", False))
                    reason = str(data.get("reason", "Auditoría completada."))
                    return approved, reason
                except Exception as e:
                    logger.warning(f"Intento {attempt+1} con modelo {model_id} para {symbol} falló: {e}")
                    continue
            time.sleep(2 ** attempt)

        # Política de Fallo (Fail-Safe) si se agotan todos los reintentos:
        if asset_type in ["warrant", "penny"]:
            logger.error(f"❌ [VETO FAIL-SAFE] IA no disponible tras {max_retries} intentos para {symbol} ({asset_type}). Compra vetada.")
            return False, "VETO_FAILSAFE: IA no disponible tras reintentos para activo especulativo."
        else:
            logger.warning(f"⚠️ [FALLBACK APROBADO] IA no disponible para {symbol} ({asset_type}). Aprobado por solidez institucional.")
            return True, "APROBADO_FALLBACK: IA no disponible tras reintentos; permitido por categoría institucional."

    def analyze_batch_sentiment(self, asset_group_name: str, symbols_data: Dict[str, Dict]) -> Dict[str, Tuple[float, str]]:
        """Compatibilidad legacy: ahora la IA audita riesgo en audit_risk_veto."""
        return {sym: (5.0, "Auditoría diferida a fase de veto.") for sym in symbols_data.keys()}

    def analyze_sentiment(self, symbol: str, technical_details: Dict) -> Tuple[float, str]:
        """Compatibilidad con análisis de sentimiento legacy."""
        approved, reason = self.audit_risk_veto(symbol, "normal", technical_details)
        score = 8.0 if approved else 2.0
        return score, reason


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyzer = AIAnalyzer()
    dummy = {"price": 220.0, "rsi": 55.0}
    app, reas = analyzer.audit_risk_veto("AAPL", "normal", dummy)
    print(f"AAPL Aprobado: {app} | Razón: {reas}")
