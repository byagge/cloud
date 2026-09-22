from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

TWOPLACES = Decimal("0.01")
STEP_DEFAULT = Decimal("0.10")


def D(value: object) -> Decimal:
    """Parse money from str/int/Decimal. Reject float."""
    if isinstance(value, float):
        raise TypeError("float is forbidden for money; use str or Decimal")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: object) -> Decimal:
    """Normalize to 2 decimal places (HALF_UP for display amounts)."""
    return D(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def format_usd(value: object) -> str:
    return f"${money(value)}"


def ceil_to_step(value: Decimal, step: Decimal = STEP_DEFAULT) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step
