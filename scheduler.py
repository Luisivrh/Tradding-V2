"""
==============================================
PLANIFICADOR DEL MODO AUTO
==============================================
El cron ejecuta `python main.py --mode auto` cada 15 minutos (con flock para evitar traslapes).
Este módulo decide qué tarea toca en cada corrida, según el reloj de Alpaca y la hora:

- stocks_scan:   escaneo completo de acciones (screener + señales + compras), 1 vez al día
                 a partir de `stocks_scan_time` (hora NY), con el mercado abierto y con margen
                 antes del cierre. La vela diaria está casi completa, igual que en el backtest.
- daily_24h_scan: escaneo de cripto (y forex de lunes a viernes) 1 vez al día después del cierre
                 de la vela diaria UTC.
- monitor_stocks: en el resto de corridas con mercado abierto, solo se revisan salidas.
- monitor_crypto: cripto opera 24/7; sus salidas se revisan en cada corrida.

La decisión es una función pura (decide_tasks) para poder probarla sin red ni broker.
"""

import json
import logging
import os
from datetime import datetime, time as dtime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from config import SCHEDULE_CONFIG

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


def decide_tasks(now_utc: datetime, market_open: bool, next_close: Optional[datetime], state: Dict) -> Dict:
    """
    now_utc: datetime con zona horaria (UTC).
    market_open / next_close: del reloj de Alpaca (get_clock).
    state: {'stocks_scan_date': 'YYYY-MM-DD' (NY), 'daily_24h_scan_date': 'YYYY-MM-DD' (UTC)}
    """
    tz_ny = ZoneInfo(SCHEDULE_CONFIG.get("timezone", "America/New_York"))
    now_ny = now_utc.astimezone(tz_ny)
    today_ny = now_ny.date().isoformat()
    today_utc = now_utc.date().isoformat()

    scan_time = _parse_hhmm(SCHEDULE_CONFIG.get("stocks_scan_time", "15:40"))
    min_before_close = SCHEDULE_CONFIG.get("min_minutes_before_close", 5)
    minutes_to_close = None
    if next_close is not None:
        minutes_to_close = (next_close - now_utc).total_seconds() / 60.0

    stocks_scan = (
        market_open
        and now_ny.time() >= scan_time
        and state.get("stocks_scan_date") != today_ny
        and (minutes_to_close is None or minutes_to_close >= min_before_close)
    )

    daily_time = _parse_hhmm(SCHEDULE_CONFIG.get("daily_24h_scan_time_utc", "00:05"))
    daily_24h_scan = now_utc.time() >= daily_time and state.get("daily_24h_scan_date") != today_utc
    # Forex (simulado) no cotiza en fin de semana
    include_forex = now_utc.weekday() < 5

    return {
        "stocks_scan": stocks_scan,
        "daily_24h_scan": daily_24h_scan,
        "include_forex": include_forex,
        "monitor_stocks": market_open and not stocks_scan,
        "monitor_crypto": not daily_24h_scan,
        "today_ny": today_ny,
        "today_utc": today_utc,
    }


def load_state(path: Optional[str] = None) -> Dict:
    path = path or SCHEDULE_CONFIG.get("state_file", "scheduler_state.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error leyendo {path}: {e}")
    return {}


def save_state(state: Dict, path: Optional[str] = None):
    path = path or SCHEDULE_CONFIG.get("state_file", "scheduler_state.json")
    try:
        state = dict(state, updated_at=datetime.now(timezone.utc).isoformat())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Error guardando {path}: {e}")
