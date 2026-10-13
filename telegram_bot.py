import requests
import logging
from datetime import datetime
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

    def send_buy_alert(self, symbol: str, asset_type: str, price: float, qty: float, cost: float, score: float, atr: float, spread: float, reason: str, is_real: bool):
        """Formatea y envía una alerta de COMPRA detallada."""
        tipo_ejecucion = "🟢 REAL/PAPER (Limit Order)" if is_real else "🟡 SIMULACION INTERNA"
        hora = datetime.now().strftime('%H:%M:%S')
        
        msg = (
            f"✅ <b>NUEVA POSICIÓN: COMPRA</b>\n\n"
            f"<b>Ticker:</b> {symbol}\n"
            f"<b>Tipo:</b> {asset_type.upper()}\n"
            f"<b>Precio Entrada:</b> ${price:.4f}\n"
            f"<b>Cantidad:</b> {qty:.4f}\n"
            f"<b>Inversión:</b> ${cost:.2f}\n"
            f"<b>Score Bot:</b> {score:.2f}/10\n"
            f"<b>Motivo:</b> {reason}\n"
            f"<b>ATR:</b> ${atr:.4f}\n"
            f"<b>Spread:</b> {spread*100:.2f}%\n"
            f"<b>Hora:</b> {hora}\n\n"
            f"<i>Modo: {tipo_ejecucion}</i>"
        )
        self.send_message(msg)

    def send_sell_alert(self, symbol: str, entry_price: float, exit_price: float, qty: float, pnl: float, pnl_pct: float, hold_time: float, reason: str, atr: float, spread: float, is_real: bool):
        """Formatea y envía una alerta de VENTA detallada."""
        tipo_ejecucion = "🔴 REAL/PAPER (Market/Limit)" if is_real else "🟡 SIMULACION INTERNA"
        hora = datetime.now().strftime('%H:%M:%S')
        
        # Emoji dinámico según PnL
        icono = "🔥" if pnl > 0 else "🩸"
        
        msg = (
            f"{icono} <b>POSICIÓN CERRADA: VENTA</b>\n\n"
            f"<b>Ticker:</b> {symbol}\n"
            f"<b>Precio Entrada:</b> ${entry_price:.4f}\n"
            f"<b>Precio Salida:</b> ${exit_price:.4f}\n"
            f"<b>Rentabilidad:</b> {pnl_pct*100:+.2f}%\n"
            f"<b>PnL USD:</b> ${pnl:+.2f}\n"
            f"<b>Tiempo en pos.:</b> {hold_time:.1f} hrs\n"
            f"<b>Motivo Salida:</b> {reason}\n"
            f"<b>ATR Actual:</b> ${atr:.4f}\n"
            f"<b>Spread Salida:</b> {spread*100:.2f}%\n"
            f"<b>Hora:</b> {hora}\n\n"
            f"<i>Modo: {tipo_ejecucion}</i>"
        )
        self.send_message(msg)

    def send_post_mortem_report(self, report_text: str):
        """Envía el reporte largo de Post-Mortem y Estadísticas Semanales."""
        # Telegram limite es de 4096 caracteres. 
        # Lo dividimos si es necesario.
        parts = [report_text[i:i+4000] for i in range(0, len(report_text), 4000)]
        for part in parts:
            self.send_message(part)

    def send_summary(self, detected: int, executed: int, capital: float, config_details: dict, evaluations: list, validations: list = None, portfolio_summary: str = ""):
        """Envía el resumen al finalizar el escaneo por lotes con detalles del sistema y tabla de resultados."""

        ia_status = "✅ Activada" if config_details.get("ia_enabled") else "❌ Desactivada"
        live_status = "🟢 REAL/PAPER" if config_details.get("live_trading") else "🟡 SIMULACIÓN INTERNA"

        # Truncar listas inmensas para no romper el límite de Telegram
        max_items = 40
        if evaluations and len(evaluations) > max_items:
            evaluations = evaluations[:max_items] + [f"... y {len(evaluations)-max_items} más ocultos."]
        evals_text = "\n".join(evaluations) if evaluations else "Sin datos procesados."
        
        vals_text = ""
        if validations:
            if len(validations) > max_items:
                validations = validations[:max_items] + [f"... y {len(validations)-max_items} más ocultos."]
            vals_text = "<b>Validaciones:</b>\n<pre>" + "\n".join(validations) + "</pre>\n\n"

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
            f"<pre>{evals_text}</pre>\n\n"
            f"{vals_text}"
            f"<b>Estado del Portafolio:</b>\n"
            f"<pre>{portfolio_summary}</pre>"
        )
        self.send_message(msg)