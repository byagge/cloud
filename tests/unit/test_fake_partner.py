from __future__ import annotations

from decimal import Decimal

import pytest

from app.partner.fake import FakePartner


@pytest.mark.asyncio
async def test_fake_catalog_and_create():
    p = FakePartner(starting_balance=Decimal("1000"))
    plans = await p.plans("germany")
    assert plans
    assert plans[0].prices[1] > 0
    os_list = await p.os_list("germany", plans[0].id)
    assert os_list
    created = await p.create_server(
        location="germany",
        plan_id=plans[0].id,
        os_id=os_list[0].id,
        months=1,
        name="arix-o1",
        idem_key="order-1",
    )
    assert created.password
    again = await p.create_server(
        location="germany",
        plan_id=plans[0].id,
        os_id=os_list[0].id,
        months=1,
        name="arix-o1",
        idem_key="order-1",
    )
    assert again.id == created.id
    servers = await p.list_servers()
    assert len(servers) == 1
