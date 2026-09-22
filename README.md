# ARIX Cloud Bot

Telegram-панель ARIX Cloud для покупки и управления VPS (реселл Tihost).  
UI как у Outreach: premium emoji Translucent Pack, HTML-экраны, inline-кнопки с `icon_custom_emoji_id`.

## Что умеет

- Каталог DE / FI / PL, тарифы, ОС, сроки 1–12 мес, наценка ARIX
- Баланс USD (Decimal + леджер), пополнение через fake/крипто-шлюз
- Покупка с баланса → очередь provision → доступы (один раз, удаление через 10 мин)
- Серверы: питание, продление, автопродление, пароль, переустановка, скрипты, отключить
- Админка: сводка, клиенты, серверы, заказы, финансы, настройки, аудит (`/admin`)
- Job-диспетчер с лимитом записи 9/мин, FakePartner для разработки без Tihost

## Быстрый старт (без Docker)

```bash
cp .env.example .env
# BOT_TOKEN=... от @BotFather
# OWNER_TG_ID=ваш telegram id

python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

python -m scripts.seed_dev   # +$100 owner (idempotent)
python -m app.bot.main       # бот + embedded worker + /health :8080
```

По умолчанию: SQLite (`data/arix.db`), `REDIS_URL=memory://`, `EMBEDDED_WORKER=true`, `USE_FAKE_PARTNER=true`.

В чате: `/start` → принять оферту → **Пополнить** → «Проверить оплату» (в dev fake сразу зачисляет) → **Купить сервер**.

## Production (без Docker)

См. **[DEPLOY.md](DEPLOY.md)** — venv + systemd, SQLite в `/opt/arix/data`, порт `127.0.0.1:18080`.

```bash
cd /opt/arix
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.production.example .env   # заполнить секреты
mkdir -p data
sudo cp deploy/systemd/arix-bot.service /etc/systemd/system/
sudo systemctl enable --now arix-bot
curl -sS http://127.0.0.1:18080/health
```

## Тесты

```bash
pytest -q
```

## Документы

- Деплой: `DEPLOY.md`
- Полное ТЗ: `sow.md` / `docs/TZ.md`
- Правила агента: `.cursor/rules/arix.mdc`
