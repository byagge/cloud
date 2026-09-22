from __future__ import annotations

from aiogram import Dispatcher

from app.bot.routers import admin, balance, buy, profile, servers, start


def setup_routers(dp: Dispatcher) -> None:
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(buy.router)
    dp.include_router(balance.router)
    dp.include_router(servers.router)
    dp.include_router(admin.router)
