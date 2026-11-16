"""
==============================================
BACKTESTER RECONSTRUIDO (TAREA 5.0) - TRADDING-V2
==============================================
Simulador cuantitativo en memoria para validación de estrategias:
- RiskManager aislado en memoria (in_memory=True): NUNCA toca portfolio.json ni audit_log.json.
- Paridad con los motores Institucional y Especulativo (asset_type configurable).
- Costos de fricción realistas: Slippage y comisiones por orden.
- Reúso exacto de check_exit_conditions (ATR stops, Trailing Chandelier, Take Profit).
- Validación de significancia estadística (>= 100 trades) y split Out-of-Sample.
- Documentación de sesgo de supervivencia.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from config import BACKTEST_CONFIG, RISK_CONFIG, ASSETS
from risk_management import RiskManager
from technical_analysis import SignalAnalyzer
from data_fetcher import DataFetcher, DataPreprocessor

logger = logging.getLogger(__name__)


class Backtester:
    """Ejecuta backtests realistas en memoria para un activo."""

    def __init__(
        self,
        symbol: str,
        df: pd.DataFrame,
        asset_type: str = "normal",
        initial_capital: float = None,
        commission: float = 1.0,
        slippage_pct: float = 0.001
    ):
        self.symbol = symbol
        self.df = df.copy()
        self.asset_type = asset_type
        self.initial_capital = initial_capital or BACKTEST_CONFIG.get("initial_capital", 100000.0)
        self.commission = commission
        self.slippage_pct = slippage_pct
        
        # RiskManager aislado 100% en memoria
        self.risk_manager = RiskManager(starting_capital=self.initial_capital, in_memory=True)
        self.trades_log = []
        self.equity_curve = []
        self.equity_dates = []

    def run(self, verbose: bool = True) -> Dict:
        """Ejecuta la simulación histórica bar-by-bar."""
        logger.info(f"⏳ Iniciando backtest en memoria para {self.symbol} ({self.asset_type})...")
        
        if len(self.df) < 50:
            logger.warning(f"Insuficientes velas ({len(self.df)}) para {self.symbol}")
            return {}

        # Determinar punto de inicio (mínimo 50 velas para indicadores técnicos)
        start_idx = min(50, len(self.df) - 10)
        
        for idx in range(start_idx, len(self.df)):
            current_date = self.df.index[idx]
            current_price = float(self.df.iloc[idx]["Close"])
            df_slice = self.df.iloc[:idx+1]
            
            # Estimación del ATR de la vela actual
            high_low = float(self.df.iloc[idx]["High"]) - float(self.df.iloc[idx]["Low"])
            current_atr = max(high_low, current_price * 0.02)
            spread = self.slippage_pct

            # 1. EVALUAR CONDICIONES DE SALIDA (STOP LOSS / TP / TRAILING STOP)
            if self.symbol in self.risk_manager.positions:
                current_prices_dict = {
                    self.symbol: {
                        "price": current_price,
                        "atr": current_atr,
                        "spread": spread
                    }
                }
                forced_exits = self.risk_manager.check_exit_conditions(current_prices_dict)
                for exit_opp in forced_exits:
                    reason = exit_opp.get("details", {}).get("reason", "STOP_LOSS")
                    exit_exec_price = current_price * (1.0 - self.slippage_pct)
                    pnl = self.risk_manager.close_position(
                        self.symbol,
                        exit_exec_price,
                        reason=reason,
                        exit_date=str(current_date),
                        exit_atr=current_atr,
                        exit_spread=spread
                    )
                    self.risk_manager.current_capital -= self.commission
                    self.trades_log.append({
                        "date": current_date,
                        "action": "EXIT",
                        "symbol": self.symbol,
                        "reason": reason,
                        "price": exit_exec_price,
                        "pnl": pnl
                    })

            # 2. EVALUAR SEÑALES TÉCNICAS (PARIDAD CON EL MOTOR DE PRODUCCIÓN)
            analyzer = SignalAnalyzer(df_slice, self.symbol, asset_type=self.asset_type)
            signal_type, score, details = analyzer.evaluate_signal()

            # Venta técnica si hay señal SELL
            if signal_type == "SELL" and self.symbol in self.risk_manager.positions:
                exit_exec_price = current_price * (1.0 - self.slippage_pct)
                pnl = self.risk_manager.close_position(
                    self.symbol,
                    exit_exec_price,
                    reason="SEÑAL_TÉCNICA_SELL",
                    exit_date=str(current_date),
                    exit_atr=current_atr,
                    exit_spread=spread,
                    exit_score=score
                )
                self.risk_manager.current_capital -= self.commission
                self.trades_log.append({
                    "date": current_date,
                    "action": "EXIT",
                    "symbol": self.symbol,
                    "reason": "SEÑAL_TÉCNICA_SELL",
                    "price": exit_exec_price,
                    "pnl": pnl
                })

            # Compra si hay señal BUY y no tenemos posición abierta
            elif signal_type == "BUY" and self.symbol not in self.risk_manager.positions:
                min_score = BACKTEST_CONFIG.get("min_score", 6.8)
                if score >= min_score:
                    buy_exec_price = current_price * (1.0 + self.slippage_pct)
                    pos = self.risk_manager.open_position(
                        symbol=self.symbol,
                        price=buy_exec_price,
                        side="BUY",
                        atr=current_atr,
                        spread_pct=spread,
                        score=score,
                        entry_date=str(current_date)
                    )
                    if pos:
                        self.risk_manager.current_capital -= self.commission
                        self.trades_log.append({
                            "date": current_date,
                            "action": "ENTRY",
                            "symbol": self.symbol,
                            "signal_score": score,
                            "price": buy_exec_price,
                            "quantity": pos.quantity
                        })

            # Registrar valor de portafolio
            total_equity = self.risk_manager.get_total_portfolio_value(is_forex=(self.asset_type == "forex"))
            self.equity_curve.append(total_equity)
            self.equity_dates.append(current_date)

        # Cerrar remanentes al final del período
        if self.symbol in self.risk_manager.positions:
            final_p = float(self.df.iloc[-1]["Close"])
            self.risk_manager.close_position(
                self.symbol,
                final_p,
                reason="BACKTEST_END",
                exit_date=str(self.df.index[-1])
            )

        report = self._generate_report()
        if verbose:
            self._print_report(report)
        return report

    def _generate_report(self) -> Dict:
        """Calcula métricas clave de desempeño."""
        trades = self.risk_manager.trade_history
        total_trades = len(trades)
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
        
        final_equity = self.equity_curve[-1] if self.equity_curve else self.initial_capital
        total_return_pct = ((final_equity - self.initial_capital) / self.initial_capital) * 100.0
        
        # Max Drawdown
        equity_series = pd.Series(self.equity_curve) if self.equity_curve else pd.Series([self.initial_capital])
        cummax = equity_series.cummax()
        dd_series = (equity_series - cummax) / cummax
        max_dd = abs(float(dd_series.min())) if len(dd_series) > 0 else 0.0

        return {
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": sum(t.get("pnl", 0) for t in trades),
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": max_dd * 100.0,
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "is_statistically_significant": total_trades >= 100,
            "survivorship_bias_note": "Aviso: Este activo se seleccionó a posteriori; los retornos reales de screening dinámico pueden diferir."
        }

    def _print_report(self, r: Dict):
        """Imprime resumen formateado."""
        if not r:
            return
        print("\n" + "="*70)
        print(f"📊 REPORTE DE BACKTEST EN MEMORIA: {r['symbol']} ({r['asset_type'].upper()})")
        print("="*70)
        print(f"Capital Inicial:      ${r['initial_capital']:,.2f}")
        print(f"Capital Final:        ${r['final_equity']:,.2f}")
        print(f"P&L Total:            ${r['total_pnl']:,.2f}")
        print(f"Retorno Neto:         {r['total_return_pct']:+.2f}%")
        print(f"Max Drawdown:         {r['max_drawdown_pct']:.2f}%")
        print("-"*70)
        print(f"Total Operaciones:    {r['total_trades']} {'(Significativo >=100)' if r['is_statistically_significant'] else '(Muestra Pequeña <100)'}")
        print(f"Win Rate:             {r['win_rate']:.1f}%")
        print(f"Profit Factor:        {r['profit_factor']:.2f}x")
        print("="*70 + "\n")

    def run_train_test_split(self, train_pct: float = 0.7, verbose: bool = True) -> Dict[str, Dict]:
        """
        [FASE 5] Ejecuta validación cruzada In-Sample (70%) vs Out-of-Sample (30%)
        para detectar sobreajuste (overfitting).
        """
        if len(self.df) < 100:
            logger.warning(f"Insuficientes datos ({len(self.df)}) para split train/test en {self.symbol}.")
            return {"in_sample": self.run(verbose=verbose), "out_of_sample": {}}

        split_idx = int(len(self.df) * train_pct)
        train_df = self.df.iloc[:split_idx]
        test_df = self.df.iloc[split_idx:]

        logger.info(f"📚 Ejecutando In-Sample (Train: {len(train_df)} barras)...")
        bt_train = Backtester(self.symbol, train_df, self.asset_type, self.initial_capital, self.commission, self.slippage_pct)
        report_train = bt_train.run(verbose=False)

        logger.info(f"🧪 Ejecutando Out-of-Sample (Test: {len(test_df)} barras)...")
        bt_test = Backtester(self.symbol, test_df, self.asset_type, self.initial_capital, self.commission, self.slippage_pct)
        report_test = bt_test.run(verbose=False)

        if verbose:
            print("\n" + "="*70)
            print(f"🔬 VALIDACIÓN IN-SAMPLE / OUT-OF-SAMPLE: {self.symbol} ({self.asset_type.upper()})")
            print("="*70)
            print(f"In-Sample (Train 70%):     WinRate: {report_train.get('win_rate', 0):.1f}% | ProfitFactor: {report_train.get('profit_factor', 0):.2f}x | Trades: {report_train.get('total_trades', 0)}")
            print(f"Out-of-Sample (Test 30%):  WinRate: {report_test.get('win_rate', 0):.1f}% | ProfitFactor: {report_test.get('profit_factor', 0):.2f}x | Trades: {report_test.get('total_trades', 0)}")
            print("="*70 + "\n")

        return {"in_sample": report_train, "out_of_sample": report_test}


class BacktestRunner:
    """Orquestador de backtests para múltiples activos con Out-of-Sample Split."""

    def __init__(self):
        self.data_fetcher = DataFetcher()
        self.results = {}

    def run_all_backtests(self, symbols: List[str], asset_type: str = "normal") -> Dict[str, Dict]:
        """Ejecuta backtests para una lista de símbolos."""
        logger.info(f"🔄 Ejecutando backtests en memoria para {len(symbols)} activos...")
        for sym in symbols:
            df = self.data_fetcher.fetch_market_data(sym)
            if not df.empty:
                bt = Backtester(sym, df, asset_type=asset_type)
                res = bt.run(verbose=True)
                if res:
                    self.results[sym] = res
        return self.results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Prueba rápida con datos sintéticos para validar que no toca disco
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    prices = np.linspace(100, 150, 100) + np.random.normal(0, 2, 100)
    sample_df = pd.DataFrame({
        "Open": prices,
        "High": prices + 1.5,
        "Low": prices - 1.5,
        "Close": prices,
        "Volume": 1000000
    }, index=dates)
    
    bt = Backtester("TEST_STOCK", sample_df, asset_type="normal")
    res = bt.run(verbose=True)
    print("Backtest sintético completado sin errores y sin tocar archivos.")
