from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN


def decimals_from_step(step_value: str | float | int) -> int:
    normalized = str(step_value).rstrip("0").rstrip(".")
    if "." not in normalized:
        return 0
    return len(normalized.split(".")[1])


def step_from_decimals(decimals: int) -> Decimal:
    return Decimal("1").scaleb(-decimals) if decimals > 0 else Decimal("1")


def parse_step(step_value, fallback_decimals: int = 0) -> Decimal:
    try:
        step = Decimal(str(step_value))
    except (InvalidOperation, TypeError, ValueError):
        return step_from_decimals(fallback_decimals)
    if step <= 0:
        return step_from_decimals(fallback_decimals)
    return step


def floor_to_step(value: float, step: Decimal) -> float:
    dec_value = Decimal(str(value))
    if step <= 0:
        return float(dec_value)
    units = (dec_value / step).to_integral_value(rounding=ROUND_DOWN)
    return float(units * step)
