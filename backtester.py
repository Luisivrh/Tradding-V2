"""
==============================================
BACKTESTER - BOT TRADING
==============================================
Simula la estrategia en datos históricos
para validar su desempeño.
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt

from config import BACKTEST_CONFIG, RISK_CONFIG
from risk_management import RiskManager, Position
from technical_analysis import SignalAnalyzer
from data_fetcher import DataFetcher, DataPreprocessor

logger = logging.getLogger(__name__)

# ==========================================
# BACKTESTER
# ==========================================

class Backtester:
    """Ejecuta backtests de la estrategia."""
    
    def __init__(self, symbol: str, df: pd.DataFrame):
        self.symbol = symbol
        self.df = df.copy()
        self.risk_manager = RiskManager(BACKTEST_CONFIG["initial_capital"])
        self.trades_log = []
        self.equity_curve = []
        self.equity_dates = []
    
    def run(self, verbose: bool = True) -> Dict:
        """
        Ejecuta el backtest.
        
        Returns:
            dict: Resultados del backtest
        """
        logger.info(f"⏳ Iniciando backtest de {self.symbol}...")
        
        # Iterar por cada vela
        for idx in range(len(self.df)):
            current_date = self.df.index[idx]
            
            # Solo procesar después de tener suficientes datos (200 días para SMA200)
            if idx < 200:
                continue
            
            # Obtener datos hasta este punto
            df_slice = self.df.iloc[:idx+1]
            
            # Analizar señal
            analyzer = SignalAnalyzer(df_slice, self.symbol)
            signal_type, score, details = analyzer.evaluate_signal()
            
            current_price = self.df.iloc[idx]['Close']
            
            # Procesar posiciones abiertas
            for symbol in list(self.risk_manager.positions.keys()):
                position = self.risk_manager.positions[symbol]
                should_exit, exit_reason = position.should_exit(current_price)
                
                if should_exit:
                    self.risk_manager.close_position(
                        symbol,
                        current_price,
                        reason=exit_reason,
                        exit_date=str(current_date)
                    )
                    self.trades_log.append({
                        "date": current_date,
                        "action": "EXIT",
                        "symbol": symbol,
                        "reason": exit_reason,
                        "price": current_price
                    })
            
            # Abrir nueva posición si hay señal
            if signal_type == "BUY" and self.symbol not in self.risk_manager.positions:
                if signal_type == "BUY" and score >= BACKTEST_CONFIG.get("min_score", 6.0):
                    position = self.risk_manager.open_position(
                        self.symbol,
                        current_price,
                        side="BUY",
                        entry_date=str(current_date)
                    )
                    
                    if position:
                        self.trades_log.append({
                            "date": current_date,
                            "action": "ENTRY",
                            "symbol": self.symbol,
                            "signal_score": score,
                            "price": current_price
                        })
            
            # Registrar equity
            portfolio_value, pnl = self.risk_manager.get_portfolio_value(
                {self.symbol: current_price}
            )
            self.equity_curve.append(portfolio_value)
            self.equity_dates.append(current_date)
        
        # Cerrar posiciones abiertas al final
        final_price = self.df.iloc[-1]['Close']
        for symbol in list(self.risk_manager.positions.keys()):
            self.risk_manager.close_position(
                symbol,
                final_price,
                reason="BACKTEST_END",
                exit_date=str(self.df.index[-1])
            )
        
        results = self._generate_report()
        
        if verbose:
            self._print_report(results)
        
        return results
    
    def _generate_report(self) -> Dict:
        """Genera reporte de resultados."""
        summary = self.risk_manager.get_summary()
        
        # Calcular métricas adicionales
        equity_series = pd.Series(self.equity_curve)
        
        # Sharpe Ratio (simplificado)
        returns = equity_series.pct_change()
        sharpe_ratio = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        
        # Profit Factor
        winning_total = sum(t["pnl"] for t in self.risk_manager.trade_history if t["pnl"] > 0)
        losing_total = abs(sum(t["pnl"] for t in self.risk_manager.trade_history if t["pnl"] < 0))
        profit_factor = winning_total / losing_total if losing_total > 0 else 0
        
        # Max drawdown
        equity_series_min = equity_series.cummin()
        drawdown_series = (equity_series - equity_series_min) / equity_series_min
        max_drawdown = drawdown_series.max() if len(drawdown_series) > 0 else 0
        
        final_equity = self.equity_curve[-1] if self.equity_curve else BACKTEST_CONFIG["initial_capital"]
        total_return = (final_equity - BACKTEST_CONFIG["initial_capital"]) / BACKTEST_CONFIG["initial_capital"] * 100
        
        return {
            "symbol": self.symbol,
            "total_trades": summary["total_trades"],
            "winning_trades": summary["winning_trades"],
            "losing_trades": summary["losing_trades"],
            "win_rate": summary["win_rate"],
            "profit_factor": profit_factor,
            "total_pnl": summary["total_pnl"],
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "avg_win": summary["avg_win"],
            "avg_loss": summary["avg_loss"],
            "initial_capital": BACKTEST_CONFIG["initial_capital"],
            "final_capital": final_equity,
            "start_date": str(self.df.index[0]),
            "end_date": str(self.df.index[-1]),
        }
    
    def _print_report(self, results: Dict):
        """Imprime un reporte formateado."""
        print("\n" + "="*70)
        print(f"📊 REPORTE DE BACKTEST: {results['symbol']}")
        print("="*70)
        print(f"Período:              {results['start_date']} → {results['end_date']}")
        print(f"Capital Inicial:      ${results['initial_capital']:,.2f}")
        print(f"Capital Final:        ${results['final_capital']:,.2f}")
        print(f"P&L Total:            ${results['total_pnl']:,.2f}")
        print(f"Retorno Total:        {results['total_return']:.2f}%")
        print("-"*70)
        print(f"Total Trades:         {results['total_trades']}")
        print(f"Trades Ganadores:     {results['winning_trades']}")
        print(f"Trades Perdedores:    {results['losing_trades']}")
        print(f"Win Rate:             {results['win_rate']:.1f}%")
        print(f"Profit Factor:        {results['profit_factor']:.2f}x")
        print("-"*70)
        print(f"Avg. Ganancia:        ${results['avg_win']:,.2f}")
        print(f"Avg. Pérdida:         ${results['avg_loss']:,.2f}")
        print(f"Max Drawdown:         {results['max_drawdown']*100:.2f}%")
        print(f"Sharpe Ratio:         {results['sharpe_ratio']:.2f}")
        print("="*70 + "\n")
    
    def plot_equity_curve(self, filename: str = "equity_curve.png"):
        """Plotea la curva de equity."""
        plt.figure(figsize=(14, 6))
        
        plt.plot(self.equity_dates, self.equity_curve, linewidth=2, label='Equity')
        plt.fill_between(self.equity_dates, self.equity_curve, alpha=0.3)
        
        plt.xlabel('Fecha')
        plt.ylabel('Valor del Portfolio ($)')
        plt.title(f'Curva de Equity - Backtest {self.symbol}')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        
        plt.savefig(filename, dpi=100)
        logger.info(f"Gráfico guardado en: {filename}")
        plt.close()


# ==========================================
# CORREDOR DE BACKTESTS
# ==========================================

class BacktestRunner:
    """Ejecuta backtests para múltiples activos."""
    
    def __init__(self):
        self.results = {}
        self.data_fetcher = DataFetcher()
        self.preprocessor = DataPreprocessor()
    
    def run_backtest(self, symbol: str, df: pd.DataFrame) -> Dict:
        """Ejecuta backtest para un símbolo."""
        
        # Validar datos
        if not self.preprocessor.validate_data(df, symbol):
            logger.error(f"Datos inválidos para {symbol}")
            return None
        
        # Filtrar por fecha
        start_date = pd.to_datetime(BACKTEST_CONFIG["start_date"])
        end_date = pd.to_datetime(BACKTEST_CONFIG["end_date"])
        
        df_filtered = df[(df.index >= start_date) & (df.index <= end_date)]
        
        if len(df_filtered) < 200:
            logger.error(f"Insuficientes datos para {symbol}")
            return None
        
        # Ejecutar backtest
        backtester = Backtester(symbol, df_filtered)
        results = backtester.run()
        
        self.results[symbol] = results
        
        return results
    
    def run_all_backtests(self, symbols: List[str]) -> Dict[str, Dict]:
        """Ejecuta backtests para todos los símbolos."""
        
        logger.info(f"🔄 Ejecutando backtests para {len(symbols)} símbolos...")
        
        # Descargar datos
        fetcher = DataFetcher()
        
        for symbol in symbols:
            logger.info(f"\n📈 Backtestando {symbol}...")
            
            df = fetcher.fetch_market_data(symbol)
            
            if not df.empty:
                self.run_backtest(symbol, df)
        
        return self.results
    
    def print_summary(self):
        """Imprime resumen de todos los backtests."""
        if not self.results:
            logger.warning("No hay resultados de backtest")
            return
        
        print("\n" + "="*70)
        print("📊 RESUMEN DE BACKTESTS")
        print("="*70)
        
        for symbol, results in self.results.items():
            if results:
                print(f"\n{symbol:8} | "
                      f"Return: {results['total_return']:>7.2f}% | "
                      f"Win Rate: {results['win_rate']:>5.1f}% | "
                      f"Trades: {results['total_trades']:>3} | "
                      f"Sharpe: {results['sharpe_ratio']:>5.2f}")
        
        print("="*70 + "\n")
    
    def get_best_performer(self) -> Tuple[str, Dict]:
        """Retorna el símbolo con mejor desempeño."""
        if not self.results:
            return None, None
        
        best_symbol = max(self.results.keys(), 
                         key=lambda k: self.results[k]['total_return'] if self.results[k] else -999)
        
        return best_symbol, self.results[best_symbol]


# ==========================================
# EJEMPLO DE USO
# ==========================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Ejemplo: Backtestear BTC
    runner = BacktestRunner()
    
    symbols_to_test = ["BTC-USD", "ETH-USD"]
    results = runner.run_all_backtests(symbols_to_test)
    
    runner.print_summary()
    
    best_symbol, best_results = runner.get_best_performer()
    if best_results:
        print(f"\n🏆 Mejor desempeño: {best_symbol}")
