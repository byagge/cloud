from __future__ import annotations

from decimal import Decimal

from app.core.money import D, ceil_to_step, money


def client_price(
    partner_cost: Decimal,
    markup: Decimal,
    fee: Decimal,
    step: Decimal = Decimal("0.10"),
) -> Decimal:
    """price = ceil_to_step(cost * markup / (1 - fee))"""
    cost = D(partner_cost)
    m = D(markup)
    f = D(fee)
    if f >= 1:
        raise ValueError("fee must be < 1")
    raw = cost * m / (Decimal("1") - f)
    return money(ceil_to_step(raw, D(step)))
