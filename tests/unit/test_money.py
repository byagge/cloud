from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import D, ceil_to_step, format_usd, money
from app.core.pricing import client_price


def test_float_forbidden():
    with pytest.raises(TypeError):
        D(1.5)


def test_client_price_example():
    # SOW: cost 9.60, markup 2.0, fee 0.02 → 19.60
    assert client_price(Decimal("9.60"), Decimal("2.0"), Decimal("0.02")) == Decimal("19.60")


def test_client_price_4():
    # cost 4.00, markup 2.0 → 8.20
    assert client_price(Decimal("4.00"), Decimal("2.0"), Decimal("0.02")) == Decimal("8.20")


def test_client_price_4_default_markup():
    # partner $4, markup 2.2, fee 2% → client $9.00
    assert client_price(Decimal("4.00"), Decimal("2.2"), Decimal("0.02")) == Decimal("9.00")


def test_ceil_to_step():
    assert ceil_to_step(Decimal("19.51"), Decimal("0.10")) == Decimal("19.60")


def test_format_usd():
    assert format_usd("12.4") == "$12.40"


def test_money_quantize():
    assert money("1.235") == Decimal("1.24")
