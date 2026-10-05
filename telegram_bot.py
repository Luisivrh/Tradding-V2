import requests
import logging
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Envía notificaciones del bot de trading a Telegram vía API HTTP cruda."""
    
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        
        if not self.enabled:
            logger.info("Telegram Notifier: Deshabilitado (Faltan credenciales en .env)")

    def send_message(self, message: str) -> bool:
        """Envía un mensaje de texto al chat configurado."""
        if not self.enabled:
            return False
            
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Error enviando Telegram: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Excepción en Telegram API: {e}")
            return False

    def send_error(self, context: str, error_msg: str):
        """Envía una alerta de error crítico (API fallida, rechazo de broker)."""
        msg = (
            f"⚠️ <b>ERROR CRÍTICO EN EL SISTEMA</b> ⚠️\n\n"
            f"<b>Contexto:</b> {context}\n"
            f"<b>Detalle:</b>\n<code>{error_msg}</code>"
        )
        self.send_message(msg)

    def send_trade_alert(self, symbol: str, action: str, price: float, score: float, qty: float, is_real: bool):
        """Formatea y envía una alerta cuando se ejecuta una compra/venta."""
        tipo_ejecucion = "🟢 REAL/PAPER" if is_real else "🟡 SIMULACION INTERNA"
        
        msg = (
            f"🚨 <b>ALERTA DE TRADING</b> 🚨\n\n"
            f"<b>Activo:</b> {symbol}\n"
            f"<b>Accion:</b> {action}\n"
            f"<b>Precio:</b> ${price:.2f}\n"
            f"<b>Cantidad:</b> {qty:.4f}\n"
            f"<b>Score Bot:</b> {score:.2f}/10\n\n"
            f"<i>Modo: {tipo_ejecucion}</i>"
        )
        self.send_message(msg)

    def send_summary(self, detected: int, executed: int, capital: float, config_details: dict, evaluations: list):
        """Envía el resumen al finalizar el escaneo por lotes con detalles del sistema y tabla de resultados."""
        
        ia_status = "✅ Activada" if config_details.get("ia_enabled") else "❌ Desactivada"
        live_status = "🟢 REAL/PAPER" if config_details.get("live_trading") else "🟡 SIMULACIÓN INTERNA"
        
        evals_text = "\n".join(evaluations) if evaluations else "Sin datos procesados."
        
        msg = (
            f"📊 <b>REPORTE DE ESCANEO COMPLETADO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Resumen de Operaciones:</b>\n"
            f"• Señales encontradas: {detected}\n"
            f"• Trades ejecutados: {executed}\n"
            f"• Capital Actual: ${capital:,.2f}\n\n"
            f"<b>Configuración del Sistema:</b>\n"
            f"• Ejecución: {live_status}\n"
            f"• Inteligencia Artificial: {ia_status}\n"
            f"• Umbral Compra/Venta: {config_details.get('buy_threshold')}/10\n"
            f"• Activos Escaneados: {config_details.get('total_assets')}\n\n"
            f"<b>Resultados del Escaneo:</b>\n"
            f"<pre>{evals_text}</pre>"
        )
        self.send_message(msg)