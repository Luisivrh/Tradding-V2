import json
import logging
from datetime import datetime
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


def _scalar_float(value):
    """Convierte un valor de pandas/JSON a float escalar, o None si no es usable."""
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return None
        value = value.iloc[-1, 0]
    if isinstance(value, pd.Series):
        if value.empty:
            return None
        value = value.iloc[0]
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


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
                                close_1d = _scalar_float(future_data.iloc[0]['Close'])
                                if close_1d is not None:
                                    entry["plus_1d"] = close_1d
                                    updated = True
                            if days_passed >= 3 and entry["plus_3d"] is None and len(future_data) >= 3:
                                close_3d = _scalar_float(future_data.iloc[2]['Close'])
                                if close_3d is not None:
                                    entry["plus_3d"] = close_3d
                                    updated = True
                            if days_passed >= 5 and entry["plus_5d"] is None and len(future_data) >= 5:
                                close_5d = _scalar_float(future_data.iloc[4]['Close'])
                                if close_5d is not None:
                                    entry["plus_5d"] = close_5d
                                    updated = True
            
            if updated:
                with open(self.speculative_tracker_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error actualizando speculative tracker: {e}")

    def _get_current_price(self, symbol):
        """Intenta obtener el precio actual para la evaluación retroactiva priorizando Alpaca."""
        try:
            if not symbol:
                return None
            
            from config import ASSETS, BROKERS_CONFIG
            is_crypto = "-USD" in symbol or "/" in symbol or symbol in ASSETS.get("crypto", [])
            alp_sym = symbol.replace("-USD", "/USD") if "-USD" in symbol else symbol

            # 1. Priorizar Alpaca Market Data (Midpoint no sesgado, sin rate limits de Yahoo)
            if self.data_fetcher and getattr(self.data_fetcher, 'alpaca_client', None) and not symbol.endswith("=X"):
                try:
                    if is_crypto:
                        from alpaca.data.requests import CryptoLatestQuoteRequest
                        from alpaca.data.historical import CryptoHistoricalDataClient
                        crypto_client = CryptoHistoricalDataClient(
                            api_key=BROKERS_CONFIG["alpaca"]["api_key"],
                            secret_key=BROKERS_CONFIG["alpaca"]["secret_key"]
                        )
                        req = CryptoLatestQuoteRequest(symbol_or_symbols=alp_sym)
                        quote = crypto_client.get_crypto_latest_quote(req)
                        if alp_sym in quote:
                            ask = float(quote[alp_sym].ask_price or 0.0)
                            bid = float(quote[alp_sym].bid_price or 0.0)
                            if ask > 0 and bid > 0:
                                return (ask + bid) / 2.0
                            return ask if ask > 0 else bid
                    else:
                        from alpaca.data.requests import StockLatestQuoteRequest
                        req = StockLatestQuoteRequest(symbol_or_symbols=alp_sym)
                        quote = self.data_fetcher.alpaca_client.get_stock_latest_quote(req)
                        if alp_sym in quote:
                            ask = float(quote[alp_sym].ask_price or 0.0)
                            bid = float(quote[alp_sym].bid_price or 0.0)
                            if ask > 0 and bid > 0:
                                return (ask + bid) / 2.0
                            return ask if ask > 0 else bid
                except Exception:
                    pass

            # 2. Respaldo para Forex o cuando Alpaca no esté disponible
            if symbol.endswith("=X") or not self.data_fetcher or not getattr(self.data_fetcher, 'alpaca_client', None):
                import yfinance as yf
                df = yf.download(symbol.replace("-", "-"), period="1d", progress=False)
                if not df.empty:
                    close = df['Close']
                    last_close = close.iloc[-1] if not isinstance(close, pd.DataFrame) else close.iloc[-1, 0]
                    return _scalar_float(last_close)
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
            try:
                exit_date = datetime.fromisoformat(trade['exit_date'])
            except (TypeError, ValueError):
                logger.warning("Post-Mortem omite trade con exit_date inválida: %s", trade.get('symbol'))
                continue
            days_passed = (now - exit_date).total_seconds() / 86400.0
            
            milestones = [(1, '1d'), (3, '3d'), (5, '5d'), (10, '10d')]
            needs_update = False
            
            for days, label in milestones:
                price_key = f"price_after_{label}"
                if days_passed >= days and trade.get(price_key) is None:
                    needs_update = True
            
            if needs_update:
                current_price = self._get_current_price(trade.get('symbol'))
                exit_price = _scalar_float(trade.get('exit_price'))
                if not exit_price or exit_price <= 0:
                    logger.warning(
                        "Post-Mortem omite %s: exit_price inválido (%s)",
                        trade.get('symbol'),
                        trade.get('exit_price'),
                    )
                    continue
                if current_price and current_price > 0:
                    for days, label in milestones:
                        price_key = f"price_after_{label}"
                        profit_key = f"missed_profit_{label}"
                        
                        if days_passed >= days and trade.get(price_key) is None:
                            trade[price_key] = current_price
                            # Si es compra y vendimos, la oportunidad perdida es (Precio Actual - Precio Venta) / Precio Venta
                            trade[profit_key] = (current_price - exit_price) / exit_price
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

    def generate_weekly_report(self, alpaca_client=None, risk_manager=None) -> str:
        """Genera el reporte estadístico de Post-Mortem y calcula Alpha semanal contra SPY."""
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

        # Benchmark semanal contra S&P 500 (SPY) y cálculo de Alpha
        try:
            account_weekly_ret = None
            if alpaca_client:
                try:
                    from alpaca.trading.requests import GetPortfolioHistoryRequest
                    req = GetPortfolioHistoryRequest(period="1W", timeframe="1D")
                    hist = alpaca_client.get_portfolio_history(history_filter=req)
                    if hist and hasattr(hist, 'equity') and hist.equity and len(hist.equity) >= 2:
                        start_eq = float(hist.equity[0])
                        end_eq = float(hist.equity[-1])
                        if start_eq > 0:
                            account_weekly_ret = (end_eq - start_eq) / start_eq
                except Exception as e_hist:
                    logger.debug(f"Error consultando get_portfolio_history en Alpaca: {e_hist}")

            if account_weekly_ret is None and risk_manager:
                base_eq = getattr(risk_manager, 'weekly_baseline_equity', None)
                curr_eq = risk_manager.get_total_portfolio_value(is_forex=False)
                if base_eq and base_eq > 0:
                    account_weekly_ret = (curr_eq - base_eq) / base_eq

            spy_weekly_ret = None
            if self.data_fetcher:
                spy_df = self.data_fetcher.fetch_stock("SPY")
                if not spy_df.empty and len(spy_df) >= 6:
                    spy_weekly_ret = float((spy_df["Close"].iloc[-1] - spy_df["Close"].iloc[-6]) / spy_df["Close"].iloc[-6])

            if account_weekly_ret is not None and spy_weekly_ret is not None:
                alpha_pct = (account_weekly_ret - spy_weekly_ret) * 100.0
                report += f"\n🌐 <b>BENCHMARK SEMANAL (vs S&P 500 / SPY):</b>\n"
                report += f"• Retorno Cuenta (1S): {account_weekly_ret*100:+.2f}%\n"
                report += f"• Retorno SPY (1S):    {spy_weekly_ret*100:+.2f}%\n"
                report += f"• Alpha Generado:      {alpha_pct:+.2f}%\n"
            elif spy_weekly_ret is not None:
                report += f"\n🌐 <b>BENCHMARK SEMANAL:</b>\n• Retorno SPY (5d): {spy_weekly_ret*100:+.2f}%\n"
        except Exception as e:
            logger.debug(f"Error generando benchmark semanal: {e}")
            
        return report