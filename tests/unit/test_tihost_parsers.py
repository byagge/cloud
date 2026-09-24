from decimal import Decimal

from app.partner.tihost.parsers import parse_plans, parse_server


def test_parse_plans_tihost_periods_total_usd():
    data = {
        "location": "germany",
        "items": [
            {
                "id": 41,
                "cpu": 2,
                "ram_mb": 4096,
                "disk_gb": 60,
                "periods": [
                    {"months": 1, "total_usd": "4.00", "discount_percent": 0},
                    {"months": 3, "total_usd": "10.80", "discount_percent": 10},
                    {"months": 6, "total_usd": "20.40", "discount_percent": 15},
                    {"months": 12, "total_usd": "38.40", "discount_percent": 20},
                ],
            }
        ],
    }
    plans = parse_plans(data)
    assert len(plans) == 1
    p = plans[0]
    assert p.id == "41"
    assert p.prices[1] == Decimal("4.00")
    assert p.prices[3] == Decimal("10.80")
    assert p.prices[12] == Decimal("38.40")


def test_parse_server_renew_prices_array():
    data = {
        "id": 2427,
        "name": "web-01",
        "os": "Ubuntu 24.04",
        "ip": "1.2.3.4",
        "state": "active",
        "location": "germany",
        "resources": {"cpu": 2, "ram_mb": 4096, "disk_gb": 60},
        "renew_prices": [
            {"days": 30, "total_usd": "4.00"},
            {"days": 90, "total_usd": "10.80"},
        ],
    }
    s = parse_server(data)
    assert s.cpu == 2
    assert s.ram_mb == 4096
    assert s.renew_prices[30] == Decimal("4.00")
    assert s.renew_prices[90] == Decimal("10.80")
