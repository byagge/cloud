# Деплой без Docker — `/opt/cloud`, User=root

## На сервере

```bash
cd /opt/cloud

python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt

cp .env.production.example .env
nano .env          # BOT_TOKEN, OWNER_TG_ID, FERNET_KEY, …
chmod 600 .env
mkdir -p data

# проверка
python -m app.bot.main
# curl http://127.0.0.1:18080/health
```

Fernet-ключ:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Минимум в `.env`:

```env
ENV=prod
BOT_TOKEN=...
OWNER_TG_ID=...
DATABASE_URL=sqlite+aiosqlite:///./data/arix.db
REDIS_URL=memory://
EMBEDDED_WORKER=true
HTTP_HOST=127.0.0.1
HTTP_PORT=18080
USE_FAKE_PARTNER=true
FERNET_KEY=...
```

## systemd

```bash
cp /opt/cloud/deploy/systemd/arix-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now arix-bot
journalctl -u arix-bot -f
```

Обновление:

```bash
cd /opt/cloud
source venv/bin/activate
pip install -r requirements.txt
systemctl restart arix-bot
```

Стоп: `systemctl stop arix-bot`
