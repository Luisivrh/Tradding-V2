import json
import logging
from datetime import datetime
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)

class PostMortemAnalyzer:
    def __init__(self, data_fetcher=None):
        self.audit_file = Path("audit_log.json")
        self.speculative_tracker_file = Path("speculative_tracker.json")
        self.data_fetcher = data_fetcher
        
        # Ensure tracker exists
        if not self.speculative_tracker_file.exists():
            with open(self.speculative_tracker_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
    
    def log_speculative_signal(self, ticker: str, price: float, score: float, spread: float):
        """Registra un BUY especulativo en modo calibración para seguimiento (Ghost Trade)."""
        try:
            with open(self.speculative_tracker_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Avoid duplicate logs for the same ticker on the same day
            today = datetime.now().strftime("%Y-%m-%d")
            for entry in data:
                if entry["ticker"] == ticker and entry["date"][:10] == today:
                    return
                    
            data.append({
                "ticker": ticker,
                "date": datetime.now().isoformat(),
                "price_signal": price,
                "score_final": score,
                "spread": spread,
                "plus_1d": None,
                "plus_3d": None,
                "plus_5d": None
            })
            
            with open(self.speculative_tracker_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error registrando speculative signal en tracker: {e}")

    def update_speculative_signals(self):
        """Actualiza precios a 1, 3 y 5 días para los Ghost Trades."""
        try:
            with open(self.speculative_tracker_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            updated = False
            for entry in data:
                if entry["plus_1d"] is not None and entry["plus_3d"] is not None and entry["plus_5d"] is not None:
                    continue
                    
                date_signal = datetime.fromisoformat(entry["date"])
                days_passed = (datetime.now() - date_signal).days
                
                if days_passed >= 1 and self.data_fetcher:
                    df = self.data_fetcher.fetch_market_data(entry["ticker"], period="1mo")
                    if not df.empty:
                        future_data = df[df.index > pd.to_datetime(date_signal.date())]
                        if not future_data.empty:
                            if days_passed >= 1 and entry["plus_1d"] is None and len(future_data) >= 1:
                                entry["plus_1d"] = float(future_data.iloc[0]['Close'])
                                updated = True
                            if days_passed >= 3 and entry["plus_3d"] is None and len(future_data) >= 3:
                                entry["plus_3d"] = float(future_data.iloc[2]['Close'])
                                updated = True
                            if days_passed >= 5 and entry["plus_5d"] is None and len(future_data) >= 5:
                                entry["plus_5d"] = float(future_data.iloc[4]['Close'])
                                updated = True
            
            if updated:
                with open(self.speculative_tracker_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error actualizando speculative tracker: {e}")

    def _get_current_price(self, symbol):
        """Intenta obtener el precio actual para la evaluación retroactiva."""
        try:
            import yfinance as yf
            df = yf.download(symbol.replace("-", "-"), period="1d", progress=False)
            if not df.empty:
                return float(df['Close'].iloc[-1])
        except Exception as e:
            logger.debug(f"PostMortem no pudo obtener precio para {symbol}: {e}")
        return None

    def update_logs(self):
        """Busca operaciones que cerraron hace días y evalúa su rendimiento posterior."""
        if not self.audit_file.exists():
            return
            
        try:
            with open(self.audit_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except Exception as e:
            logger.error(f"Error leyendo audit_log.json: {e}")
            return
            
        updated = False
        now = datetime.now()
        
        for trade in logs:
            if 'exit_date' not in trade:
                continue
                
            exit_date = datetime.fromisoformat(trade['exit_date'])
            days_passed = (now - exit_date).total_seconds() / 86400.0
            
            milestones = [(1, '1d'), (3, '3d'), (5, '5d'), (10, '10d')]
            needs_update = False
            
            for days, label in milestones:
                price_key = f"price_after_{label}"
                if days_passed >= days and trade.get(price_key) is None:
                    needs_update = True
            
            if needs_update:
                current_price = self._get_current_price(trade['symbol'])
                if current_price and current_price > 0:
                    for days, label in milestones:
                        price_key = f"price_after_{label}"
                        profit_key = f"missed_profit_{label}"
                        
                        if days_passed >= days and trade.get(price_key) is None:
                            trade[price_key] = current_price
                            # Si es compra y vendimos, la oportunidad perdida es (Precio Actual - Precio Venta) / Precio Venta
                            trade[profit_key] = (current_price - trade['exit_price']) / trade['exit_price']
                            updated = True
                            
                    # Update Exit Quality Score
                    max_recovery = -999.0
                    for _, l in milestones:
                        val = trade.get(f"missed_profit_{l}")
                        if val is not None and val > max_recovery:
                            max_recovery = val
                            
                    if max_recovery != -999.0:
                        if max_recovery > 1.0: trade['exit_quality_score'] = "CATASTROFICO"
                        elif max_recovery > 0.5: trade['exit_quality_score'] = "MUY MALO"
                        elif max_recovery > 0.3: trade['exit_quality_score'] = "MALO"
                        elif max_recovery <= 0: trade['exit_quality_score'] = "EXCELENTE"
                        else: trade['exit_quality_score'] = "NEUTRO"

        if updated:
            try:
                with open(self.audit_file, 'w', encoding='utf-8') as f:
                    json.dump(logs, f, indent=4)
                logger.info("✅ Post-Mortem Audit Log actualizado con nuevos precios históricos.")
            except Exception as e:
                logger.error(f"Error guardando audit_log.json: {e}")

    def generate_weekly_report(self) -> str:
        """Genera el reporte estadístico de Post-Mortem."""
        if not self.audit_file.exists():
            return "No hay datos de auditoría suficientes para el reporte."
            
        try:
            with open(self.audit_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except Exception:
            return "Error leyendo audit_log.json."
            
        if not logs:
            return "No hay trades cerrados para analizar."
            
        total_trades = len(logs)
        valid_mfe_mae = [t for t in logs if 'mfe_pct' in t and 'mae_pct' in t]
        
        avg_mfe = sum(t['mfe_pct'] for t in valid_mfe_mae) / len(valid_mfe_mae) if valid_mfe_mae else 0.0
        avg_mae = sum(t['mae_pct'] for t in valid_mfe_mae) / len(valid_mfe_mae) if valid_mfe_mae else 0.0
        
        # Recuperación (Missed Profit)
        max_missed = 0.0
        sum_missed = 0.0
        count_missed = 0
        up_1d = 0; count_1d = 0
        up_3d = 0; count_3d = 0
        up_5d = 0; count_5d = 0
        
        for t in logs:
            highest_missed = -999.0
            for label in ['1d', '3d', '5d', '10d']:
                val = t.get(f"missed_profit_{label}")
                if val is not None:
                    if val > highest_missed: highest_missed = val
            if highest_missed != -999.0:
                if highest_missed > max_missed: max_missed = highest_missed
                sum_missed += highest_missed
                count_missed += 1
                
            if t.get('missed_profit_1d') is not None:
                count_1d += 1
                if t['missed_profit_1d'] > 0: up_1d += 1
                
            if t.get('missed_profit_3d') is not None:
                count_3d += 1
                if t['missed_profit_3d'] > 0: up_3d += 1
                
            if t.get('missed_profit_5d') is not None:
                count_5d += 1
                if t['missed_profit_5d'] > 0: up_5d += 1
                
        avg_missed = sum_missed / count_missed if count_missed > 0 else 0.0
        
        # Categorías de Reason
        reasons = {}
        for t in logs:
            reason = t.get('reason', 'UNKNOWN')
            # Extract main reason keyword
            main_reason = reason.split()[0].replace('_ATR', '').replace('_SIGNAL', '') 
            if main_reason not in reasons:
                reasons[main_reason] = {"count": 0, "pnl": 0.0, "missed": 0.0, "m_count": 0}
            
            reasons[main_reason]["count"] += 1
            reasons[main_reason]["pnl"] += t.get('profit_pct', 0.0)
            
            t_missed = max([t.get(f"missed_profit_{l}") or -999 for l in ['1d', '3d', '5d', '10d']])
            if t_missed != -999:
                reasons[main_reason]["missed"] += t_missed
                reasons[main_reason]["m_count"] += 1
        
        # Ranking Mejores/Peores
        sorted_logs = sorted(logs, key=lambda x: x.get('profit_pct', 0.0))
        top_peores = sorted_logs[:10]
        top_mejores = sorted_logs[-10:][::-1]
        
        report = "🔬 <b>REPORTE POST-MORTEM & MICROESTRUCTURA</b>\n"
        report += "━━━━━━━━━━━━━━━━━━━\n\n"
        report += f"<b>Total Trades Analizados:</b> {total_trades}\n"
        report += f"<b>Promedio MFE (Max Favorable):</b> {avg_mfe*100:+.2f}%\n"
        report += f"<b>Promedio MAE (Max Adverso):</b> {avg_mae*100:+.2f}%\n\n"
        
        report += "📊 <b>OPORTUNIDAD PERDIDA (Rebote Post-Venta):</b>\n"
        report += f"• Promedio Recuperación: {avg_missed*100:+.2f}%\n"
        report += f"• Máxima Recuperación Perdida: {max_missed*100:+.2f}%\n"
        report += f"• % Veces que subió al día 1: {(up_1d/count_1d*100) if count_1d else 0:.1f}%\n"
        report += f"• % Veces que subió al día 3: {(up_3d/count_3d*100) if count_3d else 0:.1f}%\n"
        report += f"• % Veces que subió al día 5: {(up_5d/count_5d*100) if count_5d else 0:.1f}%\n\n"
        
        report += "⚠️ <b>DESEMPEÑO POR TIPO DE SALIDA:</b>\n"
        for r, data in reasons.items():
            avg_pnl = data['pnl'] / data['count']
            avg_m = data['missed'] / data['m_count'] if data['m_count'] else 0
            report += f"• <b>{r}</b> ({data['count']} trades) | PnL: {avg_pnl*100:+.2f}% | Recup. Posterior: {avg_m*100:+.2f}%\n"
            
        report += "\n🥇 <b>TOP 3 MEJORES TRADES:</b>\n"
        for t in top_mejores[:3]:
            report += f"• {t['symbol']} | {t.get('profit_pct',0)*100:+.2f}% | {t.get('reason','')} \n"
            
        report += "\n🩸 <b>TOP 3 PEORES TRADES:</b>\n"
        for t in top_peores[:3]:
            report += f"• {t['symbol']} | {t.get('profit_pct',0)*100:+.2f}% | {t.get('reason','')} \n"
            
        return report