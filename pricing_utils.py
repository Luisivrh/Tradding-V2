"""
Utilidades de formateo y validación de precios para el motor de trading.
Asegura el cumplimiento estricto de la regla SEC 612 (Sub-Penny Rule) y de Alpaca.
"""

from decimal import Decimal, ROUND_HALF_UP


def round_order_price(price: float, is_crypto: bool = False) -> float:
    """
    Formatea el precio límite según la regla SEC 612 (Sub-Penny) y requisitos de Alpaca:
    - Cripto: 4 decimales.
    - Acciones con precio >= $1.00: 2 decimales ($0.01).
    - Acciones con precio < $1.00: 4 decimales ($0.0001).
    Usa Decimal(str(price)) para evitar artefactos de punto flotante binario.
    """
    if price is None or price <= 0:
        return 0.0
    d = Decimal(str(price))
    if is_crypto:
        return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    if d >= Decimal("1.00"):
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    else:
        return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def resolve_exit_price(
    reason: str,
    execution_price: float,
    quote: dict,
    opp_current_price: float,
    last_known_price: float
) -> float:
    """
    Resuelve el precio de salida usando una cadena estricta de respaldo:
    1. Si es STOP: cotización 'bid' en tiempo real (si bid > 0). Si es venta técnica: execution_price.
    2. Fallback 1: 'price' de quote o opp_current_price si bid no está disponible (ej. mercado cerrado).
    3. Fallback 2: last_known_price de la posición.
    4. Si todos son <= 0: devuelve 0.0 (indicando que NO se debe liquidar a cero).
    """
    candidate_price = quote.get("bid", 0.0) if (reason and reason.startswith("STOP")) else execution_price
    if candidate_price is None or candidate_price <= 0:
        candidate_price = quote.get("price", 0.0) or opp_current_price
    if candidate_price is None or candidate_price <= 0:
        candidate_price = last_known_price
        
    return max(0.0, float(candidate_price or 0.0))
