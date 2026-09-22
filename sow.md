# ТЗ на разработку: ARIX Cloud Bot

Telegram-бот-панель для перепродажи VPS через API партнёра Tihost.
Версия 1.0 · 21.09.2026 · Заказчик: Muhammad Aziz (ARIX) · Исполнитель: разработчик с Cursor.

Документ самодостаточный: по нему можно писать код без дополнительных вопросов. Всё, что неизвестно про API партнёра, помечено `ASSUMPTION` или `OPEN`. Такие места нельзя «додумывать», их проверяют по реальным ответам API (раздел 2.6 и 15).

---

## 0. Как работать с этим ТЗ в Cursor

### 0.1 Правила для агента (положить в `.cursor/rules/arix.mdc` как есть)

```text
Проект: ARIX Cloud Bot (Python 3.12, aiogram 3, PostgreSQL, Redis).
Источник требований: docs/TZ.md. Если код расходится с ТЗ, прав ТЗ.

Жёсткие правила:
1. Деньги только Decimal. float для денег запрещён. Округление явно (ROUND_CEILING для цен).
2. Партнёрский API вызывается ТОЛЬКО через PartnerAdapter (app/partner). Прямой httpx к партнёру в других местах запрещён.
3. Любая запись к партнёру (POST/PATCH) идёт ТОЛЬКО через job-диспетчер (app/jobs/dispatcher.py) и учитывается в лимите записи.
4. Любое изменение баланса только через app/core/ledger.py, в одной транзакции с проводкой.
5. Пароли серверов, API-ключи, токены не попадают в логи, БД, jobs.result, аудит, тексты ошибок.
6. Имена полей партнёрского API не выдумывать. Неизвестное читать через .get(), помечать `# ASSUMPTION`, покрывать contract-тестом с фикстурой.
7. Каждый модуль с логикой идёт вместе с тестами (pytest). Без тестов задача не закрыта.
8. Работай по этапам M0..M8 из раздела 16. Этап Phase 2 (деплой, ИИ-агент) не трогать.
9. Проверка перед коммитом: ruff check, ruff format --check, mypy app/core app/partner app/payments, pytest.
10. Тексты бота только через app/bot/texts (ключи), не строками в хендлерах.
```

### 0.2 Порядок для агента

1. Прочитать разделы 1–5 целиком, создать каркас (раздел 3.3) и миграцию (раздел 5).
2. Дальше идти по этапам раздела 16. Один этап = один PR/коммит-серия, в конце этапа зелёные тесты.
3. Перед началом M1 выполнить `scripts/capture_fixtures.py` (раздел 2.6), иначе адаптер строится на догадках.

### 0.3 Определение «готово» для любой задачи

- Код типизирован, тесты зелёные, линтеры чистые.
- Нет секретов в логах (проверено тестом на редактирование).
- Поведение на ошибках описано в ТЗ и покрыто тестом.

---

## 1. Цель и границы

### 1.1 Цель

Клиент в Telegram покупает VPS, оплачивает крипто-шлюзом, получает доступы, управляет сервером, продлевает аренду. Владелец (ARIX) администрирует всё в том же боте. Серверы физически создаёт партнёр Tihost по своему API.

### 1.2 В объёме (MVP, этапы M0–M8)

- Каталог (локации, тарифы, ОС, сроки) из API партнёра, наценка ARIX.
- Внутренний баланс клиента в USD, пополнение через крипто-шлюз.
- Покупка сервера с баланса, выдача доступов.
- Мои серверы: список, карточка, питание (start/stop/restart), продление, автопродление (наше), смена пароля, переименование (локально), переустановка ОС, запуск скриптов партнёра, «отключить» (выключить автопродление).
- Уведомления клиенту и админу.
- Админка в боте: сводка, клиенты, серверы, заказы, партнёр, abuse, рассылка, аудит, настройки.
- Очередь задач с лимитером записи 10/мин, идемпотентность, сверка с партнёром.
- Логи, алерты, бэкап БД.

### 1.3 Вне объёма (Phase 2, не делать в MVP)

- Авто-рост тарифа (нет resize и метрик в API партнёра).
- Деплой репо + домен, ИИ-агент деплоя, домены и SSL.
- Веб-панель.
- Удаление сервера у партнёра (метода нет в документации).
- Снапшоты, веб-консоль, бэкапы клиентских серверов.
- Второй партнёр (только заложить интерфейс).

### 1.4 Роли

| Роль | Кто | Права |
|---|---|---|
| client | любой пользователь Telegram | свои серверы, баланс, заказы |
| support | админ уровня 1 | просмотр клиентов, сообщения клиентам |
| operator | админ уровня 2 | support + серверы, заказы, заморозка |
| owner | владелец | всё: деньги, настройки, список админов |

Роли админов хранятся в таблице `admins`, первый owner берётся из `OWNER_TG_ID`.

---

## 2. Партнёр: Tihost API

Источник: tihost.io/api и tihost.io (сняты 21.09.2026 выжимкой). Перед кодом сверить с оригиналом и реальными ответами.

### 2.1 Общее

- Формат JSON, UTF-8. Авторизация `Authorization: Bearer <API_KEY>`.
- Базовый адрес в документации: `https://tihost.io/api/public/v1`. **OPEN-1:** это localhost по HTTP, боевой HTTPS-адрес узнать у партнёра. Адрес берётся только из `PARTNER_BASE_URL`. Схема `http://` для хостов, кроме localhost, запрещена: клиент при старте падает с ошибкой.
- Заголовок `Idempotency-Key` (до 128 символов) для записей. Тот же ключ и тело в течение 24 часов возвращают прошлый ответ с заголовком `Idempotent-Replay: true`. Тот же ключ с другим телом даёт `422 idempotency_key_reused`.
- Лимиты: чтение 60 запросов/мин, запись 10 запросов/мин, окна независимые. Заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, при 429 ещё `Retry-After`.
- Ошибка: `{"error": {"code": str, "message": str, "details": obj?, "request_id": str}}`, заголовок `X-Request-Id` совпадает.
- Локации: `germany`, `finland`, `poland`. Цены одинаковые. Периоды покупки: 1, 3, 6, 12 месяцев (скидки до 20%).
- Состояния сервера: `active`, `running`, `stopped`, `creating`, `installing`, `migrating`, `restarting`, `error`, `null`.

### 2.2 Методы

| Метод | Путь | Запрос | Ответ (по документации) | Тип |
|---|---|---|---|---|
| GET | `/ping` | — | `account_id`, `server_time` | read |
| GET | `/balance` | — | `balance_usd` (строка), `currency` = USD | read |
| GET | `/catalog/plans?location=` | location | тарифы с ценами по периодам | read |
| GET | `/catalog/os?location=&plan_id=` | location, plan_id | совместимые ОС | read |
| POST | `/servers` | `location`, `plan_id`, `os_id`, `months`, `name?` | `id`, `name`, `os`, `location`, `login`, `password` (один раз), `rent_expires_at`, `charged_usd`, `balance_usd` | write |
| GET | `/servers` | — | список серверов | read |
| GET | `/servers/{id}` | — | состояние, IP, `cpu`, `ram_mb`, `disk_gb`, цены продления | read |
| POST | `/servers/{id}/renew` | `days` ∈ {2,7,30,90,180,365} | `rent_expires_at`, `charged_usd`, `balance_usd` | write |
| PATCH | `/servers/{id}/auto-renew` | `enabled: bool` | — | write |
| POST | `/servers/{id}/power` | `action` ∈ {start,stop,restart} | — | write |
| PATCH | `/servers/{id}/name` | `name` 2–49 символов | — | write |
| POST | `/servers/{id}/password` | `password?` или null (генерация) | новый пароль (**ASSUMPTION**) | write |
| GET | `/servers/{id}/os` | — | ОС для переустановки | read |
| POST | `/servers/{id}/reinstall` | `os_id`, `confirm: true` | — | write |
| GET | `/servers/{id}/scripts` | — | список скриптов | read |
| POST | `/servers/{id}/scripts/run` | `script_id` | — | write |

### 2.3 Коды ошибок

| Код | HTTP | Значение | Реакция (раздел 7.5) |
|---|---|---|---|
| `invalid_request` | 400 | валидация | не повторять |
| `unauthorized` | 401 | нет/неверный заголовок | критично |
| `token_invalid` | 401 | ключ не найден или отозван | критично |
| `insufficient_funds` | 402 | мало денег, `details.missing_usd` | ждать пополнения |
| `forbidden` | 403 | доступ запрещён | критично |
| `account_banned` | 403 | аккаунт заблокирован | критично |
| `not_found` | 404 | объекта нет или он чужой | `missing` |
| `conflict` | 409 | действие невозможно в состоянии | обновить состояние |
| `server_busy` | 409 | машина занята операцией | повторить позже |
| `rent_expired` | 409 | аренда истекла | пометить expired |
| `idempotency_key_reused` | 422 | тот же ключ, другое тело | баг, алерт |
| `rate_limited` | 429 | превышена квота | пауза `Retry-After` |
| `internal_error` | 500 | сбой | повтор с backoff |
| `service_unavailable` | 503 | временно недоступно | повтор с backoff |

### 2.4 Чего в API нет (и что из этого следует)

| Нет | Следствие в коде |
|---|---|
| Удаление сервера | «Отключить» = выключить наше автопродление. Сервер живёт до конца срока |
| Изменение ресурсов | Апгрейд только новой покупкой и ручным переносом. В коде нет resize |
| Метрики | Графиков и авто-роста нет. Интерфейс метрик не создавать |
| Вебхуки | Изменения состояния узнаём опросом `GET /servers` |
| Снапшоты, консоль | Кнопок нет |
| Выделенный IPv4, rDNS | В MVP не продаётся. **OPEN-7** |
| Песочница | Тесты интеграции с реальным API вручную и только на самом дешёвом тарифе |

### 2.5 Открытые вопросы к партнёру

| ID | Вопрос | Блокирует код? | Как ведёт себя код пока нет ответа |
|---|---|---|---|
| OPEN-1 | Боевой HTTPS-адрес API | **да** | `PARTNER_BASE_URL` обязателен, без него старт невозможен |
| OPEN-2 | Форма ответа `POST /servers/{id}/password` | нет | читать `password` из ответа, если нет, искать в теле любое поле-строку длиной ≥ 8 под ключами `password`, `new_password`; иначе job в `needs_review` |
| OPEN-3 | Форма `renewal prices` в `GET /servers/{id}` | **да для M5** | парсер в `partner/tihost/parsers.py`, фикстура обязательна, fallback: цена продления = цена месячного тарифа × days / 30 с флагом `estimated=true` и предупреждением админу |
| OPEN-4 | Ответ `POST /servers/{id}/reinstall` (новый пароль?) | нет | после установки всегда делать `reset_password` и отправлять его |
| OPEN-5 | Можно ли `renew` после `rent_expired` | нет | пробуем, при `rent_expired` пишем клиенту «Срок истёк, напишите в поддержку» |
| OPEN-6 | Через сколько удаляются данные после конца аренды | нет | в оферте формулировка «по правилам партнёра»; в боте на карточке истёкшего сервера предупреждение |
| OPEN-7 | Персональный IPv4 у каждого сервера? Дополнительный IP? | нет | не обещать выделенный IP |
| OPEN-8 | Лимит 10 записей/мин можно поднять? | нет | считаем 10, рабочий лимит 9 (`PARTNER_WRITE_LIMIT`) |
| OPEN-9 | Поле IP в `POST /servers`? | нет | в ответе покупки IP нет (по документации), берём из `GET /servers/{id}` |
| OPEN-10 | Состав скриптов `/scripts` | нет | раздел «Скрипты» скрывается, если список пуст |

### 2.6 Фикстуры и контракт

Создать `scripts/capture_fixtures.py`, который безопасно снимает реальные ответы и кладёт в `tests/fixtures/partner/`:

- read-only: `ping`, `balance`, `catalog_plans_{location}`, `catalog_os`, `servers_list`, `server_detail`, `servers_os`, `servers_scripts`;
- write-ответы (покупка, renew, password) снимаются вручную один раз на самом дешёвом тарифе; скрипт умеет принять JSON из stdin и санитизировать: заменить `password`, `login`, IP, `id` на плейсхолдеры.

Contract-тесты (`tests/contract/test_partner_contract.py`) парсят фикстуры адаптером и проверяют все обязательные поля. Если фикстур нет, тесты помечаются `xfail` с причиной «capture fixtures first».

---

## 3. Архитектура

### 3.1 Схема

```text
Telegram ─► bot (aiogram, aiohttp web) ──► PostgreSQL
Gateway  ─► bot (webhook оплаты)      ──► Redis (FSM, кэш, замки, одноразовые секреты)
                                          ▲
worker (один экземпляр): dispatcher + scheduler ──► PartnerAdapter ──► Tihost API
                                          └──► GatewayAdapter ──► крипто-шлюз
```

Процессы:

- `bot`: aiogram 3, приём Telegram webhook (или long polling при `BOT_MODE=polling`), приём webhook шлюза, `/health`. Масштабируется горизонтально.
- `worker`: **ровно один экземпляр** (лидерство через `pg_advisory_lock`, при потере блокировки процесс завершается). Внутри: диспетчер записи к партнёру, планировщик периодических задач, отправка уведомлений из очереди.
- `postgres`, `redis`, опционально `caddy` для TLS.

### 3.2 Стек и версии

| Что | Выбор |
|---|---|
| Язык | Python 3.12 |
| Бот | aiogram 3.x (Bot API webhook, RedisStorage для FSM) |
| Web | aiohttp (встроен в интеграцию aiogram) |
| БД | PostgreSQL 16, SQLAlchemy 2.x async, asyncpg, Alembic |
| Кэш | Redis 7 |
| HTTP-клиент | httpx (AsyncClient, HTTP/1.1, keep-alive) |
| Схемы | Pydantic v2, pydantic-settings |
| Планировщик | APScheduler (AsyncIOScheduler) внутри worker |
| Логи | structlog, JSON |
| Шифрование | cryptography (Fernet) |
| Тесты | pytest, pytest-asyncio, respx, freezegun, testcontainers (или docker-compose для CI) |
| Качество | ruff, mypy, pre-commit |
| Пакеты | uv (pyproject.toml) |

### 3.3 Структура репозитория

```text
arix-cloud-bot/
├─ pyproject.toml
├─ docker-compose.yml
├─ Dockerfile
├─ .env.example
├─ docs/TZ.md
├─ scripts/
│  ├─ capture_fixtures.py
│  └─ backup_db.sh
├─ alembic/                     миграции
├─ app/
│  ├─ config.py                 pydantic-settings
│  ├─ logging.py                structlog + редактирование секретов
│  ├─ db/  session.py  models.py
│  ├─ core/
│  │  ├─ money.py               Decimal-хелперы
│  │  ├─ pricing.py             формула цены
│  │  ├─ ledger.py              проводки и баланс
│  │  ├─ orders.py              жизненный цикл заказов
│  │  ├─ servers.py             состояния, отображаемый статус
│  │  ├─ settings_store.py      настройки из БД с кэшем
│  │  ├─ audit.py
│  │  └─ secrets.py             Fernet, одноразовые секреты в Redis
│  ├─ partner/
│  │  ├─ base.py                PartnerAdapter (Protocol), модели, PartnerError
│  │  ├─ tihost/  client.py  parsers.py  errors.py
│  │  └─ fake.py                FakePartner для тестов и разработки
│  ├─ payments/
│  │  ├─ base.py                GatewayAdapter (Protocol), модели
│  │  ├─ fake.py
│  │  └─ <gateway>.py           после выбора шлюза
│  ├─ jobs/
│  │  ├─ dispatcher.py          очередь записи, лимитер
│  │  ├─ handlers/  provision.py renew.py power.py password.py reinstall.py script.py
│  │  ├─ scheduled/ sync_servers.py sync_catalog.py partner_balance.py renewals.py
│  │  │             reminders.py reconcile.py invoices_watch.py cleanup.py
│  │  └─ notify.py              отправка уведомлений с дедупликацией
│  ├─ bot/
│  │  ├─ main.py  web.py        запуск, webhook-маршруты, /health
│  │  ├─ middlewares/           user.py ban.py throttle.py audit.py maintenance.py
│  │  ├─ callbacks.py           все CallbackData
│  │  ├─ fsm.py
│  │  ├─ keyboards/
│  │  ├─ texts/  ru.py  en.py
│  │  └─ routers/
│  │     ├─ start.py buy.py balance.py servers.py help.py
│  │     └─ admin/ menu.py summary.py clients.py servers.py orders.py partner.py
│  │               abuse.py broadcast.py audit.py settings.py
│  └─ worker.py                 точка входа worker
└─ tests/
   ├─ unit/ integration/ contract/ fixtures/
```

### 3.4 Конфигурация (`.env`)

| Переменная | Пример | Описание |
|---|---|---|
| `BOT_TOKEN` | — | токен Telegram-бота |
| `BOT_MODE` | `webhook` | `webhook` или `polling` |
| `PUBLIC_BASE_URL` | `https://bot.example.com` | внешний адрес для webhook |
| `TG_WEBHOOK_SECRET` | случайная строка ≥ 32 | и путь `/tg/{secret}`, и `secret_token` |
| `OWNER_TG_ID` | `123456789` | первый owner |
| `ADMIN_CHAT_ID` | `-100...` | куда слать алерты |
| `DATABASE_URL` | `postgresql+asyncpg://...` | |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `PARTNER_BASE_URL` | боевой https-адрес | **OPEN-1** |
| `PARTNER_API_KEY` | — | Bearer-ключ |
| `PARTNER_WRITE_LIMIT` | `9` | записей в скользящем окне 60 с |
| `PARTNER_READ_LIMIT` | `50` | чтений в окне 60 с |
| `PARTNER_TIMEOUT_CONNECT` / `_READ` | `5` / `15` | секунды |
| `FERNET_KEY` | base64 32 байта | шифрование одноразовых секретов |
| `ENV` | `prod` | `dev` включает fake-оплату и подробные логи, `prod` запрещает их |
| `WEBHOOK_PATH_SECRET` | случайная строка ≥ 32 | сегмент пути `/webhooks/pay/{secret}` |
| `GATEWAY` | `fake` | `fake`, далее имя реализации |
| `GATEWAY_*` | — | параметры выбранного шлюза |
| `SUPPORT_URL` | `https://t.me/...` | ссылка на поддержку |
| `TERMS_URL` | — | оферта |
| `DEFAULT_LANG` | `ru` | |
| `SENTRY_DSN` | пусто | необязательно |

Настройки, меняемые в админке (таблица `settings`, раздел 5): наценка, комиссия, пороги, лимиты, режим обслуживания, включённые сети.

### 3.5 Развёртывание

`docker-compose.yml` с сервисами `bot`, `worker`, `postgres`, `redis`, `caddy`. Миграции запускаются командой `alembic upgrade head` при старте `bot` (с advisory-lock, чтобы не запускались параллельно). `worker` стартует после успешной миграции. Health: `GET /health` отдаёт `{"db": ok, "redis": ok, "partner": {"last_ping_ok": true, "age_s": 32}, "worker": {"leader": true, "queue_depth": 0}}`.

Переключение webhook/polling: смена `BOT_MODE` и перезапуск. При `polling` бот вызывает `delete_webhook`.

---

## 4. Деньги: цена и леджер

### 4.1 Правила денег

- Валюта только USD, тип `Decimal`, в БД `numeric(12,2)`. Строки партнёра (`"12.30"`) разбираются через `Decimal(str)`.
- `float` для денег запрещён во всём коде, включая тесты.
- Показ клиенту: два знака после точки, знак доллара: `$19.60`.

### 4.2 Формула цены (`app/core/pricing.py`)

```python
def client_price(partner_cost: Decimal, markup: Decimal, fee: Decimal, step: Decimal = Decimal("0.10")) -> Decimal:
    """price = ceil_to_step(cost * markup / (1 - fee))"""
    raw = partner_cost * markup / (Decimal("1") - fee)
    return (raw / step).to_integral_value(rounding=ROUND_CEILING) * step
```

- `markup` по умолчанию `2.0`, `fee` по умолчанию `0.02`, `step` `0.10`. Все три в `settings`.
- Пример: себестоимость `9.60`, markup `2.0`, fee `0.02` → `19.59…` → `19.60`.
- Цена всегда считается от **свежей** себестоимости партнёра. Себестоимость покупки берётся из кэша каталога (обновляется каждые 10 минут). В момент подтверждения заказа, если кэш старше 60 секунд, делается свежий `GET /catalog/plans?location=` (тратит бюджет чтения, если он исчерпан, берём кэш).
- Допуск: если новая цена отличается от показанной клиенту больше чем на `price_tolerance` (2%), заказ не проводится, клиенту показывается новая цена и просьба подтвердить ещё раз.
- В заказе фиксируются: `partner_cost_usd`, `markup`, `fee`, `price_usd`.
- Цена продления на `days` считается той же формулой от партнёрской цены продления на `days` (**OPEN-3**).

### 4.3 Леджер (`app/core/ledger.py`)

Леджер только дописывается: строки не правятся и не удаляются, ошибка исправляется встречной проводкой. Единственная функция, меняющая `users.balance_usd`:

```python
async def post_entry(
    session: AsyncSession, *, user_id: int, kind: LedgerKind, amount: Decimal,
    uniq_key: str, ref_type: str | None = None, ref_id: int | None = None,
    reason: str | None = None, admin_id: int | None = None,
) -> LedgerEntry:
    """Идемпотентная проводка. Вызывать внутри транзакции."""
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    existing = await session.scalar(select(LedgerEntry).where(LedgerEntry.uniq_key == uniq_key))
    if existing:
        return existing                      # повтор не применяется второй раз
    new_balance = user.balance_usd + amount
    if new_balance < 0:
        raise InsufficientBalance(balance=user.balance_usd, needed=-amount)
    entry = LedgerEntry(user_id=user_id, kind=kind, amount_usd=amount, uniq_key=uniq_key,
                        ref_type=ref_type, ref_id=ref_id, reason=reason, admin_id=admin_id)
    user.balance_usd = new_balance
    session.add(entry)
    await session.flush()
    return entry
```

| kind | Знак | uniq_key | Когда |
|---|---|---|---|
| `topup` | + | `topup:{invoice_id}` | оплачен счёт |
| `purchase` | − | `order:{order_id}:purchase` | подтверждён заказ покупки |
| `renewal` | − | `order:{order_id}:renewal` | подтверждено продление |
| `refund` | + | `order:{order_id}:refund` | заказ окончательно не выполнен |
| `promo` | + | `promo:{admin_id}:{uuid}` | админ начислил бонус |
| `adjust` | ± | `adjust:{admin_id}:{uuid}` | ручная правка, причина обязательна |

Правила:

- Проводки создаются в той же транзакции, что и смена статуса заказа.
- Баланс в `users.balance_usd` — кэш суммы проводок. Ночная задача `ledger_reconcile` сравнивает и шлёт алерт при расхождении.
- `refund` возможен один раз на заказ (уникальный ключ).
- Админские `promo` и `adjust` требуют роль owner, обязательное `reason` и пишутся в аудит.

---

## 5. Модель данных (PostgreSQL)

Все время `timestamptz` в UTC. Первичные ключи `bigserial`. Ниже DDL как основа миграции Alembic `0001_init`.

```sql
create table users (
  id bigserial primary key,
  tg_id bigint unique not null,
  username text,
  first_name text,
  lang text not null default 'ru',
  balance_usd numeric(12,2) not null default 0 check (balance_usd >= 0),
  banned boolean not null default false,
  bot_blocked boolean not null default false,     -- пользователь заблокировал бота
  ref_source text,
  tz text not null default 'UTC',
  created_at timestamptz not null default now(),
  last_seen_at timestamptz
);

create table admins (
  tg_id bigint primary key,
  role text not null check (role in ('owner','operator','support')),
  added_by bigint,
  created_at timestamptz not null default now()
);

create table servers (
  id bigserial primary key,
  user_id bigint not null references users(id),
  partner_id text unique,                          -- id у партнёра, null до создания
  partner_name text not null,                      -- 'arix-o{order_id}', неизменяемое
  display_name text not null,                      -- имя для клиента, меняется локально
  location text not null,
  os_label text,
  plan_label text,
  login text,                                      -- не секрет, показывается в карточке
  ip inet,
  cpu int, ram_mb int, disk_gb int,
  partner_state text,                              -- как вернул партнёр
  rent_expires_at timestamptz,
  auto_renew boolean not null default false,       -- НАШЕ автопродление
  renew_days int not null default 30 check (renew_days in (2,7,30,90,180,365)),
  cancelled boolean not null default false,        -- клиент отключил (не продлевать)
  frozen boolean not null default false,           -- заморожен админом (abuse)
  missing boolean not null default false,          -- пропал из ответа партнёра
  reminded_3d_at timestamptz,
  reminded_1d_at timestamptz,
  last_expired_notice_at timestamptz,
  reinstall_pending boolean not null default false,
  ready_notified_at timestamptz,
  partner_auto_renew_off boolean not null default false, -- подтверждено, что автопродление у партнёра выключено
  created_at timestamptz not null default now(),
  synced_at timestamptz
);
create index on servers (user_id);
create index on servers (rent_expires_at) where cancelled = false;

create table orders (
  id bigserial primary key,
  user_id bigint not null references users(id),
  kind text not null check (kind in ('purchase','renew','reinstall','admin_purchase')),
  status text not null check (status in (
    'draft','queued','provisioning','waiting_partner_funds','active',
    'needs_review','failed','refunded','cancelled')),
  server_id bigint references servers(id),
  snapshot jsonb not null default '{}',            -- выбор клиента и списки индексов каталога для callback
  location text, plan_id text, os_id text, months int, renew_days int,
  partner_cost_usd numeric(12,2), markup numeric(6,3), fee numeric(6,3), price_usd numeric(12,2),
  charged_by_partner_usd numeric(12,2),            -- реально списанное партнёром
  payment_mode text not null default 'balance' check (payment_mode in ('balance','promo','admin')),
  admin_id bigint,
  attempts int not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create index on orders (user_id, created_at desc);
create index on orders (status) where status in ('queued','provisioning','waiting_partner_funds','needs_review');

create table jobs (
  id bigserial primary key,
  kind text not null,                              -- provision renew power reset_password reinstall run_script set_auto_renew ...
  class text not null check (class in ('purchase','manage','renew','retry')),
  payload jsonb not null default '{}',             -- без секретов
  status text not null default 'pending' check (status in ('pending','running','done','failed','cancelled')),
  run_after timestamptz not null default now(),
  attempts int not null default 0,
  max_attempts int not null default 6,
  idem_key text,                                   -- Idempotency-Key для партнёра
  order_id bigint references orders(id),
  server_id bigint references servers(id),
  last_error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index on jobs (status, run_after, class);

create table partner_calls (                       -- журнал вызовов, он же окно лимитера
  id bigserial primary key,
  at timestamptz not null default now(),
  method text not null,                            -- GET/POST/PATCH
  path text not null,                              -- без query с секретами
  status int,
  error_code text,
  request_id text,
  duration_ms int,
  job_id bigint,
  job_class text                                   -- purchase/manage/renew/retry, для квот окна
);
create index on partner_calls (method, at desc);

create table ledger_entries (
  id bigserial primary key,
  user_id bigint not null references users(id),
  kind text not null check (kind in ('topup','purchase','renewal','refund','promo','adjust')),
  amount_usd numeric(12,2) not null,
  ref_type text, ref_id bigint,
  reason text,
  admin_id bigint,
  uniq_key text unique not null,
  created_at timestamptz not null default now()
);
create index on ledger_entries (user_id, created_at desc);

create table invoices (
  id bigserial primary key,
  user_id bigint not null references users(id),
  gateway text not null,
  gateway_invoice_id text unique,
  amount_usd numeric(12,2) not null,               -- запрошенная сумма
  credited_usd numeric(12,2),                      -- реально зачисленная
  asset text, network text,
  pay_amount numeric(20,8),
  address text, pay_url text,
  status text not null check (status in ('pending','paid','expired','partial','failed')),
  expires_at timestamptz not null,
  paid_at timestamptz,
  txid text,
  created_at timestamptz not null default now()
);
create index on invoices (user_id, created_at desc);
create index on invoices (status, expires_at);

create table notifications (                       -- дедупликация уведомлений
  id bigserial primary key,
  user_id bigint references users(id),
  key text not null,
  ref text not null default '',
  sent_at timestamptz not null default now(),
  unique (user_id, key, ref)
);

create table audit_log (
  id bigserial primary key,
  at timestamptz not null default now(),
  actor_kind text not null check (actor_kind in ('user','admin','system')),
  actor_id bigint,
  action text not null,
  subject_type text, subject_id bigint,
  before jsonb, after jsonb,
  request_id text
);
create index on audit_log (subject_type, subject_id, at desc);

create table settings (key text primary key, value jsonb not null, updated_at timestamptz default now(), updated_by bigint);
```

Начальные значения `settings` (миграция вставляет):

| key | value | Смысл |
|---|---|---|
| `markup` | `2.0` | наценка |
| `gateway_fee` | `0.02` | запас на комиссию шлюза |
| `price_step` | `0.10` | шаг округления вверх |
| `price_tolerance` | `0.02` | допуск изменения цены |
| `min_topup` / `max_topup` | `5` / `500` | границы пополнения, USD |
| `max_servers_per_user` | `10` | лимит серверов |
| `new_user_daily_purchases` | `2` | покупок в первые 24 часа |
| `partner_min_balance` | `20` | порог алерта, USD |
| `alert_lookahead_days` | `7` | горизонт обязательств |
| `maintenance` | `false` | режим обслуживания |
| `enabled_networks` | `[]` | включённые сети оплаты |
| `draft_ttl_min` | `30` | жизнь черновика |
| `invoice_ttl_min` | `60` | жизнь счёта |
| `default_renew_days` | `30` | период автопродления по умолчанию |
| `reminder_days` | `[3,1]` | за сколько дней напоминать |
| `stuck_order_min` | `5` | заказ считается застрявшим |
| `provision_delay_notify_min` | `15` | когда сообщить о задержке |

Хранение и очистка: `partner_calls` 14 дней, `jobs` со статусом done/failed 30 дней, `notifications` 90 дней. Очистка задачей `cleanup` раз в сутки.

---

## 6. Заказы, серверы, состояния

### 6.1 Жизненный цикл заказа

| Из | В | Событие | Побочные эффекты |
|---|---|---|---|
| — | `draft` | клиент выбрал локацию | создан черновик, TTL `draft_ttl_min` |
| `draft` | `cancelled` | отмена или TTL | черновик удалён очисткой |
| `draft` | `queued` | «Оплатить с баланса» | проводка `purchase`, job `provision` |
| `queued` | `provisioning` | диспетчер взял задачу | `attempts += 1` |
| `provisioning` | `active` | партнёр вернул сервер | создана строка `servers`, отправлены доступы |
| `provisioning` | `waiting_partner_funds` | `insufficient_funds` | алерт админу, повтор через 5 минут |
| `waiting_partner_funds` | `provisioning` | баланс партнёра достаточен | повтор той же job |
| `provisioning` | `failed` | окончательная ошибка 4xx | проводка `refund`, уведомление клиенту |
| `provisioning` | `needs_review` | исчерпаны повторы без ясности | алерт админу, деньги остаются списанными |
| `failed` | `refunded` | после проводки refund | заказ закрыт |
| `needs_review` | `active` / `refunded` | решение админа | вручную из админки |

Для `kind = renew`: `queued → provisioning → active` (продлено) либо `failed → refunded`. Для `kind = reinstall` денег нет: `queued → provisioning → active`. Для `admin_purchase` (ручной заказ за клиента): то же, что `purchase`, но оплата по `payment_mode`: `balance` (с баланса клиента), `promo` (проводка `promo`+`purchase`, чистый ноль, причина обязательна), `admin` (без проводок).

### 6.2 Серверы: отображаемый статус

Функция `display_status(server, now) -> Status` (порядок проверок важен):

| Условие | Статус (ключ) | Текст RU |
|---|---|---|
| `frozen` | `frozen` | Заморожен |
| `missing` | `missing` | Не найден у партнёра |
| `rent_expires_at <= now` | `expired` | Срок истёк |
| `partner_state in (creating, installing, migrating)` | `provisioning` | Создаётся |
| `partner_state == restarting` | `restarting` | Перезапускается |
| `partner_state == error` | `error` | Ошибка |
| `partner_state == stopped` | `stopped` | Остановлен |
| `partner_state in (active, running)` | `running` | Работает |
| иначе | `unknown` | Неизвестно |

Флаги `expiring` (до конца срока ≤ 3 дней) и `cancelled` накладываются поверх как пометки, а не как отдельные статусы.

Цвет точки в списке: `running` зелёный, `stopped`/`unknown` серый, `expiring` или `provisioning`/`restarting` жёлтый, `error`/`expired`/`missing`/`frozen` красный.

### 6.3 Доступность действий по статусу

| Статус | Старт | Стоп | Рестарт | Продлить | Пароль | Переустановка | Скрипты |
|---|---|---|---|---|---|---|---|
| running | нет | да | да | да | да | да | да |
| stopped | да | нет | нет | да | да | да | нет |
| provisioning | нет | нет | нет | нет | нет | нет | нет |
| restarting | нет | нет | нет | да | нет | нет | нет |
| error | нет | да | да | да | да | да | нет |
| expired | нет | нет | нет | да (**OPEN-5**) | нет | нет | нет |
| frozen | нет | нет | нет | нет | нет | нет | нет |
| missing | нет | нет | нет | нет | нет | нет | нет |

Кнопки, недоступные по статусу, не показываются. Если callback всё же пришёл (устарел экран), хендлер перечитывает статус и отвечает `answer_callback_query("Сейчас это действие недоступно")`.

---

## 7. Партнёрский адаптер, лимитер и фоновые задачи

### 7.1 Интерфейс `PartnerAdapter` (`app/partner/base.py`)

```python
class PartnerAdapter(Protocol):
    async def ping(self) -> Ping: ...
    async def balance(self) -> Decimal: ...
    async def plans(self, location: str) -> list[Plan]: ...
    async def os_list(self, location: str, plan_id: str) -> list[OsImage]: ...
    async def create_server(self, *, location: str, plan_id: str, os_id: str,
                            months: int, name: str, idem_key: str) -> CreatedServer: ...
    async def list_servers(self) -> list[PartnerServer]: ...
    async def get_server(self, partner_id: str) -> PartnerServer: ...
    async def renew(self, partner_id: str, days: int, idem_key: str) -> Renewed: ...
    async def set_auto_renew(self, partner_id: str, enabled: bool, idem_key: str) -> None: ...
    async def power(self, partner_id: str, action: Literal["start", "stop", "restart"], idem_key: str) -> None: ...
    async def reset_password(self, partner_id: str, password: str | None, idem_key: str) -> str: ...
    async def os_for_reinstall(self, partner_id: str) -> list[OsImage]: ...
    async def reinstall(self, partner_id: str, os_id: str, idem_key: str) -> None: ...
    async def scripts(self, partner_id: str) -> list[Script]: ...
    async def run_script(self, partner_id: str, script_id: str, idem_key: str) -> None: ...
```

Метода `rename` в адаптере нет: имя клиента хранится только у нас (`servers.display_name`). Имя у партнёра фиксированное `arix-o{order_id}`.

Модели Pydantic v2 с `model_config = ConfigDict(extra="allow")` (партнёр может добавить поля). Обязательные поля из документации:

| Модель | Поля |
|---|---|
| `Ping` | `account_id`, `server_time` |
| `Plan` | `id`, название, `cpu`, `ram_mb`, `disk_gb`, цены по периодам (месяцы → `Decimal`). Точные имена по фикстуре, **ASSUMPTION** |
| `OsImage` | `id`, `name` (**ASSUMPTION**) |
| `CreatedServer` | `id`, `name`, `os`, `location`, `login`, `password`, `rent_expires_at`, `charged_usd`, `balance_usd` |
| `PartnerServer` | `id`, `name`, `state`, `ip`, `cpu`, `ram_mb`, `disk_gb`, `rent_expires_at`, `renew_prices: dict[int, Decimal]` (**OPEN-3**), `os`, `location` |
| `Renewed` | `rent_expires_at`, `charged_usd`, `balance_usd` |

Все допущения о форме собираются в `app/partner/tihost/parsers.py`, каждая помечена `# ASSUMPTION`. Парсер не падает на неизвестных полях и логирует предупреждение, если обязательное поле отсутствует.

### 7.2 HTTP-клиент (`app/partner/tihost/client.py`)

- Один `httpx.AsyncClient` на процесс, keep-alive, таймауты `connect=5`, `read=15`.
- Заголовки: `Authorization: Bearer …`, `Accept: application/json`, для записей `Idempotency-Key`.
- Каждый вызов пишется в `partner_calls` (метод, путь без секретов, статус, `error.code`, `request_id`, длительность, `job_id`, `job_class`). Тела запросов и ответов в лог не пишутся.
- Ответы 4xx/5xx разбираются в `PartnerError(code, http, details, request_id, category)`, где `category` один из: `RETRY`, `WAIT_FUNDS`, `RATE_LIMITED`, `FATAL_SERVICE`, `FATAL_REQUEST`, `NOT_FOUND`, `STATE_CONFLICT`, `BUSY`, `EXPIRED`, `BUG`. Таблица соответствия в разделе 7.5.
- Сетевые ошибки (`ConnectError`, `ReadTimeout`) превращаются в `PartnerError(category=RETRY, ambiguous=True)` для записей и `RETRY` для чтения.
- Клиент сам не повторяет запросы. Повторами управляет диспетчер (записи) и вызывающий код (чтения).
- При старте: если `PARTNER_BASE_URL` начинается с `http://` и хост не `localhost`/`127.0.0.1`, процесс завершается с ошибкой.

### 7.3 Диспетчер записи (`app/jobs/dispatcher.py`)

Единственный цикл, который выполняет записи к партнёру. Работает только у лидера (`pg_advisory_lock`).

Правила лимита (скользящее окно 60 секунд по `partner_calls`):

- Запись = любой запрос с методом не `GET`. Разрешено не больше `PARTNER_WRITE_LIMIT` (9) записей в любом окне 60 с. Так, что бы партнёр ни использовал (скользящее или фиксированное окно), 10 не превышается.
- Чтение: не больше `PARTNER_READ_LIMIT` (50) за окно.
- При ответе 429: в Redis ставится `partner:paused_until = now + Retry-After`, до этого момента диспетчер ничего не пишет.

Приоритеты и квоты на окно (сумма равна `PARTNER_WRITE_LIMIT`, настраиваются `PARTNER_QUOTAS`):

| Класс | Приоритет | Квота |
|---|---|---|
| `purchase` | 1 | 4 |
| `manage` | 2 | 2 |
| `renew` | 3 | 2 |
| `retry` | 4 | 1 |

Алгоритм выбора следующей задачи:

```python
async def pick_next_job(session) -> Job | None:
    if paused(): return None
    used = await writes_in_window_by_class(session)          # {class: count} за 60 с
    if sum(used.values()) >= WRITE_LIMIT: return None
    due = select(Job).where(Job.status == "pending", Job.run_after <= now())
    # 1) по приоритету, пока квота класса не выбрана
    for cls in ("purchase", "manage", "renew", "retry"):
        if used.get(cls, 0) < QUOTAS[cls]:
            job = await claim(session, due.where(Job.class_ == cls))   # FOR UPDATE SKIP LOCKED
            if job: return job
    # 2) без простоя: любой класс по приоритету
    for cls in ("purchase", "manage", "renew", "retry"):
        job = await claim(session, due.where(Job.class_ == cls))
        if job: return job
    return None
```

Правила исполнения:

- **Одна задача делает не больше одной записи к партнёру.** Последующие записи это отдельные задачи (например, `set_auto_renew` после покупки). Чтения внутри обработчика разрешены в пределах бюджета чтения.
- Обработчик возвращает один из результатов: `Done`, `Retry(after, reason, count_attempt=True)`, `Wait(after, reason)` (не увеличивает `attempts`), `Fail(reason)`, `Review(reason)`.
- Пауза между повторами (по `attempts`): 5, 15, 45, 120, 300, 600 секунд. После `max_attempts` (6) задача уходит в `Review`.
- `Idempotency-Key` фиксируется при создании задачи и **не меняется** на всех повторах этой задачи. Формат: `order-{order_id}` (покупка), `renew-{order_id}`, `reinstall-{order_id}`, `job-{job_id}` (остальное). Длина ≤ 128.
- Падение процесса во время `running`: задачи со статусом `running` старше 5 минут возвращаются в `pending` задачей `reconcile_orders`. Безопасно благодаря идемпотентному ключу.

### 7.4 Обработчики задач

#### `provision` (покупка)

```text
1. Заблокировать заказ (FOR UPDATE). Если статус active/refunded/cancelled → Done.
2. Статус provisioning, attempts += 1.
3. Если attempts >= 3 и последняя ошибка «неоднозначная» (таймаут после отправки):
   a) list_servers() (чтение), найти сервер с name == "arix-o{order_id}".
   b) Нашли → adopt: создать строку servers, статус active, поставить job reset_password (пароля у нас нет), уведомить.
4. (Опционально, PRECHECK_PARTNER_BALANCE=true) balance() < partner_cost → Wait(5 мин), алерт админу.
5. create_server(location, plan_id, os_id, months, name="arix-o{order_id}", idem_key=job.idem_key).
6. Успех:
   - INSERT servers (partner_id, partner_name, display_name="server-{server_id}", location, os_label, plan_label,
     rent_expires_at, auto_renew=false, renew_days=default_renew_days).
   - orders: status=active, server_id, charged_by_partner_usd=charged_usd, finished_at.
   - INSERT job set_auto_renew(enabled=false), class=purchase, idem_key=job-{id}.
   - Отправить доступы (7.4.1), не сохраняя пароль в БД.
   - Пометить в Redis флаг «ускоренный sync».
7. Ошибки по таблице 7.5.
```

Отображаемое имя по умолчанию: `server-{server_id}`. Клиент меняет его в боте.

##### 7.4.1 Выдача доступов

- Сообщение отправляется с `protect_content=True` (запрет пересылки и сохранения).
- Текст: логин, пароль в `<code>`, предупреждение, что пароль показан один раз (шаблон `creds` в разделе 10).
- В Redis кладётся зашифрованная (Fernet) копия `{login, password, chat_id, message_id}` под ключом `cred:{server_id}` с TTL 15 минут: если отправка не удалась, повторная попытка берёт её оттуда. После доставки копия остаётся до TTL или до нажатия «Я сохранил, удалить».
- Сообщение с паролем удаляется автоматически через 10 минут: запись в Redis ZSET `tg:delete_queue` (score = unix-время удаления), воркер опрашивает раз в 30 секунд.
- Пароль нигде больше не хранится и не пишется в логи, `jobs.payload`, `jobs.last_error`, аудит.
- IP в ответе покупки нет (**OPEN-9**): отдельное уведомление «Сервер готов» с IP приходит, когда `sync_servers` увидит `running` и IP.

#### `renew` (продление)

```text
1. Заказ kind=renew уже создан, проводка renewal списана (в момент подтверждения).
2. renew(server.partner_id, days, idem_key="renew-{order_id}").
3. Успех: servers.rent_expires_at = result.rent_expires_at; reminded_3d_at/reminded_1d_at = null;
   заказ active; уведомление «Продлено до …».
4. Ошибки: insufficient_funds → Wait(5 мин) + алерт;
   rent_expired → servers помечается expired, refund, уведомление «Срок истёк, напишите в поддержку» (OPEN-5);
   conflict/BUG → Fail → refund.
```

#### `power`

```text
payload: {server_id, action, msg_ref: {chat_id, message_id}}
1. power(partner_id, action, idem_key).
2. Успех: обновить карточку (edit_message по msg_ref) текстом «Команда отправлена, статус обновится в течение минуты»,
   выставить флаг ускоренного sync.
3. conflict → уведомление «Сейчас недоступно, состояние: …» и немедленный get_server.
4. server_busy → Retry(60 c), до 5 раз.
```

#### `reset_password`

```text
1. reset_password(partner_id, None, idem_key) → новый пароль (OPEN-2).
2. Доставка как в 7.4.1, но текст «Новый пароль» (шаблон creds_password_only).
3. Если пароль не удалось извлечь из ответа → Review + алерт.
```

#### `reinstall`

```text
1. reinstall(partner_id, os_id, idem_key="reinstall-{order_id}") с confirm=true (задаётся адаптером).
2. servers.reinstall_pending = true, ускоренный sync.
3. sync_servers, увидев состояние running при reinstall_pending → создаёт job reset_password и после доставки
   пароля шлёт уведомление «ОС переустановлена». reinstall_pending = false.
```

#### `run_script`, `set_auto_renew`

Простые вызовы адаптера, результат в уведомление («Скрипт запущен») или тихо (`set_auto_renew`). Ошибка `set_auto_renew` после покупки: повторы, затем алерт админу (риск: партнёр продлит за наш счёт).

### 7.5 Ошибки партнёра: реакция диспетчера

| Категория (code) | Реакция задачи | Уведомления |
|---|---|---|
| `RETRY` (`internal_error`, `service_unavailable`, сетевой сбой) | `Retry` с backoff, тот же ключ | клиенту статус «в очереди/создаётся»; после 3-й попытки алерт админу |
| `RATE_LIMITED` (`rate_limited`) | пауза `Retry-After`, `Wait` без увеличения `attempts` | нет |
| `WAIT_FUNDS` (`insufficient_funds`) | заказ `waiting_partner_funds`, `Wait(5 мин)` | админу алерт с `details.missing_usd`; клиенту «Создаём сервер, до 15 минут»; через `provision_delay_notify_min` сообщение о задержке |
| `BUSY` (`server_busy`) | `Retry(60 c)` до 5 раз | клиенту «Сервер занят, повторим» |
| `STATE_CONFLICT` (`conflict`) | `Fail`, обновить состояние сервера чтением | «Сейчас это действие недоступно, состояние: …» |
| `EXPIRED` (`rent_expired`) | сервер expired, для платных операций refund | «Срок истёк» |
| `NOT_FOUND` (`not_found`) | `servers.missing = true`, `Fail` | админу алерт |
| `FATAL_REQUEST` (`invalid_request`) | `Fail`, refund платных заказов | клиенту «Не получилось, поддержка уведомлена», админу алерт с `request_id` |
| `FATAL_SERVICE` (`unauthorized`, `token_invalid`, `forbidden`, `account_banned`) | включить режим обслуживания (`maintenance=true`), задачи в `Wait`, критичный алерт | клиентам «Сервис временно недоступен» |
| `BUG` (`idempotency_key_reused`) | `Review`, критичный алерт разработчику | нет |

### 7.6 Плановые задачи (`app/jobs/scheduled/`)

Все выполняются в `worker` через APScheduler, у каждой защита от параллельного запуска (`max_instances=1`) и запись результата в лог.

| Задача | Период | Что делает |
|---|---|---|
| `partner_ping` | 60 с | `ping()`. 3 неудачи подряд: алерт, флаг `partner_down` (покупки и продления временно выключаются в UI). Восстановление: алерт «ok» |
| `sync_servers` | 60 с (15 с при активной установке) | см. ниже |
| `sync_catalog` | 10 мин | `plans(location)` по трём локациям в кэш Redis `catalog:plans:{loc}` (TTL 1 ч), метка `fetched_at`. Ошибка: оставить кэш, алерт при возрасте > 60 мин |
| `partner_balance` | 5 мин | `balance()`, расчёт обязательств (ниже), алерт, значение в Redis `partner:balance` |
| `renewals` | час | автопродление (ниже) |
| `reminders` | час | напоминания за 3 и 1 день |
| `reconcile_orders` | 5 мин | возврат зависших `running`-задач, алерт по заказам старше `stuck_order_min` |
| `invoices_watch` | 5 мин | закрытие просроченных счетов и опрос шлюза (раздел 8) |
| `cleanup` | сутки | очистка по срокам хранения |
| `ledger_reconcile` | сутки | сверка кэша баланса с суммой проводок |

#### `sync_servers`

```text
1. Проверить бюджет чтения. Нет бюджета — пропустить цикл.
2. list_servers().
3. Для каждого партнёрского сервера:
   - найти нашу строку по partner_id; если нет, по partner_name == "arix-o{id}" (усыновление);
   - не найден совсем: считать «неучтённым» (например, куплен вручную), не ошибка; учитывать в сводке админа.
   - обновить partner_state, ip, cpu, ram_mb, disk_gb, rent_expires_at, renew_prices, synced_at.
   - если IP или renew_prices нет в списке: get_server() в пределах бюджета (не больше 5 за цикл).
4. События:
   - переход в running и ready_notified_at is null → уведомление «Сервер готов» (дедупликация server_ready:{id}).
   - переход в error → уведомление клиенту и админу.
   - reinstall_pending и состояние running → job reset_password (см. reinstall).
   - rent_expires_at в прошлом → уведомление «Срок истёк», не чаще раза в сутки, 3 раза.
5. Наши серверы, которых нет в ответе: счётчик пропусков в Redis; при 2 подряд missing=true и алерт; появился снова → missing=false.
6. Если есть серверы в provisioning/reinstall_pending, выставить в Redis флаг fast_sync (период 15 с) на 10 минут.
```

#### `renewals`

```text
Кандидаты: servers.auto_renew AND NOT cancelled AND NOT frozen AND NOT missing
           AND rent_expires_at <= now() + interval '24 hours'
           AND NOT EXISTS(pending/provisioning renew order для сервера).
Для каждого:
  price = client_price(partner_renew_price(server, renew_days), ...)
  если balance_usd >= price:
      в одной транзакции: заказ kind=renew (queued) + проводка renewal + job renew(class=renew)
  иначе:
      уведомление renew_failed_no_funds (дедупликация server:{id}:{date}) — не чаще раза в сутки
Не создавать второй renew, если он уже в очереди.
```

#### `reminders`

За 3 дня и за 1 день до `rent_expires_at`, один раз каждое (`reminded_3d_at`, `reminded_1d_at`, сбрасываются при продлении). Текст включает: срок, включено ли автопродление, хватает ли баланса. Не отправлять для `cancelled`.

#### Расчёт обязательств перед партнёром

```text
obligations(days) = сумма партнёрских цен продления (renew_days) серверов с auto_renew, у которых
                    rent_expires_at попадает в ближайшие days дней
                  + сумма partner_cost_usd заказов в queued/provisioning/waiting_partner_funds
covers_days = максимальное d в 1..30, для которого obligations(d) <= balance
Алерт, если balance < partner_min_balance ИЛИ balance < obligations(alert_lookahead_days) * 1.2.
Дедупликация алерта 6 часов. Показ в админке: «хватает на {covers_days} дней».
```

### 7.7 Бюджет чтения для интерактивных запросов

Функция `partner_reads.get_server_fresh(server)` вызывается при открытии карточки. Она обновляет данные, только если выполнены оба условия: свежий запрос по этому серверу не делался последние 10 секунд, и в окне есть запас чтения (`READ_LIMIT` минус 10 резерв под плановые задачи). Иначе карточка строится по кэшу в БД, флаг «данные на HH:MM» показывается, если кэш старше 3 минут.


---

## 8. Платежи (крипто-шлюз)

Шлюз выбирается через `GATEWAY` (**OPEN-11**: Cryptomus, CryptoCloud или BTCPay Server, решает владелец). Код не привязан к шлюзу: всё идёт через интерфейс, реализация шлюза лежит в `app/payments/<gateway>.py`. До выбора работает `FakePaymentGateway` (для разработки и тестов, оплата эмулируется командой админа `/fake_pay <invoice_id>` только при `ENV=dev`).

### 8.1 Интерфейс

```python
class PaymentGateway(Protocol):
    name: str
    async def create_invoice(self, *, order_ref: str, amount_usd: Decimal, ttl_min: int,
                             network: str | None) -> GatewayInvoice: ...
    async def get_invoice(self, gateway_invoice_id: str) -> GatewayInvoice: ...
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent: ...
        # бросает InvalidSignature; НЕ ходит в сеть

class GatewayInvoice(BaseModel):
    gateway_invoice_id: str
    status: Literal["pending", "paid", "expired", "partial", "failed"]
    pay_url: str | None
    address: str | None
    asset: str | None
    network: str | None
    pay_amount: Decimal | None
    paid_usd: Decimal | None        # реально пришло, в USD по курсу шлюза
    txid: str | None
    expires_at: datetime

class WebhookEvent(BaseModel):
    gateway_invoice_id: str
    order_ref: str                  # наш invoice id, который мы передали при создании
    status: str
    paid_usd: Decimal | None
    txid: str | None
    raw_hash: str                   # sha256 тела, для журнала
```

### 8.2 Поток пополнения

1. Клиент в боте: «Пополнить» → выбирает сумму (кнопки $10 / $25 / $50 / $100 или ввод своей) → выбирает сеть (из `enabled_networks`).
2. Проверка `min_topup <= сумма <= max_topup`, лимит: не более 5 счетов `pending` на пользователя (старые истекают сами).
3. Создаётся строка `invoices` (`status=pending`, `expires_at = now + invoice_ttl_min`), затем `gateway.create_invoice(order_ref=str(invoice.id))`. Если вызов шлюза упал: строка удаляется, клиенту «Шлюз сейчас недоступен, попробуйте позже», алерт админу (дедуп 10 минут).
4. Клиент получает экран счёта (адрес или кнопка «Открыть страницу оплаты», сумма к отправке, сеть, срок), кнопки «Проверить оплату» и «Отмена».
5. Приход денег фиксируется вебхуком (основной путь) и поллером (страховка).

### 8.3 Вебхук

Маршрут: `POST /webhooks/pay/{WEBHOOK_PATH_SECRET}` (случайный сегмент в пути хранится в `.env`, плюс подпись шлюза). Алгоритм обработчика:

```text
1. Прочитать тело как bytes (до любого JSON-парсинга). Ограничение 64 КБ.
2. verify_webhook(headers, body). Ошибка подписи → 401, запись в журнал безопасности, счётчик; без деталей в ответе.
3. Найти invoice по order_ref (наш id), сверить gateway_invoice_id. Не найден → 200 (не провоцировать повторы), алерт.
4. Обязательно перепроверить статус запросом gateway.get_invoice(...). Данные из вебхука сами по себе денег не зачисляют.
5. В одной транзакции с блокировкой строки invoice (FOR UPDATE):
   - если invoice.status уже paid → вернуть 200 (идемпотентность);
   - status=paid: credited = min(paid_usd, amount_usd * 1.5) если paid_usd есть, иначе amount_usd;
       post_entry(kind=topup, amount=credited, uniq_key=f"topup:{invoice.id}"), invoice.status=paid, paid_at, txid, credited_usd;
   - status=partial: записать как partial, зачисление НЕ делать, админу уведомление (решает вручную: adjust на факт. сумму);
   - status=expired/failed: обновить статус.
6. После коммита: уведомление клиенту `topup_ok` (раздел 12.1). Заказов, ожидающих оплаты, по дизайну нет: покупка идёт только с баланса.
7. Ответ 200 быстро (до 1 секунды). Тяжёлая работа не выполняется в обработчике.
```

Недоплата, переплата, оплата после истечения счёта: если шлюз сообщает `paid` для истёкшего счёта, деньги всё равно зачисляются (клиент отправил в срок жизни адреса) с пометкой в аудит. Правило: **зачисляем только то, что шлюз подтверждает как фактически полученное**.

### 8.4 Поллер счетов (страховка)

Задача `poll_invoices` каждые 60 секунд: для `invoices.status='pending'` и `expires_at > now() - 2 hours` вызывает `gateway.get_invoice` (не чаще раза в 30 секунд на счёт, пачкой не более 20) и применяет тот же путь зачисления, что и вебхук (общая функция `apply_invoice_state`). Просроченные без оплаты через 2 часа после `expires_at` помечаются `expired`.

Кнопка «Проверить оплату» вызывает то же самое для одного счёта, с ограничением раз в 10 секунд на пользователя.

### 8.5 Требования

- Комиссию шлюза берём из цены (`gateway_fee`), на пополнение комиссии не добавляем.
- Все суммы шлюза парсятся в `Decimal` из строк, не из float.
- Мемо/адрес, суммы к оплате в криптовалюте показываются точно так, как вернул шлюз (не пересчитываются нами).
- Нет автоматических возвратов на кошелёк клиента. Возврат клиенту денег с баланса делается вручную владельцем вне бота, в боте фиксируется `adjust` с причиной.

---

## 9. Бот: общие правила

### 9.1 Ввод и навигация

- Основной интерфейс: inline-кнопки. Команды: `/start`, `/menu`, `/help`, `/balance`, `/servers`, `/admin` (только для админов, остальным ответ как на неизвестную команду).
- Каждое сообщение бота с кнопками редактируется на месте (`edit_message_text`), а не плодит новые. Исключения: доступы к серверу, уведомления, счёт на оплату (новые сообщения).
- Вложенность не глубже 4 экранов, на каждом есть «Назад» и «В меню».
- `/start` всегда возвращает главное меню и сбрасывает FSM. Параметр `/start ref_xxx` сохраняется в `users.ref_source` один раз.
- Один активный «экран» на пользователя: id сообщения меню хранится в Redis (`ui:{tg_id}:menu`). При устаревании (не удалось отредактировать) отправляется новое сообщение.

### 9.2 callback_data

Ограничение Telegram: 64 байта. Формат `префикс:поле:поле`, поля без двоеточий, числа в base10.

| Префикс | Формат | Назначение |
|---|---|---|
| `nav` | `nav:{to}` | to: `home`, `help`, `lang`, `servers`, `balance`, `buy` |
| `buy` | `buy:{step}:{draft_id}:{arg}` | step: `loc`, `plan`, `os`, `term`, `confirm`, `go`, `back`, `cancel`. `arg` для каталога это индекс в `orders.snapshot`, не id партнёра |
| `bal` | `bal:{action}:{arg}` | action: `topup`, `amt` (arg=сумма), `own` (ввод суммы), `net` (arg=сеть), `chk` (arg=invoice_id), `cancel`, `hist` (arg=страница) |
| `srv` | `srv:{action}:{server_id}:{arg}` | action: `list` (arg=страница), `open`, `pwr` (arg=start/stop/restart), `pwr_cf`, `rn`, `rn_d` (arg=дни), `rn_go`, `ar` (arg=on/off), `pw`, `pw_go`, `nm`, `ri`, `ri_os` (arg=индекс), `ri_cf`, `sc`, `sc_run`, `rm`, `rm_cf`, `ack` |
| `adm` | `adm:{section}:{action}:{arg}` | section: `home`, `usr`, `srv`, `ord`, `fin`, `set`, `bc`, `aud`, `cat` |

Правила:

- Каждый обработчик `srv:*` первым делом загружает сервер и проверяет `server.user_id == текущий пользователь` (или что пользователь админ для `adm:*`). Несоответствие: `answer_callback_query("Не найдено")` и запись в журнал безопасности.
- Каждый обработчик `adm:*` проверяет админа по `admins` на каждый вызов (не кэшировать дольше 60 секунд), роль сверяется по матрице раздела 11.1.
- Устаревшие кнопки (черновик не найден, состояние изменилось): `answer_callback_query` с коротким текстом и возврат на актуальный экран.
- Всегда вызывать `callback_query.answer()`, иначе «часики» на кнопке висят.
- Повторное нажатие кнопки-действия (двойной тап) защищается Redis-замком `lock:cb:{tg_id}:{hash(callback_data)}` на 3 секунды и идемпотентностью самой операции.

### 9.3 FSM (aiogram, хранилище Redis)

| Состояние | Где | Ожидает | Выход |
|---|---|---|---|
| `TopupAmount` | ввод своей суммы пополнения | число `min_topup..max_topup` | счёт или ошибка формата, кнопка «Отмена» |
| `RenameServer` | смена имени | 2–49 символов, без переводов строки, HTML экранируется | сохраняется в `servers.display_name` |
| `ReinstallConfirm` | подтверждение переустановки | точный ввод имени сервера | job reinstall или отмена |
| `AdminSearch` | поиск клиента/сервера | tg_id, @username, id, IP | результат |
| `AdminAmount` / `AdminReason` | промо/правка баланса | сумма, затем причина ≥ 5 символов | подтверждение и проводка |
| `AdminBroadcast` | рассылка | текст | превью → подтверждение |
| `AdminSetting` | изменение настройки | значение по типу | валидация, запись, аудит |

Правила: TTL состояния 15 минут; любая команда или кнопка главного меню сбрасывает FSM; произвольный текст вне состояний игнорируется с подсказкой «Используйте кнопки меню».

### 9.4 Форматирование и локализация

- `parse_mode=HTML`. Весь пользовательский ввод (имена серверов, username) экранируется через `html.escape`. Технические значения (IP, логин, пароль) в `<code>`.
- Деньги: `$12.40`, две цифры после точки; даты в часовом поясе пользователя (`users.tz`, по умолчанию UTC, меняется в «Язык и часовой пояс»), формат `21.10.2026 14:30`; оставшиеся дни считаются целыми вверх.
- Язык: `ru` по умолчанию, `en` в M7. Все строки в `app/bot/texts/{ru,en}.py` по ключам. Недостающий английский ключ падает на русский.
- Длина сообщений ≤ 4096 символов, длинные списки страницами по 6 серверов.
- Кнопки: не более 2 в ряду для текстовых, 1 в ряду для длинных подписей.

### 9.5 Ошибки бота

Глобальный error-handler aiogram: логирует исключение с `request_id`, клиенту показывает «Что-то пошло не так. Мы уже знаем. Код: {short_id}», админ-чату алерт с типом исключения и `short_id` (без трейсбека, он в Sentry/логах). Ошибки Telegram: `TelegramForbiddenError` → `users.bot_blocked=true`, дальнейшие уведомления пользователю пропускаются; `TelegramRetryAfter` → пауза и повтор; `TelegramBadRequest: message is not modified` игнорируется.

### 9.6 Режим обслуживания и бан

- `maintenance=true`: клиентам всё, кроме просмотра серверов и баланса, отвечает «Идут технические работы. Серверы работают, вернёмся в течение часа». Админы работают как обычно.
- `users.banned`: middleware отвечает «Доступ ограничен. Напишите в поддержку» и ничего не выполняет. Забаненные не получают уведомлений, кроме связанных с их серверами (см. раздел 12).

---

## 10. Экраны клиента (тексты RU)

Тексты рабочие, ключи в `texts/ru.py`. `{...}` подстановки. Эмодзи-индикаторы состояния допустимы только те, что указаны в разделе 6.2.

### 10.1 Приветствие и главное меню

Первое `/start` (нового пользователя):

```text
Привет! Это ARIX Cloud: виртуальные серверы, которые создаются за пару минут.
Оплата криптовалютой с баланса. Сервер получаешь сразу после оплаты.

Продолжая, ты соглашаешься с условиями: {TERMS_URL}
```

Кнопки: `[Принимаю, продолжить]` → создаёт `users`, показывает главное меню. Без нажатия других действий нет (гейт оферты).

Главное меню:

```text
ARIX Cloud

Баланс: ${balance}
Серверов: {count}{expiring_line}

Что делаем?
```

`expiring_line` = `\nСкоро истекает: {n}`, если есть серверы с `expiring`. Кнопки:

```text
[Купить сервер]  [Мои серверы]
[Баланс]         [Помощь]
[Язык]
```

### 10.2 Покупка сервера

Каталог берётся из кэша Redis (`catalog:plans:{location}`, TTL 5 минут), при промахе запрос к партнёру (чтение). Если партнёр недоступен и кэша нет: «Каталог временно недоступен, попробуйте через минуту» (кнопка «Повторить»).

**Шаг 1. Локация** (создаётся `draft`, snapshot заполняется):

```text
Где разместить сервер?
```

Кнопки: `Германия · от $X/мес`, `Финляндия · …`, `Польша · …` (цена = минимальная месячная клиентская цена по локации). `[Назад]`.

**Шаг 2. Тариф:**

```text
Локация: {location}
Выбери тариф:
```

Кнопки по одной в ряду: `{cpu} vCPU · {ram} ГБ · {disk} ГБ · ${monthly_price}/мес`. Порядок по цене. Если тарифов больше 8: страницы. Показывать только `available` тарифы (если в каталоге есть поле доступности, **ASSUMPTION**).

**Шаг 3. ОС** (`GET /catalog/os?location&plan_id`):

```text
Тариф: {plan}
Выбери операционную систему:
```

Кнопки по две в ряду, группами: Ubuntu, Debian, прочие Linux, Windows (если есть, с пометкой «доп. цена», если цена отличается).

**Шаг 4. Срок:**

```text
{plan} · {location} · {os}
На какой срок?
```

Кнопки: `1 мес · ${p1}`, `3 мес · ${p3} (−{d}%)`, `6 мес · ${p6} (−{d}%)`, `12 мес · ${p12} (−{d}%)`. Скидка считается от цены месяца ×N и показывается только если положительна. Цена периода = `client_price(partner_cost_for_period)`.

**Шаг 5. Подтверждение:**

```text
Проверь заказ

Локация: {location}
Тариф: {cpu} vCPU · {ram} ГБ RAM · {disk} ГБ диск
ОС: {os}
Срок: {months} мес (до {end_date})
Итого: ${price}
На балансе: ${balance}

Сервер создаётся сразу после оплаты. Доступы придут в этот чат.
```

Кнопки: если баланса хватает: `[Оплатить ${price}]`, иначе `[Пополнить на ${missing}]` (ведёт в поток пополнения с предзаполненной суммой, после оплаты клиент возвращается на этот же черновик, если он жив) и `[Отмена]`.

**Шаг 6. Оплата (`buy:go`), в одной транзакции:**

```text
1. Загрузить draft (проверить владельца и TTL). Не найден → «Заказ устарел, начните заново».
2. Пересчитать цену по свежему каталогу. Если |новая - старая| / старая > price_tolerance или цена выросла более чем на $0.10 →
   показать экран подтверждения с новой ценой («Цена изменилась»), заказ не проводить.
3. Проверить лимиты: max_servers_per_user (считая активные+в заказах), new_user_daily_purchases, maintenance, banned.
4. Проверить, что нет другого queued/provisioning заказа с теми же параметрами за последние 30 секунд (защита от дублей).
5. post_entry(purchase, -price, uniq_key=order:{id}:purchase). InsufficientBalance → экран «Не хватает $X».
6. orders.status=queued, INSERT job provision (class=purchase, idem_key=order-{id}), audit.
7. Commit. Затем ответ клиенту.
```

Ответ клиенту:

```text
Заказ #{order_id} принят. Создаём сервер, обычно 1–3 минуты.
Пришлю доступы сюда. Можно закрыть чат.
```

Кнопки: `[Мои серверы]`, `[В меню]`. Если задержка > `provision_delay_notify_min`: «Создание идёт дольше обычного. Ничего делать не нужно, деньги не потеряются. Если сервер не появится, вернём оплату.» (один раз.)

### 10.3 Доступы к серверу (уведомление о готовности)

Два сообщения. Первое (обычное):

```text
Сервер готов: {display_name}
Локация: {location} · {os}
IP: <code>{ip}</code>
Логин: <code>{login}</code>
Оплачен до: {rent_expires_at}

Пароль в следующем сообщении. Оно скроется через 10 минут, сохрани пароль сразу.
```

Второе с `protect_content=True`, ставится в очередь удаления через 10 минут:

```text
Пароль: <code>{password}</code>
Подключение: <code>ssh {login}@{ip}</code>
```

Кнопки под первым сообщением: `[Открыть сервер]`, `[Как подключиться]`. Если IP ещё неизвестен (партнёр не вернул): в первом сообщении «IP появится в карточке сервера через минуту».

Если пароль недоступен (истёк Redis-кэш, клиент не открыл): кнопка `[Получить новый пароль]` → `srv:pw`.

«Как подключиться» (статический текст): для Linux `ssh`, для Windows RDP-клиент и порт 3389, как сменить пароль после входа, предупреждение не публиковать пароль.

### 10.4 Мои серверы

```text
Мои серверы ({count})
```

По одной кнопке на сервер: `{dot} {display_name} · {ip} · {days_left} дн.` По 6 на страницу, `[◀] [▶]`. Если пусто:

```text
У тебя пока нет серверов.
```

Кнопки: `[Купить сервер]`, `[В меню]`.

### 10.5 Карточка сервера

```text
{dot} {display_name}   [{status_text}]

Локация: {location}
ОС: {os}
Ресурсы: {cpu} vCPU · {ram} ГБ RAM · {disk} ГБ
IP: <code>{ip}</code>
Логин: <code>{login}</code>

Оплачен до: {rent_expires_at} (ещё {days_left} дн.)
Автопродление: {auto_text}
{notes}

Данные на {synced_at}   ← только если кэш старше 3 минут
```

`auto_text`: `включено, {renew_days} дн. · ~${renew_price}` либо `выключено`. `notes` (строки при необходимости): «Хватит ли баланса для продления: нет, нужно ещё ${x}» (если автопродление включено), «Сервер заморожен: обратитесь в поддержку», «Срок истёк. Данные хранятся по правилам партнёра, продлите как можно скорее», «Сервер помечен как отключённый: продлеваться не будет».

Кнопки (по доступности, раздел 6.3):

```text
[Старт] или [Стоп] [Рестарт]
[Продлить] [Автопродление]
[Новый пароль] [Переименовать]
[Переустановить ОС] [Скрипты]
[Отключить сервер]
[К списку] [В меню]
```

### 10.6 Питание

Стоп и рестарт с подтверждением: «Остановить сервер {name}? Он перестанет отвечать до запуска.» `[Да, остановить] [Отмена]`. Старт без подтверждения.
После нажатия: экран карточки со строкой «Команда отправлена, обновляем состояние…»; job `power`; после выполнения карточка обновляется (редактирование сообщения по сохранённым chat_id/message_id из `jobs.payload`). Если очередь длинная (>30 секунд): «Команда в очереди».

### 10.7 Продление

Экран выбора срока: кнопки `{d} дн · ${price}` для `{2,7,30,90,180,365}` (цены из партнёрских цен продления, раздел 7 и OPEN-3). Ниже: `На балансе: ${balance}`. После выбора:

```text
Продлить {name} на {d} дн.?
Спишем ${price}. Новая дата: {new_end}
```

`[Продлить] [Отмена]`. Недостаточно баланса → `[Пополнить на ${missing}]`. Успех (после job): «Сервер продлён до {date}». Идемпотентность: повторное нажатие в течение 30 секунд при существующем queued renew возвращает «Продление уже выполняется».

### 10.8 Автопродление

Переключатель `srv:ar:{id}:on|off`. Включение: выбор периода (30 дн по умолчанию) и текст «Продлеваем за 24 часа до конца, если на балансе хватает денег. Иначе пришлём напоминание.» Выключение без подтверждения. Это наш флаг, партнёрское автопродление всегда выключено (раздел 7).

### 10.9 Новый пароль

```text
Сбросить пароль сервера {name}?
Старый пароль перестанет работать.
```

`[Сбросить] [Отмена]` → job `reset_password` → пароль приходит по схеме 10.3 (защищённое сообщение). Ограничение: не чаще 3 раз в час на сервер.

### 10.10 Переименование

FSM `RenameServer`: «Новое имя сервера (2–49 символов):». Имя меняется только локально в `display_name`, партнёру не отправляется (раздел 7, у нас `partner_name` фиксированный).

### 10.11 Переустановка ОС

1. `srv:ri` → выбор ОС из `GET /servers/{id}/os` (кэш 5 мин), кнопки по две.
2. Предупреждение:

```text
Переустановка удалит ВСЕ данные на сервере {name}.
Новая ОС: {os}.
Чтобы подтвердить, напиши имя сервера: {name}
```

3. FSM `ReinstallConfirm`: точное совпадение с `display_name`. Иначе «Имя не совпало, переустановка отменена».
4. job `reinstall`. После завершения автоматически `reset_password`, клиент получает доступы (раздел 10.3, заголовок «ОС переустановлена»).

### 10.12 Скрипты

Раздел показывается, если `GET /servers/{id}/scripts` вернул непустой список (**OPEN-10**). Кнопки с названиями скриптов, подтверждение «Запустить {script} на {name}?». Запуск через job `run_script`.

### 10.13 Отключить сервер («удаление»)

API удаления нет (раздел 2.4), поэтому это выключение продления.

Экран 1:

```text
Отключить сервер {name}?
Мы перестанем его продлевать. Сервер будет работать до {rent_expires_at}, потом данные могут быть удалены.
Деньги за оплаченный срок не возвращаются.
```

`[Продолжить] [Отмена]`. Экран 2 (второе подтверждение): «Точно отключить {name}? Это нельзя отменить кнопкой, но можно снова включить продление до конца срока.» `[Да, отключить] [Отмена]`. Действие: `servers.cancelled=true`, `auto_renew=false`, job `set_auto_renew(false)` (страховка), аудит. На карточке пометка «отключён» и кнопка `[Включить продление снова]` (снимает `cancelled`).

### 10.14 Баланс и пополнение

Экран баланса:

```text
Баланс: ${balance}

Последние операции:
{date} +$25.00 пополнение
{date} −$8.60 покупка сервера #{id}
{date} −$8.60 продление {name}
```

(до 5 строк). Кнопки: `[Пополнить] [История] [В меню]`.
Пополнение: «Сумма пополнения:» `[$10] [$25] [$50] [$100] [Другая сумма]`; сети: кнопки из `enabled_networks` (напр. `USDT TRC20`, `USDT BEP20`, `TON`, `BTC`), под кнопками текст «Сеть влияет на комиссию перевода: TRC20 обычно дешевле».
Экран счёта:

```text
Счёт #{invoice_id} на ${amount}
Отправь {pay_amount} {asset} ({network}) на адрес:
<code>{address}</code>

Счёт действует до {expires_at}. После подтверждения сети баланс пополнится автоматически.
Отправляй точную сумму и только в указанной сети.
```

Кнопки: `[Открыть страницу оплаты]` (url, если есть), `[Проверить оплату]`, `[Отмена счёта]`. После зачисления сообщение счёта заменяется на «Оплачено: +${credited}».
История: постранично по 10, из `ledger_entries` и `invoices`.

### 10.15 Помощь и язык

Помощь: краткий FAQ (как оплатить, где пароль, как продлить, что если сервер не открывается), ссылки `SUPPORT_URL` и `TERMS_URL`. Кнопка `[Написать в поддержку]` (url). Язык: `[Русский] [English]`, часовой пояс: кнопки `UTC`, `Москва (UTC+3)`, `Киев (UTC+2/+3)`, `Другой (ввод смещения)`.

---

## 11. Админ-панель в боте

Вход: `/admin`. Только `tg_id` из таблицы `admins` (первый owner создаётся миграцией из `OWNER_TG_ID`). Все действия пишутся в `audit_log`. Все опасные действия имеют подтверждение (экран «Точно?» с описанием последствий).

### 11.1 Матрица ролей

| Действие | owner | operator | support |
|---|:---:|:---:|:---:|
| Дашборд, просмотр клиентов и серверов | да | да | да |
| Поиск, просмотр заказов, журнала | да | да | да |
| Заморозка/разморозка сервера, бан/разбан клиента | да | да | нет |
| Ручной запуск sync, retry job, решение по `needs_review` | да | да | нет |
| Ручной заказ за клиента (`admin_purchase`) | да | да | нет |
| Промо-начисление, ручная правка баланса | да | нет | нет |
| Настройки (наценка, лимиты, сети) | да | нет | нет |
| Рассылка | да | нет | нет |
| Управление админами | да | нет | нет |

Отказ по роли: «Недостаточно прав», запись в журнал безопасности.

### 11.2 Дашборд (`adm:home`)

```text
ARIX Cloud · админ

Партнёр: ${partner_balance} (хватает на {covers_days} дн., обязательства 7 дн.: ${obligations7})
Клиентов: {users} (новых за 24ч: {new24})
Серверов: {active} активных · {expiring} скоро истекают · {problem} проблемных
Очередь: {pending} ждут · {running} в работе · окно записи {w}/9
Требуют решения: {needs_review} заказов
Выручка за 30 дн.: ${revenue30} · расход партнёру: ${cost30}
Счета: {pending_invoices} ожидают оплаты
```

Кнопки: `[Клиенты] [Серверы] [Заказы] [Финансы] [Настройки] [Рассылка] [Журнал] [Каталог] [Обновить]`.
Расчёт выручки и расхода из `orders` (`price_usd`, `charged_by_partner_usd`) за статусы `active`. Значения кэшируются в Redis на 60 секунд.

### 11.3 Клиенты (`adm:usr`)

- Поиск (FSM `AdminSearch`): по tg_id, @username, внутреннему id. Список последних 10 клиентов по умолчанию.
- Карточка клиента: id, tg_id, @username, регистрация, последний визит, баланс, число серверов, сумма пополнений, статус (`banned`, `bot_blocked`), ref_source.
- Действия: `[Серверы клиента]`, `[Операции]` (леджер), `[Забанить/Разбанить]` (при бане: вопрос «Заморозить и его серверы?»), `[Начислить бонус]` (owner: FSM сумма → причина → подтверждение → `promo`), `[Правка баланса]` (owner: знак ± , причина → подтверждение → `adjust`), `[Написать клиенту]` (FSM текст → отправка от имени бота, в аудит), `[Заказать сервер за клиента]`.
- `Заказать за клиента`: выбор каталога так же, как у клиента, затем `payment_mode`: `с баланса клиента`, `бонус` (промо-проводка +сумма, затем покупка), `без списаний` (партнёру платим мы, клиенту бесплатно, для переноса пострадавших 17.09). Причина обязательна. Создаётся `orders.kind=admin_purchase`.

### 11.4 Серверы (`adm:srv`)

- Поиск по id, `partner_id`, IP, имени, tg_id клиента. Фильтры: `все`, `истекают ≤ 3 дн.`, `ошибка`, `missing`, `frozen`.
- Карточка: всё из клиентской + `partner_id`, `partner_name`, `synced_at`, `partner_state`, флаги, связанные заказы и последние 5 jobs.
- Действия: `[Принудительный sync]` (одно чтение), `[Заморозить]`/`[Разморозить]` (причина обязательна; при заморозке админ выбирает: только блокировка действий в боте или также `power stop` через партнёра, текст клиенту: «Сервер заморожен из-за нарушения правил. Напишите в поддержку.»), `[Старт/Стоп/Рестарт]`, `[Продлить за клиента]` (`payment_mode` как выше), `[Сбросить пароль]` (новый пароль уходит только клиенту, админ пароли не видит никогда), `[Снять пометку missing]`, `[Сменить владельца]` (owner; для переноса, аудит).
- Заморозка не останавливает биллинг партнёра. Фиксировать в карточке «заморожен с {date} причина {reason}».

### 11.5 Заказы (`adm:ord`)

Списки: `Требуют решения` (`needs_review`, `waiting_partner_funds`), `В работе`, `Последние`. Карточка заказа: параметры, суммы (клиенту/партнёру), попытки, `last_error`, `partner_calls` по job, связанные проводки.
Решение по `needs_review` (owner/operator, с подтверждением):

- `[Проверить у партнёра]` — reconcile: ищет сервер по имени `arix-o{order_id}` (`GET /servers`), если найден, привязывает и переводит в `active`, отправляет доступы через сброс пароля.
- `[Повторить]` — новая job provision с новым `idem_key` (`order-{id}-r{n}`), допустимо только если reconcile ничего не нашёл.
- `[Вернуть деньги клиенту]` — проводка `refund`, статус `refunded`.
- `[Закрыть как выполнено вручную]` — статус `active` без побочных эффектов (для случаев, когда сервер выдан вручную).

### 11.6 Финансы (`adm:fin`)

Баланс партнёра (по запросу, чтение), обязательства, «хватает на N дней»; таблица счетов (последние 20 со статусами, `partial` подсвечены); последние проводки; сверка `ledger_reconcile` (дата и результат); сводка за 7/30 дней (пополнения, покупки, продления, возвраты, расход партнёра, маржа = продажи − расход).

### 11.7 Настройки (`adm:set`)

Список ключей из таблицы `settings` (раздел 5) с текущими значениями. Изменение через FSM: валидация типа и диапазона (`markup ≥ 1.0`, `gateway_fee` 0–0.2, `price_step` > 0, `min_topup < max_topup`), подтверждение с показом «было → станет», запись, аудит, сброс кэша настроек (`settings_store` кэширует на 30 секунд). Смена `markup` не меняет цены уже оплаченных заказов и уже поставленных в очередь продлений (цена фиксируется в заказе), влияет на новые расчёты.
Включение/выключение сетей оплаты (`enabled_networks`), режим `maintenance` — отдельными переключателями.

### 11.8 Рассылка (`adm:bc`)

Owner. FSM: текст (HTML, длина ≤ 3500) → аудитория (`все`, `у кого есть активные серверы`, `по tg_id списком`) → превью себе → подтверждение с числом получателей. Отправка фоновой задачей `broadcast`: не более 20 сообщений в секунду, пропуск `bot_blocked` и `banned`, при `TelegramForbiddenError` помечать `bot_blocked`. Прогресс и итог (отправлено/ошибок) в админ-чат. Кнопка `[Остановить]` во время отправки. Каждая рассылка в `audit_log` (текст, аудитория, счётчики).

### 11.9 Журнал (`adm:aud`)

Последние 20 записей `audit_log` с фильтром по актору, объекту, действию; постранично. Показывать без `before/after` в списке, детали по нажатию. Пароли и секреты в аудите не хранятся (правило 5).

### 11.10 Каталог и цены (`adm:cat`)

Таблица по локации: тариф, партнёрская цена месяца, клиентская цена месяца, наценка фактическая, для 1/3/6/12 мес. Кнопка `[Обновить каталог]` (сброс кэша, одно чтение). Помечать красным тарифы, где фактическая наценка < 1.5 из-за округления/скидок (значит, что-то не так).

---

## 12. Уведомления клиентам и админам

Отправка через `jobs/notify.py`: очередь сообщений с ограничением ≤ 25 сообщений/сек (лимит Telegram 30), повтор при `RetryAfter`, дедупликация по таблице `notifications (user_id, key, ref)`. Уведомления не идут забаненным (кроме отмеченных «всем»), заблокировавшим бота помечаются и пропускаются.

### 12.1 Клиентам

| Ключ | Когда | Дедуп (`key:ref`) | Текст (кратко) |
|---|---|---|---|
| `server_ready` | сервер создан | `server_ready:{server_id}` | Раздел 10.3 |
| `provision_delay` | > `provision_delay_notify_min` без результата | `provision_delay:{order_id}` | «Создание идёт дольше обычного…» |
| `order_failed_refund` | заказ `failed → refunded` | `order_failed:{order_id}` | «Не удалось создать сервер. Вернули ${price} на баланс.» |
| `reminder_3d` | за 3 дня до конца | `remind3:{server_id}:{end_date}` | «{name}: осталось 3 дня. Автопродление {вкл/выкл}. Баланс {хватает/не хватает}.» |
| `reminder_1d` | за 1 день | `remind1:{server_id}:{end_date}` | то же, срочный тон |
| `renewed_auto` | автопродление прошло | `renewed:{order_id}` | «Продлили {name} до {date}, списано ${price}.» |
| `renew_failed_no_funds` | автопродление без денег | `renewfail:{server_id}:{date}` | «Не хватило ${x} для продления {name}. Пополните баланс.» |
| `expired` | срок истёк | `expired:{server_id}:{end_date}` | «Срок {name} истёк. Продлите как можно скорее: данные хранятся по правилам партнёра.» + кнопка продления |
| `topup_ok` | пополнение | `topup:{invoice_id}` | «Баланс пополнен на ${credited}.» |
| `invoice_expired` | счёт истёк без оплаты | `inv_exp:{invoice_id}` | «Счёт #{id} истёк. Если вы уже отправили платёж, напишите в поддержку.» |
| `server_frozen` / `server_unfrozen` | админ заморозил/снял | `frozen:{server_id}:{ts}` | Причина и как обратиться |
| `reinstall_done` | ОС переустановлена | `reinstall:{order_id}` | Раздел 10.11 |
| `server_error` | у сервера `error` дольше 10 минут | `err:{server_id}:{date}` | «Сервер в состоянии ошибки, мы уже проверяем. Можно попробовать рестарт.» |
| `server_missing` | пропал у партнёра (админам сразу, клиенту после решения) | — | не отправляется автоматически |
| `broadcast` | рассылка | — | без дедупа |

### 12.2 Админам (в `ADMIN_CHAT_ID` и лично owner для критичных)

| Ключ | Когда | Дедуп | Критичность |
|---|---|---|---|
| `partner_balance_low` | баланс партнёра < порога или обязательства | 6 часов | высокая |
| `partner_insufficient_funds` | job получил `402` | 30 минут | высокая |
| `partner_auth_error` | `401/403/account_banned` | 30 минут | критичная (все записи стоят) |
| `partner_unreachable` | ≥ 5 подряд ошибок сети/5xx | 15 минут | высокая |
| `order_needs_review` | заказ в `needs_review` | по заказу | высокая |
| `set_auto_renew_failed` | не удалось выключить автопродление у партнёра | по серверу | высокая (риск списаний) |
| `invoice_partial` / `webhook_bad_signature` (пачка) | недоплата/подделка | 1 час | средняя |
| `ledger_mismatch` | сверка расходится | сутки | критичная |
| `queue_backlog` | очередь `pending` > 20 или самая старая задача > 10 мин | 30 минут | средняя |
| `server_missing` | сервер пропал из списка партнёра | по серверу | высокая |
| `new_client_first_purchase` | первая покупка нового клиента (информ.) | по клиенту | низкая, можно отключить |
| `error_spike` | > 20 ошибок обработчиков за 5 минут | 15 минут | высокая |
| `backup_failed` | не выполнился бэкап | сутки | высокая |

Формат админ-алерта: `[ВЫСОКАЯ] partner_balance_low · баланс $12.30, обязательства 7 дн. $44.10 · Пополни партнёра` — коротко, с рекомендацией действия и без секретов.

---

## 13. Безопасность и защита от злоупотреблений

### 13.1 Секреты

- `PARTNER_API_KEY`, `BOT_TOKEN`, `FERNET_KEY`, ключи шлюза лежат только в переменных окружения / secret-хранилище хостинга. В репозитории только `.env.example` без значений. `.env` в `.gitignore`.
- Ключ партнёра отправляется только по HTTPS (проверка на старте, раздел 2.1). Логи клиента не содержат заголовков `Authorization`.
- Рекомендация владельцу: отдельный ключ партнёра только для бота, ротация при подозрении, ключ никогда не отправляется в чаты и не вставляется в промпты Cursor (в Cursor подставляется только через `.env` локально).
- Если бот получил `401 token_invalid`, диспетчер останавливает все записи (circuit breaker `partner_writes_paused` в Redis) до ручного снятия админом.

### 13.2 Пароли серверов

Правила из раздела 7.4.1 обязательны. Проверяемые инварианты:

1. Пароль есть только: в ответе партнёра в памяти процесса, в Redis под ключом `secret:{uuid}` (Fernet, TTL 15 минут), в сообщении Telegram с `protect_content=True`.
2. Сообщение с паролем удаляется через 10 минут (Redis ZSET `tg:delete_queue`, обрабатывает `worker`). Если удалить не удалось (сообщение старше 48 часов или бот заблокирован), это логируется без содержимого.
3. Ни в одной таблице, `jobs.payload`, `partner_calls`, `audit_log`, Sentry (`before_send` вычищает поля `password`, `secret`, `token`, `authorization`), нет паролей.
4. Тест: структурный логгер пропускает через `redact()` любое значение под ключами из чёрного списка и строки, похожие на пароль в теле ответа партнёра.

### 13.3 Контроль доступа

- Клиент оперирует только своими объектами: проверка владельца в каждом хендлере, а не в UI. Тест: попытка `srv:open:{чужой_id}` возвращает «Не найдено».
- Админ определяется таблицей `admins`, не `username`. Изменение админов только owner, действие в аудите.
- Опасные админ-операции (промо, adjust, смена владельца сервера, рассылка, настройки) требуют экрана подтверждения. Для `adjust`/`promo` выше `$100` требуется повторное подтверждение вводом слова `ДА` (FSM).
- Все админ-ответы с персональными данными отправляются только в личку админу, не в общий `ADMIN_CHAT_ID`.

### 13.4 Антиспам и лимиты (Redis, скользящее окно)

| Что | Лимит | Реакция |
|---|---|---|
| Апдейты от одного пользователя | 20 за 10 секунд | игнор + «Слишком часто» один раз, при повторе бан на 60 секунд в Redis |
| Нажатия дорогих кнопок (`buy:go`, `srv:*_go`, `bal:topup`) | 1 за 3 секунды | `answer_callback_query` |
| Сброс пароля | 3 в час на сервер | сообщение с временем ожидания |
| Создание счетов | 5 `pending` на пользователя, 10 в сутки | отказ с текстом |
| Новые клиенты | `new_user_daily_purchases` за первые 24 часа, `max_servers_per_user` в целом | отказ, подсказка «Напишите в поддержку для увеличения лимита» |
| Вебхуки (по IP) | 60 в минуту | 429 |
| Ошибки подписи вебхука | 5 в минуту | временная блокировка IP на 10 минут на уровне Caddy/приложения |

### 13.5 Защита от DDoS и атак на сервис

- Telegram-бот работает по вебхуку за Caddy/обратным прокси. Открыт только 443. `/tg/{secret}` проверяет секретный путь **и** заголовок `X-Telegram-Bot-Api-Secret-Token`. Порты Postgres и Redis наружу не публикуются.
- Публичные HTTP-маршруты: `/tg/{secret}`, `/webhooks/pay/{secret}`, `/health` (ограничен по IP или без деталей для внешних). Остальные 404.
- Ограничение размера тела запроса 64 КБ на всех маршрутах, таймауты чтения тела 5 секунд.
- Сервер бота за Cloudflare или аналогом (по желанию владельца), либо вебхуки принимаются только с адресов шлюза/Telegram (для Telegram это диапазоны подсетей, документировано, проверка по желанию).
- DDoS на клиентские серверы это зона партнёра (в оферте это прописывается как ограничение ответственности, раздел 18).

### 13.6 Abuse-политика

- В оферте: запрещены рассылка спама, сканирование сетей, DDoS, майнинг (если запрещён партнёром), распространение незаконного контента, фишинг. Список синхронизируется с правилами партнёра (**OPEN-12**).
- Поток жалобы: жалоба приходит владельцу → админ открывает карточку сервера → `[Заморозить]` (причина) → при необходимости остановка через партнёра → уведомление клиенту → ответ заявителю. Все шаги в аудите.
- Заморозка не даёт удалить данные: сохраняем сервер, пока не решится дело.
- Заказ нового сервера клиентом с флагом `banned` невозможен.

### 13.7 Данные и приватность

- В БД только то, что нужно: `tg_id`, `username`, `first_name`, деньги, серверы. Никаких паспортных данных и KYC (у партнёра и у нас).
- Логи хранятся 14 дней, `partner_calls` 14 дней, `audit_log` бессрочно (без секретов).
- Запрос клиента на удаление данных: анонимизация `users` (`username`, `first_name` → null, `tg_id` → хэш) при отсутствии активных серверов и нулевом балансе; леджер остаётся (финансовая отчётность).
- Бэкап БД: ежедневный `pg_dump`, шифрование (age или gpg), выгрузка в другое облако (S3-совместимое, не тот же провайдер, что сервер бота). Хранение 14 ежедневных + 8 еженедельных. Восстановление проверяется вручную минимум раз в месяц (чек-лист в разделе 18). Урок 17.09: бэкап не на том же аккаунте/провайдере.

### 13.8 Устойчивость (что делать при сбоях)

| Сбой | Поведение |
|---|---|
| Упал `worker` | Задачи остаются в БД, после рестарта продолжатся; зависшие `running` старше 5 минут возвращаются в `pending` (кроме записей с неизвестным исходом, они идут через reconcile) |
| Упал `bot`, `worker` жив | Очередь и оплаты работают, вебхуки шлюза ждут повторной доставки шлюзом, поллер страхует |
| Партнёр недоступен | Записи в backoff, клиентам показывается кэш, алерт `partner_unreachable`; заказы не теряются |
| Потеряна Redis | FSM и одноразовые секреты пропадают (клиент получит «получить новый пароль»), деньги и заказы не затронуты (всё в Postgres) |
| Потеряна Postgres | Восстановление из бэкапа; после восстановления запустить `scripts/reconcile_partner.py` (сверка серверов с партнёром по `arix-o{id}` и ручное решение по расхождениям) |
| Двойная доставка вебхука шлюза | Идемпотентность по `uniq_key topup:{id}` |
| Два экземпляра `worker` по ошибке | Лидер по `pg_advisory_lock`, второй простаивает |

---

## 14. Наблюдаемость и эксплуатация

### 14.1 Логи

`structlog` в JSON. Обязательные поля: `ts`, `level`, `event`, `request_id`, `tg_id` (если есть), `job_id`, `order_id`, `server_id`, `duration_ms`. Уровни: `INFO` для действий и переходов состояния, `WARNING` для повторов и лимитов, `ERROR` для исключений. Пароли, токены, ключи вычищаются процессором `redact()`.

### 14.2 Метрики (по желанию M8)

Prometheus-эндпоинт `/metrics` только на внутреннем интерфейсе (не публиковать). Счётчики и датчики: `partner_calls_total{method,status}`, `partner_write_window_used`, `jobs_pending`, `jobs_failed_total`, `orders_by_status`, `provision_duration_seconds`, `topup_total_usd`, `webhook_invalid_total`, `notify_queue_depth`. Если Prometheus не нужен, достаточно раздела «Финансы» и «Очередь» в админке и `/health`.

### 14.3 Команды владельца (CLI/скрипты)

| Скрипт | Что делает |
|---|---|
| `scripts/capture_fixtures.py` | снимает и санитизирует ответы партнёра (раздел 2.6) |
| `scripts/reconcile_partner.py` | сравнивает `servers` с `GET /servers`, выводит расхождения, ничего не меняет без флага `--apply` |
| `scripts/seed_dev.py` | тестовые данные для dev |
| `scripts/backup.sh` / `restore_check.sh` | бэкап и проверка восстановления |
| `scripts/import_affected_clients.py` | импорт списка пострадавших 17.09 (tg_id, сервер, срок компенсации) в таблицу `admin_purchase`-заявок (формат CSV описан в разделе 18.2) |

### 14.4 Эксплуатационный ритм

Ежедневно: проверка алертов, баланс партнёра (дашборд), очередь `needs_review`. Еженедельно: сверка выручки и расхода партнёру, просмотр `missing`. Ежемесячно: проверка восстановления бэкапа, ротация ключей при необходимости, пересмотр `markup` относительно цен партнёра.

---

## 15. Тесты и критерии приёмки

### 15.1 Обязательные автотесты

**Деньги и цены (unit):**

- `client_price`: округление вверх с шагом 0.10; примеры: стоимость 4.00 при markup 2.0, fee 0.02 → 8.20; не ниже `cost × 2`; Decimal, без float.
- `post_entry`: идемпотентность (второй вызов с тем же `uniq_key` не меняет баланс), отказ при уходе в минус, конкурентные вызовы (10 параллельных списаний, баланс не уходит в минус).
- Сверка `ledger_reconcile` находит искусственное расхождение.

**Лимитер и диспетчер (integration, fake partner):**

- 50 заказов одновременно: за любое скользящее окно 60 секунд не более 9 записей в `partner_calls` (method ∈ POST/PATCH).
- Квоты классов: purchase не съедает окно полностью, `manage` и `renew` получают свои слоты; work-conserving при пустых очередях.
- Ответ `429` с `Retry-After` приостанавливает записи на нужное время.
- Падение процесса посреди job (kill): после рестарта job не дублирует покупку (`Idempotency-Key`), сервер не создаётся дважды.
- Неоднозначный таймаут `POST /servers`: reconcile по `arix-o{order_id}` находит созданный сервер и не создаёт второй.
- Исчерпание повторов → `needs_review`, деньги остаются списанными, алерт отправлен.

**Партнёрский адаптер (respx + фикстуры):**

- Разбор всех ответов, `error` объекта → типизированные исключения, парсинг `429`.
- `http://` для не-localhost в `PARTNER_BASE_URL` → ошибка старта.
- Заголовок `Idempotency-Key` передаётся, `Idempotent-Replay` обрабатывается как успех.
- Contract-тесты по реальным фикстурам (раздел 2.6).

**Платежи:**

- Вебхук с неверной подписью → 401, баланс не тронут.
- Верная подпись → зачисление один раз, повторная доставка → без изменений.
- `partial` не зачисляется автоматически; `paid` после `expired` зачисляется с пометкой.
- Поллер зачисляет, если вебхук потерян.
- Сумма зачисления берётся из подтверждённого `get_invoice`, а не из тела вебхука.

**Безопасность:**

- Чужой `server_id` в любом `srv:*` → отказ без утечки существования.
- Пользователь-не-админ нажимает `adm:*` (подделка callback) → отказ.
- Пароль не попадает в логи (перехват логгера в тесте выдачи доступов), не пишется в БД (SELECT по всем текстовым колонкам после выдачи не находит пароль).
- Rate-limit по апдейтам и кнопкам.

**Бот (aiogram, тесты на фейковом `Bot`):**

- Полный сценарий покупки: `/start` → оферта → купить → локация → тариф → ОС → срок → оплатить → `server_ready` + защищённый пароль → удаление сообщения через 10 минут (freezegun).
- Недостаток баланса → экран пополнения → оплата (fake) → возврат к черновику → покупка.
- Продление вручную и автопродление; напоминания за 3 и 1 день ровно один раз.
- Отключить сервер (два подтверждения) → `cancelled`, продления нет.
- Переустановка с вводом имени.

### 15.2 Приёмка по этапам (главное)

| Критерий | Проверка |
|---|---|
| Деньги никогда не теряются | Тест-сценарии отказа партнёра на каждом шаге заканчиваются либо `active`, либо `refunded`, либо `needs_review` с алертом. Баланс = сумма леджера |
| Никаких дублей серверов | Повторы job и рестарты не создают второй сервер по одному заказу |
| Лимит записей не нарушен | Тест 50 заказов, окно ≤ 9 |
| Пароли не утекают | Тесты раздела 13.2 |
| Клиент понимает, что происходит | Каждый статус и ошибка из разделов 6, 10, 12 имеют текст, нет «сырых» исключений в чате |
| Админ видит проблемы | Алерты раздела 12.2 приходят в тесте на каждый сценарий |
| Ручной прогон на реальном API | Один заказ на самом дешёвом тарифе, продление на 2 дня, рестарт, смена пароля, переустановка. Результат: фикстуры в `tests/fixtures/partner/` |

### 15.3 Ручной чек-лист перед первыми клиентами

1. Реальный HTTPS-адрес партнёра прописан, `capture_fixtures.py` выполнен, контракт-тесты зелёные.
2. Шлюз настроен, тестовый платёж на минимальную сумму зачислен, вебхук и поллер проверены.
3. Отключено автопродление у партнёра для тестового сервера (`set_auto_renew(false)` прошёл).
4. Бэкап сделан и восстановлен на другой машине.
5. Оферта и правила размещения опубликованы (`TERMS_URL`).
6. Админ-алерты дошли в `ADMIN_CHAT_ID`.
7. Баланс у партнёра пополнен на сумму, покрывающую ближайшие заказы и продления.

---

## 16. Этапы разработки (M0–M8)

Даты ориентировочные (старт 21.09.2026), важнее порядок и критерий готовности.

| Этап | Срок | Содержание | Готово, когда |
|---|---|---|---|
| **M0** | Пн 21 | Получить HTTPS-адрес, снять фикстуры (`capture_fixtures.py`), ответить на OPEN-1..3; репозиторий, `.cursor/rules`, CI (ruff, mypy, pytest) | Фикстуры лежат в репо, контракт-тесты видят их |
| **M1** | Вт 22 (утро) | Каркас, миграция `0001_init`, конфиг, `money`, `pricing`, `ledger`, `PartnerAdapter` (Tihost + fake), каталог, `/start`, меню, оферта-гейт | Тесты денег и адаптера зелёные, меню открывается, каталог показывается с ценами |
| **M2** | Вт 22 | Покупка с баланса: draft-flow, диспетчер и лимитер, job `provision`, выдача доступов (10.3), reconcile | Сценарий покупки проходит на fake и на реальном партнёре (один сервер) |
| **M3** | Вт 22 (вечер) | «Мои серверы», карточка, питание, sync_servers, `display_status`, отключить сервер | Список и карточка живые, старт/стоп работают, статусы корректны |
| **M4** | Вт вечер – Ср | Платежи: интерфейс, выбранный шлюз, вебхук, поллер, экран пополнения | Реальный тестовый платёж зачисляется, повторная доставка без дубля |
| **M5** | Ср–Чт | Продление ручное и авто, напоминания, алерты баланса партнёра, `obligations` | Продление и автопродление проходят, напоминания раз, алерты приходят |
| **M6** | Ср | Админка: дашборд, клиенты, серверы, заказы, финансы, журнал, заморозка, admin_purchase | Все действия по матрице ролей работают и пишутся в аудит |
| **M7** | Чт | Смена пароля, переустановка, скрипты, переименование, EN, настройки, рассылка | Все клиентские экраны раздела 10 реализованы |
| **M8** | Пт | Укрепление, нагрузочный тест очереди, бэкап и проверка восстановления, ручной чек-лист 15.3, `import_affected_clients.py` | Чек-лист зелёный |

**Ворота запуска (не код):** оферта и правила размещения готовы до первых внешних клиентов (владелец). Без них бот работает только на владельца и доверенных.

**Запасной план по срокам:** если M4 (шлюз) сдвигается на среду, во вторник бот работает с ручным пополнением: админ начисляет баланс через `adjust`/`promo` после подтверждения платежа владельцем вручную. Код покупки при этом не меняется.

**Перенос пострадавших 17.09 (параллельно, среда):** через админку `admin_purchase` с `payment_mode=admin` (без списаний у клиента, сервер оплачивает владелец) на список клиентов; данные клиента восстанавливаются владельцем вручную из бэкапов/репозиториев после выдачи сервера. Бот в этом не участвует, кроме создания сервера и выдачи доступов.

---

## 17. Допущения и открытые вопросы владельца

| ID | Вопрос | Кто решает | По умолчанию |
|---|---|---|---|
| OPEN-11 | Какой крипто-шлюз: Cryptomus, CryptoCloud, BTCPay | владелец | до решения используется `FakePaymentGateway`; для быстрого старта Cryptomus/CryptoCloud (хостируемые), BTCPay требует своего сервера |
| OPEN-12 | Оферта и правила размещения; список запрещённого, согласованный с правилами Tihost | владелец (текст оферты готовит Claude по запросу) | ворота запуска |
| OPEN-13 | Имя бота, домен для вебхуков, домен ARIX | владелец | `PUBLIC_BASE_URL` |
| OPEN-14 | Данные пострадавших 17.09: где бэкапы, репозитории, список клиентов, срок компенсации | владелец | без этого перенос только «пустых» серверов |
| OPEN-15 | Конкурентность цен: наценка ×2 к ценам партнёра может быть выше рынка | владелец | `markup` настраивается в админке, стартово 2.0 |
| OPEN-16 | Второй (запасной) партнёр | владелец | Phase 2, интерфейс `PartnerAdapter` это допускает |
| OPEN-17 | Поддержка: контакт, часы, кто отвечает на жалобы | владелец | `SUPPORT_URL` |
| OPEN-18 | Продавать ли Windows и тарифы с доп. лицензиями | владелец | показываем ОС, что вернул каталог, с пометкой о цене |

Принятые допущения, которые можно менять только осознанно:

- Покупка только с баланса, счетов «под заказ» нет (упрощает гонки и возвраты).
- Один партнёр (Tihost), один валютный контур USD.
- Цена клиенту фиксируется в момент проводки, последующее изменение наценки на неё не влияет.
- Мультитенантности и веб-панели в MVP нет.
- «Удаление» сервера отсутствует, есть «отключить» (без возврата денег).

---

## 18. Приложения

### 18.1 Глоссарий

| Термин | Значение |
|---|---|
| Партнёр | Tihost, поставщик серверов по API |
| Заказ (`orders`) | действие клиента, приводящее к записи у партнёра: покупка, продление, переустановка |
| Job | единица фоновой работы, содержит одну запись к партнёру |
| Окно лимитера | скользящие 60 секунд, в них не более `PARTNER_WRITE_LIMIT` записей |
| Леджер | неизменяемый журнал денежных проводок, `users.balance_usd` его кэш |
| Reconcile | сверка: найти у партнёра сервер по имени `arix-o{order_id}` |
| Ворота | условие запуска, не связанное с кодом (оферта и правила) |

### 18.2 Формат импорта пострадавших (`import_affected_clients.py`)

CSV с заголовком: `tg_id,username,location,plan_id,os_id,months,note`. Скрипт для каждой строки создаёт заявку (не заказ!) для ручного подтверждения в админке: владелец проверяет и нажимает «Выполнить». Строка с несуществующим `tg_id` создаёт пользователя-заглушку (`users` без `first_name`), который станет активным при первом `/start`. Скрипт ничего не заказывает у партнёра сам.

### 18.3 Чек-лист восстановления бэкапа (ежемесячно)

1. Скачать последний дамп из хранилища в другом облаке.
2. Расшифровать, развернуть в чистый Postgres на другой машине.
3. Запустить `alembic current`, сравнить число строк в `users`, `servers`, `ledger_entries` с продом.
4. Выполнить `scripts/reconcile_partner.py` (без `--apply`) против восстановленной БД и реального API (только чтение).
5. Записать дату и результат.

### 18.4 Что попросить у партнёра (для владельца)

Если ответы на OPEN-1..10 ещё не получены, отправить в @tihost_support вопросы: боевой HTTPS-адрес, форма ответа смены пароля, цены продления в `GET /servers/{id}`, ответ переустановки, продление после `rent_expired`, срок хранения данных после конца аренды, выделенный IPv4, повышение лимита записи, поле IP при покупке, состав скриптов. Дополнительно спросить: оптовые цены при обороте, ресайз/метрики в roadmap, вебхуки, и есть ли SLA и компенсация при сбоях.

### 18.5 Быстрый старт для Cursor

```text
1. Создать репозиторий, положить этот файл в docs/TZ.md, правила из 0.1 в .cursor/rules/arix.mdc.
2. Промпт первому агенту: «Прочитай docs/TZ.md разделы 0–7. Создай каркас проекта по 3.3 и миграцию 0001_init по разделу 5. Ничего сверх этого. Добавь тесты money/pricing/ledger по разделу 4 и 15.1.»
3. Следующие промпты по одному этапу: «Реализуй этап M2 из раздела 16. Следуй разделам 6, 7, 10.2, 10.3. Покажи, какие тесты добавил.»
4. После каждого этапа: ruff, mypy, pytest, ручной прогон на fake-партнёре, затем один реальный вызов на дешёвом тарифе.
```