"""
======================================================
RECONSTRUCCIÓN DE HISTORIAL DESDE ACTIVIDADES FILL (SSOT)
======================================================
Reconstruye portfolio.json y audit_log.json directamente
desde el endpoint REST oficial de Alpaca:
GET /v2/account/activities/FILL

Características:
- Paginación exhaustiva (page_token).
- Cálculo FIFO estricto de PnL realizado.
- Reconciliación de posiciones vivas remanentes.
- Respaldo automático con timestamp antes de sobrescribir.
"""

import os
import json
import logging
import requests
from datetime import datetime
from collections import deque
from typing import Dict, List, Tuple

from config import BROKERS_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def fetch_all_fill_activities(api_key: str, secret_key: str, paper: bool = True) -> List[Dict]:
    """Descarga todas las actividades FILL de Alpaca usando la API REST con paginación."""
    base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    url = f"{base_url}/v2/account/activities/FILL"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key
    }
    
    all_fills = []
    page_token = None
    
    logger.info(f"Conectando a Alpaca REST: {url} ...")
    
    while True:
        params = {"direction": "asc", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
            
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Error consultando actividades en Alpaca: {resp.status_code} - {resp.text}")
            break
            
        activities = resp.json()
        if not activities:
            break
            
        all_fills.extend(activities)
        logger.info(f"  Descargadas {len(activities)} actividades FILL (Total acumulado: {len(all_fills)})")
        
        # Paginación
        page_token = activities[-1].get("id")
        if len(activities) < 100:
            break
            
    return all_fills


def process_fills_fifo(fills: List[Dict]) -> Tuple[List[Dict], List[Dict], Dict]:
    """
    Procesa las actividades FILL en orden cronológico usando FIFO para calcular PnL exacto.
    Devuelve: (trade_history, audit_log, open_positions_dict).
    """
    # Ordenar por transaction_time ascendente
    sorted_fills = sorted(fills, key=lambda x: x.get("transaction_time", ""))
    
    open_lots: Dict[str, deque] = {} # symbol -> deque of {"qty": float, "price": float, "date": str, "order_id": str}
    trade_history = []
    audit_log = []
    
    for fill in sorted_fills:
        symbol = fill.get("symbol", "").replace("/", "-")
        side = fill.get("side", "").lower() # buy or sell
        qty = float(fill.get("qty", 0.0))
        price = float(fill.get("price", 0.0))
        tx_time = fill.get("transaction_time", "")
        order_id = fill.get("order_id", "")
        
        if not symbol or qty <= 0 or price <= 0:
            continue
            
        if side == "buy":
            open_lots.setdefault(symbol, deque()).append({
                "qty": qty,
                "price": price,
                "date": tx_time,
                "order_id": order_id
            })
        elif side == "sell":
            remaining_to_sell = qty
            pnl_total = 0.0
            cost_basis_total = 0.0
            matched_entry_prices = []
            earliest_entry_date = tx_time
            
            lots = open_lots.get(symbol, deque())
            
            while remaining_to_sell > 0 and lots:
                oldest_lot = lots[0]
                earliest_entry_date = oldest_lot["date"]
                matched_qty = min(remaining_to_sell, oldest_lot["qty"])
                
                cost = matched_qty * oldest_lot["price"]
                proceeds = matched_qty * price
                lot_pnl = proceeds - cost
                
                pnl_total += lot_pnl
                cost_basis_total += cost
                matched_entry_prices.append((oldest_lot["price"], matched_qty))
                
                oldest_lot["qty"] -= matched_qty
                remaining_to_sell -= matched_qty
                
                if oldest_lot["qty"] <= 1e-6:
                    lots.popleft()
                    
            avg_entry_price = (cost_basis_total / qty) if qty > 0 else price
            
            trade_record = {
                "symbol": symbol,
                "entry_price": avg_entry_price,
                "exit_price": price,
                "quantity": qty,
                "pnl": pnl_total,
                "reason": "ALPACA_FILL",
                "date": tx_time,
                "entry_date": earliest_entry_date,
                "alpaca_order_id": order_id
            }
            trade_history.append(trade_record)
            
            audit_record = {
                "symbol": symbol,
                "asset_type": "normal",
                "entry_price": avg_entry_price,
                "exit_price": price,
                "quantity": qty,
                "profit_usd": pnl_total,
                "profit_pct": (pnl_total / cost_basis_total) if cost_basis_total > 0 else 0.0,
                "reason": "ALPACA_FILL",
                "holding_days": None,
                "entry_atr": None,
                "exit_atr": None,
                "entry_spread": None,
                "exit_spread": None,
                "score": None,
                "mfe": None,
                "mae": None,
                "price_after_1d": None,
                "missed_profit_1d": None,
                "price_after_3d": None,
                "missed_profit_3d": None,
                "price_after_5d": None,
                "missed_profit_5d": None,
                "price_after_10d": None,
                "missed_profit_10d": None,
                "exit_quality_score": "Pendiente",
                "entry_date": earliest_entry_date,
                "exit_date": tx_time,
                "alpaca_order_id": order_id
            }
            audit_log.append(audit_record)
            
    # Posiciones abiertas remanentes
    reconstructed_positions = {}
    for sym, lots in open_lots.items():
        total_remaining = sum(l["qty"] for l in lots if l["qty"] > 1e-6)
        if total_remaining > 1e-4:
            total_cost = sum(l["qty"] * l["price"] for l in lots if l["qty"] > 1e-6)
            avg_p = total_cost / total_remaining
            reconstructed_positions[sym] = [{
                "symbol": sym,
                "asset_type": "normal",
                "entry_price": avg_p,
                "entry_date": lots[0]["date"],
                "side": "BUY",
                "quantity": total_remaining,
                "stop_loss": avg_p * 0.95,
                "take_profit": avg_p * 1.10,
                "atr_at_entry": avg_p * 0.05,
                "spread_at_entry": 0.001,
                "highest_price": avg_p,
                "lowest_price": avg_p,
                "last_known_price": avg_p
            }]
            
    return trade_history, audit_log, reconstructed_positions


def rebuild(apply_changes: bool = False):
    """Ejecuta la reconstrucción y opcionalmente aplica sobre los archivos de producción."""
    api_key = BROKERS_CONFIG["alpaca"]["api_key"]
    secret_key = BROKERS_CONFIG["alpaca"]["secret_key"]
    paper = BROKERS_CONFIG["alpaca"]["paper"]
    
    if not api_key or not secret_key:
        logger.error("Credenciales de Alpaca no encontradas en BROKERS_CONFIG.")
        return
        
    fills = fetch_all_fill_activities(api_key, secret_key, paper)
    logger.info(f"Total actividades FILL obtenidas: {len(fills)}")
    
    if not fills:
        logger.warning("No se encontraron actividades FILL en la cuenta de Alpaca.")
        return
        
    trades, audit, positions = process_fills_fifo(fills)
    
    logger.info(f"Resultado de Reconstrucción FIFO:")
    logger.info(f"  • Total Trades Cerrados Históricos: {len(trades)}")
    logger.info(f"  • PnL Neto Histórico Realizado: ${sum(t['pnl'] for t in trades):,.2f}")
    logger.info(f"  • Posiciones Abiertas Conciliadas: {list(positions.keys())}")
    
    # Crear respaldos y guardar
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if apply_changes:
        if os.path.exists("portfolio.json"):
            os.rename("portfolio.json", f"portfolio.json.bak_{ts}")
            logger.info(f"Respaldo creado: portfolio.json.bak_{ts}")
        if os.path.exists("audit_log.json"):
            os.rename("audit_log.json", f"audit_log.json.bak_{ts}")
            logger.info(f"Respaldo creado: audit_log.json.bak_{ts}")
            
        with open("portfolio.json", "w", encoding="utf-8") as f:
            json.dump({
                "starting_capital": 100000.0,
                "current_capital": 100000.0 + sum(t['pnl'] for t in trades),
                "forex_starting_capital": 10000.0,
                "forex_capital": 10000.0,
                "positions": positions,
                "trade_history": trades
            }, f, indent=4)
            
        with open("audit_log.json", "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=4)
            
        logger.info("✅ Archivos de producción portfolio.json y audit_log.json actualizados con datos reales.")
    else:
        # Guardar en archivos .rebuilt para inspección sin alterar producción
        with open("portfolio_rebuilt.json", "w", encoding="utf-8") as f:
            json.dump({
                "starting_capital": 100000.0,
                "current_capital": 100000.0 + sum(t['pnl'] for t in trades),
                "positions": positions,
                "trade_history": trades
            }, f, indent=4)
        with open("audit_log_rebuilt.json", "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=4)
        logger.info("ℹ️ Archivos de prueba guardados: portfolio_rebuilt.json y audit_log_rebuilt.json (Usa --apply para sobrescribir)")


if __name__ == "__main__":
    import sys
    apply_mode = "--apply" in sys.argv
    rebuild(apply_changes=apply_mode)
