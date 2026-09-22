# Деплой ARIX Cloud на сервер (рядом с другими проектами)

Изоляция от чужих сервисов:

| Что | Как |
|-----|-----|
| Compose project | `name: arix` → контейнеры `arix-*` |
| Postgres / Redis | **без** публикации портов на хост |
| Health HTTP | только `127.0.0.1:18080` (не занимает чужой `:8080`) |
| Volumes / network | `arix_pgdata`, `arix_redisdata`, `arix_net` |

Не трогает чужие `postgres`/`redis` на 5432/6379 и другие compose-проекты.

---

## 0. На своей машине (подготовка)

```bash
cd /path/to/hosting

# секреты не в git
cp .env.production.example .env
# отредактируй .env: BOT_TOKEN, OWNER_TG_ID, POSTGRES_PASSWORD, FERNET_KEY, PARTNER_*

# Fernet:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Залей код на сервер (пример — отдельная папка, не в корень чужих проектов):

```bash
# с локальной машины
rsync -avz --exclude '.venv' --exclude 'data' --exclude '.git' --exclude '_tmp_emoji' \
  ./ user@SERVER:/opt/arix/
# .env лучше скопировать отдельно (не светить в истории shell):
scp .env user@SERVER:/opt/arix/.env
```

Или: `git clone` в `/opt/arix` и создать `.env` на сервере из `.env.production.example`.

---

## 1. На сервере — один раз

```bash
ssh user@SERVER
sudo mkdir -p /opt/arix
sudo chown "$USER:$USER" /opt/arix
cd /opt/arix

# Docker + Compose plugin уже должны быть (как для других проектов)
docker --version
docker compose version

# Проверь, что порты НЕ конфликтуют
ss -tlnp | grep -E ':18080|:5432|:6379' || true
# 5432/6379 у других проектов на хосте — ок: наш Postgres/Redis наружу не торчат
# Если 18080 занят — в .env поставь ARIXX_HTTP_PORT=18081 (или другой свободный)
```

Заполни `/opt/arix/.env` (из `.env.production.example`).

Обязательно:

- `BOT_TOKEN`, `OWNER_TG_ID`
- `POSTGRES_PASSWORD` (сильный)
- `FERNET_KEY`
- `USE_FAKE_PARTNER=false` + реальные `PARTNER_BASE_URL` / `PARTNER_API_KEY` (или временно `true` для smoke-теста)
- `EMBEDDED_WORKER=false` (в compose уже так)

---

## 2. Запуск (не затрагивает другие compose)

```bash
cd /opt/arix

# Явный project name на случай старого compose без `name:`
export COMPOSE_PROJECT_NAME=arix

docker compose pull   # образы postgres/redis
docker compose up -d --build

docker compose ps
curl -sS http://127.0.0.1:18080/health
docker compose logs -f --tail=100 bot worker
```

Ожидаемый health: `{"db":"ok","redis":"ok",...}`.

В Telegram: `/start` → оферта → `/admin`.

---

## 3. Обновление кода

```bash
cd /opt/arix
# git pull   # или rsync снова
export COMPOSE_PROJECT_NAME=arix
docker compose up -d --build
docker compose logs -f --tail=50 bot
```

Данные БД в volume `arix_pgdata` сохраняются между ребилдами.

---

## 4. Остановка / удаление только ARIX

```bash
cd /opt/arix
export COMPOSE_PROJECT_NAME=arix

# стоп контейнеров, volumes оставить
docker compose down

# полный снос включая БД (осторожно)
# docker compose down -v
```

Чужие проекты в других каталогах (`docker compose` там) не затрагиваются.

---

## 5. Полезные команды

```bash
docker compose -p arix ps
docker compose -p arix logs -f bot
docker compose -p arix exec bot python -c "from app.config import get_settings; print(get_settings().env)"
docker compose -p arix restart bot worker
```

Бэкап Postgres:

```bash
docker compose -p arix exec -T postgres \
  pg_dump -U arix arix | gzip > ~/arix-backup-$(date +%F).sql.gz
```

---

## 6. Webhook (опционально)

Если нужен webhook вместо polling:

1. В `.env`: `BOT_MODE=webhook`, `PUBLIC_BASE_URL=https://bot.yourdomain.com`
2. Проксируй с nginx/caddy на `127.0.0.1:18080` (только этот vhost)
3. Не вешай на порт, который уже занят другим сайтом — reverse-proxy по `server_name`

---

## Чеклист «не задеть чужое»

- [ ] Каталог отдельно: `/opt/arix` (не `/var/www` чужого сайта)
- [ ] `COMPOSE_PROJECT_NAME=arix` / `name: arix` в compose
- [ ] Postgres/Redis **без** `ports:` на хост
- [ ] HTTP только `127.0.0.1:18080` (или свой свободный порт)
- [ ] Свои volumes `arix_*`, своя сеть `arix_net`
- [ ] `.env` не в git, права `chmod 600 .env`
