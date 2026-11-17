"""
Utilidades de manejo y parseo de fechas para el bot de trading.
Asegura compatibilidad entre versiones de Python (< 3.11 y >= 3.11),
soporte para sufijo 'Z', offsets UTC y normalización a datetimes locales/naive
para evitar ValueErrors y TypeErrors en comparaciones y restas con datetime.now().
"""

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def parse_iso_datetime(val: Any, default: Optional[datetime] = None) -> datetime:
    """
    Parsea una fecha/hora en formato ISO-8601 o estándar de base de datos / broker.

    Características clave:
    - Soporta sufijos 'Z' y 'z' en Python < 3.11 (ej. '2026-10-05T13:34:23.883409Z').
    - Soporta cadenas con offsets '+HH:MM' y '-HH:MM'.
    - Soporta cadenas con separador 'T' o espacio (' ').
    - Soporta solo fecha 'YYYY-MM-DD'.
    - Si el datetime resultante tiene zona horaria (tz-aware, ej. UTC desde Alpaca),
      se convierte a la hora local del sistema y se convierte en naive (tzinfo=None)
      para permitir restas y comparaciones seguras con `datetime.now()`.
    - Si val ya es una instancia de `datetime`, se normaliza de la misma manera.
    - Si val es None, vacío o inválido y se proporciona `default`, devuelve `default`.
    """
    if val is None:
        if default is not None:
            return default
        raise ValueError("El valor de fecha es None")

    if isinstance(val, datetime):
        dt = val
    else:
        s = str(val).strip()
        if not s:
            if default is not None:
                return default
            raise ValueError("Cadena de fecha vacía")

        # Python < 3.11 no soporta sufijo 'Z' en fromisoformat()
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                # Intentar formato solo fecha YYYY-MM-DD
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            except Exception:
                try:
                    import pandas as pd
                    ts = pd.to_datetime(s)
                    dt = ts.to_pydatetime()
                except Exception as err:
                    if default is not None:
                        logger.warning("No se pudo parsear fecha '%s', usando default: %s", val, err)
                        return default
                    raise ValueError(f"Formato de fecha inválido: '{val}'") from err

    # Normalizar tz-aware a hora local naive para que sea compatible con datetime.now()
    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone().replace(tzinfo=None)
        except Exception:
            dt = dt.replace(tzinfo=None)

    return dt
