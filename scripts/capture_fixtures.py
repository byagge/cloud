from __future__ import annotations

"""Capture partner fixtures (read-only). Requires PARTNER_* and USE_FAKE_PARTNER=false."""

import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.partner.tihost.client import TihostClient
from app.db.session import get_session_factory

OUT = Path("tests/fixtures/partner")


async def main() -> None:
    settings = get_settings()
    if settings.use_fake_partner:
        raise SystemExit("Set USE_FAKE_PARTNER=false and real PARTNER_BASE_URL")
    OUT.mkdir(parents=True, exist_ok=True)
    client = TihostClient(settings, get_session_factory())
    try:
        ping = await client.ping()
        (OUT / "ping.json").write_text(ping.model_dump_json(indent=2), encoding="utf-8")
        bal = await client.balance()
        (OUT / "balance.json").write_text(json.dumps({"balance_usd": str(bal)}, indent=2), encoding="utf-8")
        for loc in ("germany", "finland", "poland"):
            plans = await client.plans(loc)
            (OUT / f"catalog_plans_{loc}.json").write_text(
                json.dumps([p.model_dump(mode="json") for p in plans], indent=2, default=str),
                encoding="utf-8",
            )
        servers = await client.list_servers()
        (OUT / "servers_list.json").write_text(
            json.dumps([s.model_dump(mode="json") for s in servers], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Wrote fixtures to {OUT}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
