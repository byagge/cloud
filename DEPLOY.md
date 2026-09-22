# Деплой ARIX Cloud **без Docker**

Изоляция от других проектов на том же сервере:

| Что | Как |
|-----|-----|
| Каталог | только `/opt/arix` (не трогаем чужие `/var/www`, `/opt/...`) |
| Python | свой `.venv` внутри `/opt/arix` |
| БД | SQLite в `/opt/arix/data/` (не общий Postgres) |
| Redis | `memory://` + embedded worker (отдельный Redis не нужен) |
| HTTP | `127.0.0.1:18080` — не занимает чужой `:80` / `:8080` |
| systemd | юниты `arix-bot.service` |

---

## 1. Залить код

С локальной машины:

```bash
rsync -avz \
  --exclude '.venv' --exclude 'data' --exclude '.git' \
  --exclude '_tmp_emoji' --exclude '.pytest_cache' --exclude '__pycache__' \
  ./ user@SERVER:/opt/arix/
```

Или на сервере: `git clone … /opt/arix`.

```bash
ssh user@SERVER
sudo mkdir -p /opt/arix
sudo chown "$USER:$USER" /opt/arix
cd /opt/arix
```

---

## 2. Python 3.12+ и venv

```bash
cd /opt/arix

# Ubuntu/Debian, если нет 3.12:
# sudo apt update && sudo apt install -y python3.12 python3.12-venv python3.12-dev

python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
# для SQLite на проде:
pip install aiosqlite
```

---

## 3. `.env` (прод без Docker)

```bash
cp .env.production.example .env
nano .env
chmod 600 .env
```

Минимальный рабочий набор:

```env
ENV=prod
BOT_TOKEN=...
BOT_MODE=polling
OWNER_TG_ID=...
ADMIN_CHAT_ID=...

DATABASE_URL=sqlite+aiosqlite:///./data/arix.db
REDIS_URL=memory://
EMBEDDED_WORKER=true

HTTP_HOST=127.0.0.1
HTTP_PORT=18080

USE_FAKE_PARTNER=false
PARTNER_BASE_URL=https://...
PARTNER_API_KEY=...

FERNET_KEY=...   # сгенерировать ниже
SUPPORT_URL=https://t.me/arxixx
TERMS_URL=https://...
DEFAULT_LANG=ru
```

Fernet:

```bash
source .venv/bin/activate
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Проверка порта:

```bash
ss -tlnp | grep 18080 || echo "18080 free"
# если занят — смени HTTP_PORT в .env (например 18081)
```

---

## 4. Ручной smoke-тест

```bash
cd /opt/arix
source .venv/bin/activate
mkdir -p data
python -m app.bot.main
```

В другом SSH:

```bash
curl -sS http://127.0.0.1:18080/health
```

В Telegram: `/start`. Остановка: `Ctrl+C`.

---

## 5. systemd (автозапуск)

Создать системного пользователя (один раз) **или** в юните поставить своего `User=`:

```bash
sudo useradd --system --home /opt/arix --shell /usr/sbin/nologin arix 2>/dev/null || true
sudo chown -R arix:arix /opt/arix
```

```bash
sudo cp /opt/arix/deploy/systemd/arix-bot.service /etc/systemd/system/
# если юзер не arix: sudo nano /etc/systemd/system/arix-bot.service  → User=/Group=
sudo systemctl daemon-reload
sudo systemctl enable --now arix-bot
sudo systemctl status arix-bot
journalctl -u arix-bot -f
```

Обновление:

```bash
cd /opt/arix
# git pull  или  rsync
source .venv/bin/activate
pip install -e .
pip install aiosqlite
sudo systemctl restart arix-bot
curl -sS http://127.0.0.1:18080/health
```

Стоп только ARIX:

```bash
sudo systemctl stop arix-bot
# disable: sudo systemctl disable arix-bot
```

---

## 6. Бэкап SQLite

```bash
cp /opt/arix/data/arix.db ~/arix-$(date +%F).db
# или
sqlite3 /opt/arix/data/arix.db ".backup '/home/$USER/arix-$(date +%F).db'"
```

---

## 7. Опционально: свой Postgres / Redis

Только если **осознанно** поднимаешь отдельные БД и **не** шаришь чужие инстансы:

- создай **отдельную** БД/юзера `arix` (не пиши в чужую БД)
- `DATABASE_URL=postgresql+asyncpg://arix:PASS@127.0.0.1:5432/arix`
- для отдельного worker: реальный Redis + `EMBEDDED_WORKER=false` + юнит `arix-worker.service`

По умолчанию этого не нужно — SQLite + embedded worker достаточно.

---

## 8. Webhook (опционально)

`BOT_MODE=webhook`, `PUBLIC_BASE_URL=https://bot.domain.com`, в nginx отдельный `server_name` → `proxy_pass http://127.0.0.1:18080`. Чужие сайты не трогать.

---

## Чеклист

- [ ] Каталог `/opt/arix`, свой `.venv`
- [ ] SQLite только в `/opt/arix/data/`
- [ ] `HTTP_HOST=127.0.0.1`, свободный `HTTP_PORT` (18080)
- [ ] `chmod 600 .env`
- [ ] Юнит `arix-bot` — не править чужие systemd-сервисы
- [ ] Не ставить пакеты в system Python чужих проектов
