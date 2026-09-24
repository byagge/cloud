from __future__ import annotations

TEXTS = {
    "welcome": (
        "Привет! Это <b>ARIX Cloud</b>: виртуальные серверы за пару минут.\n"
        "Оплата криптовалютой с баланса. Сервер сразу после оплаты.\n\n"
        "Продолжая, ты соглашаешься с условиями: {terms_url}"
    ),
    "home": (
        "{cube} <b>ARIX Cloud</b>\n\n"
        "{wallet} Баланс: <b>{balance}</b>\n"
        "{monitor} Серверов: <b>{count}</b>{expiring_line}\n\n"
        "Что делаем?"
    ),
    "home_expiring": "\nСкоро истекает: {n}",
    "btn_buy": "Купить сервер",
    "btn_servers": "Мои серверы",
    "btn_profile": "Профиль",
    "btn_support": "Поддержка",
    "btn_admin": "Админка",
    "btn_back": "◀ Назад",
    "btn_menu": "◀ В меню",
    "btn_topup": "Пополнить баланс",
    "btn_ref": "Реферальная программа",
    "btn_orders": "История покупок",
    "btn_ledger": "История транзакций",
    "btn_promo": "Промокод",
    "btn_autorenew": "Автопродление",
    "btn_lang": "Язык / Language",
    "btn_accept_terms": "Принимаю, продолжить",
    "btn_write_manager": "Написать менеджеру",
    "btn_check_pay": "Проверить оплату",
    "btn_enter_promo": "Ввести промокод",
    "btn_pay": "Оплатить {price}",
    "btn_topup_missing": "Пополнить на {missing}",
    "profile_title": "{user} <b>Профиль</b>",
    "profile_body": (
        "├ ID: <code>{tg_id}</code>\n"
        "├ Username: {username}\n"
        "├ Имя: {name}\n"
        "├ Баланс: <b>{balance}</b>\n"
        "├ Серверов: <b>{servers}</b>\n"
        "├ Автопродление: <b>{auto}</b> вкл.\n"
        "├ Рефералы: <b>{refs}</b>\n"
        "├ Язык: {lang_name}\n"
        "╰ Регистрация: {created}"
    ),
    "lang_ru": "Русский",
    "lang_en": "English",
    "choose_lang": "Выберите язык / Choose language",
    "support_title": "{mega} <b>Поддержка</b>",
    "support_body": (
        "\n\n{user} <b>Ваш персональный менеджер:</b> {manager}\n"
        "{clock} <b>Время работы:</b> <code>24/7</code>\n"
        "{check} <b>Работаем круглосуточно</b>"
    ),
    "referral_title": "{users} <b>Реферальная программа</b>",
    "referral_body": (
        "\n\nПриглашай друзей по ссылке. После принятия оферты:\n"
        "├ другу: <b>+$1</b> на баланс\n"
        "╰ тебе: <b>+$2</b> на баланс\n\n"
        "├ Твоя ссылка:\n"
        "│ <code>{link}</code>\n"
        "├ Приглашено: <b>{refs}</b>\n"
        "╰ Начислено: <b>{earned}</b>"
    ),
    "referral_works": "Рефералка активна: бонусы начисляются автоматически.",
    "promo_title": "{gift} <b>Промокод</b>\n\nВведи код — бонус зачислится на баланс сразу.",
    "promo_ask": "Введите промокод:",
    "promo_ok": "Промокод применён. На баланс +{amount}",
    "promo_bad": "Промокод не найден.",
    "promo_used": "Вы уже использовали этот промокод.",
    "promo_gone": "Промокод исчерпан.",
    "autorenew_title": (
        "{clock} <b>Автопродление</b>\n\n"
        "Вкл/выкл для каждого сервера. Списание с баланса за сутки до конца аренды."
    ),
    "orders_title": "{bag} <b>История покупок</b>",
    "orders_empty": "\n\nПока пусто.",
    "ledger_title": "{chart} <b>История транзакций</b>",
    "ledger_empty": "\n\nПока пусто.",
    "balance": (
        "{wallet} <b>Баланс</b>\n\n"
        "Доступно: <b>{balance}</b>\n"
        "Пополнить можно прямой оплатой криптой."
    ),
    "topup_amount": "{wallet} Выбери сумму пополнения:",
    "topup_network": (
        "{wallet} <b>Способ оплаты</b>\n\n"
        "Сумма: <b>{amount}</b>\n"
        "Выбери сеть / монету:"
    ),
    "topup_own_ask": "Введите сумму от {min} до {max}:",
    "invoice": (
        "{wallet} <b>Оплата #{invoice_id}</b>\n\n"
        "├ Сеть: <b>{network}</b>\n"
        "├ Актив: <b>{asset}</b>\n"
        "├ Сумма: <b>{pay_amount}</b> {asset}\n"
        "├ ≈ <b>{amount}</b>\n"
        "├ Адрес:\n"
        "│ <code>{address}</code>\n"
        "╰ До: {expires}\n\n"
        "Отправь точную сумму на адрес. После оплаты нажми «Проверить» "
        "или дождись автозачисления (1–3 мин)."
    ),
    "invoice_no_wallets": "Крипто-адреса ещё не настроены. Напишите в поддержку.",
    "pay_status_paid": "Оплата получена ✓",
    "pay_status_pending": "Пока не видно. Подождите 1–2 мин и проверьте снова.",
    "servers_empty": "{monitor} <b>Мои серверы</b>\n\nПока пусто. Купите первый сервер.",
    "servers_list": "{monitor} <b>Мои серверы</b> ({count})\n\nВыберите сервер:",
    "srv_btn_renew": "⌛ Продлить аренду",
    "srv_btn_monitor": "📊 Мониторинг",
    "srv_btn_vnc": "🖥️ Консоль (VNC) на сайте",
    "srv_btn_deploy": "🚀 Деплой проектов",
    "srv_btn_ai": "🤖 ИИ по проекту",
    "srv_btn_stop": "⛔ Выключить",
    "srv_btn_start": "▶️ Включить",
    "srv_btn_restart": "🔄 Перезапустить",
    "srv_btn_os": "🔃 Сменить ОС",
    "srv_btn_pw": "🔑 Сменить пароль",
    "srv_btn_rename": "✏️ Сменить имя",
    "srv_btn_ip": "🌐 Сменить IP",
    "srv_btn_script": "▶️ Запустить скрипт",
    "srv_btn_upgrade": "🛠️ Улучшить конфигурацию",
    "srv_btn_autorenew_on": "✅ Включить автопродление",
    "srv_btn_autorenew_off": "⛔ Выключить автопродление",
    "srv_btn_cancel": "Отключить сервер",
    "srv_btn_back": "◀️ Назад",
    "srv_monitor": (
        "📊 <b>Мониторинг</b>\n\n"
        "├ Статус: {status}\n"
        "├ IP: <code>{ip}</code>\n"
        "├ CPU: {cpu}\n"
        "├ RAM: {ram}\n"
        "├ Диск: {disk}\n"
        "╰ Синхронизация с панелью партнёра каждые несколько минут."
    ),
    "srv_ip_na": "Смена IP через API партнёра недоступна. Откройте панель Tihost.",
    "srv_upgrade_na": "Апгрейд тарифа — в панели партнёра (кнопка Консоль/сайт).",
    "srv_vnc_na": "Откройте панель партнёра для VNC.",
    "agent_deploy_ask": (
        "🚀 <b>Autodeploy</b>\n\n"
        "Пришлите <b>zip</b> или файл проекта одним сообщением.\n"
        "ИИ подключится по SSH, выберет каталог /opt/arix-apps/…, "
        "развернёт без поломки других проектов и спросит токены, если нужны."
    ),
    "agent_ai_ask": (
        "🤖 <b>ИИ по проекту</b>\n\n"
        "Опишите проблему текстом (можно приложить фото).\n"
        "ИИ зайдёт на сервер, посмотрит логи, сделает бэкап перед правками и починит."
    ),
    "agent_need_password": "Нет пароля SSH. Сначала нажмите «Сменить пароль» и дождитесь сообщения.",
    "agent_need_gemini": "GEMINI_API_KEY не задан в .env на сервере.",
    "agent_started": "ИИ агент запущен. Статус придёт сюда.",
    "agent_ask_user": "❓ ИИ спрашивает:\n\n{question}\n\nОтветьте одним сообщением.",
    "agent_done": "✅ {summary}",
    "agent_fail": "❌ Агент: {err}",
    "server_card": (
        "🖥️ <b>Сервер</b>\n\n"
        "OC: {os}\n"
        "Имя: <code>{name}</code>\n"
        "Локация: {location}\n"
        "Статус: {dot} <b>{status}</b>\n"
        "ID: <code>{sid}</code> · Partner: <code>{partner_id}</code>\n\n"
        "<b>Ресурсы</b>\n"
        "CPU: {cpu} · RAM: {ram} · Диск: {disk}\n"
        "Тариф: {plan}\n\n"
        "<b>Подключение</b>\n"
        "IP: <code>{ip}</code>\n"
        "Логин: <code>{login}</code>\n"
        "Пароль: <code>{password}</code>\n\n"
        "До: <b>{expires}</b>\n"
        "Автопродление: {auto_renew}"
    ),
    "buy_location": "{pin} <b>Где разместить сервер?</b>",
    "buy_plan_title": "<b>Выбор конфигурации сервера</b>",
    "buy_plan_meta": (
        "Локация: {flag} {location}\n"
        "Процессор: 🖥️ {cpu_model}\n"
        "Скорость канала: 🌐 {bandwidth}"
    ),
    "buy_plan_hint": "Выберите подходящую конфигурацию из списка ниже:",
    "buy_os_group_title": "<b>Выбор группы операционных систем</b>",
    "buy_os_group_hint": "Выберите группу ОС:",
    "buy_os_title": "<b>Выбор ОС: {group}</b>",
    "buy_os_hint": "Выберите операционную систему для вашего сервера:",
    "buy_spec": (
        "Локация: {flag} {location}\n"
        "Конфигурация: ⚙️ {cpu} vCPU / 💾 {ram} GB RAM / 📂 {disk}GB SSD — {price}/мес\n"
        "Процессор: 🖥️ {cpu_model}\n"
        "Скорость канала: 🌐 {bandwidth}"
    ),
    "buy_term": "{clock} {plan} · {location} · {os}\nНа какой срок?",
    "buy_confirm": (
        "{check} <b>Проверь заказ</b>\n\n"
        "Локация: {location}\n"
        "Тариф: {plan}\n"
        "ОС: {os}\n"
        "Срок: {months} мес (до {end_date})\n"
        "Итого: <b>{price}</b>\n"
        "На балансе: <b>{balance}</b>\n\n"
        "Сервер создаётся сразу после оплаты с баланса."
    ),
    "order_accepted": (
        "{robot} Заказ <b>#{order_id}</b> принят. Создаём сервер, обычно 1–3 минуты.\n"
        "Пришлю доступы сюда."
    ),
    "order_stale": "Заказ устарел",
    "need_funds": "Недостаточно средств",
    "creds": (
        "{lock} <b>Доступы к серверу #{server_id}</b>\n\n"
        "Логин: <code>{login}</code>\n"
        "Пароль: <code>{password}</code>\n\n"
        "{warn} Пароль показан один раз. Сообщение удалится через 10 минут."
    ),
    "creds_password": (
        "{lock} <b>Новый пароль #{server_id}</b>\n\n"
        "Логин: <code>{login}</code>\n"
        "Пароль: <code>{password}</code>\n\n"
        "{warn} Сохраните сейчас. Сообщение удалится через 10 минут."
    ),
    "admin_home": (
        "{crown} <b>Админка</b>\n\n"
        "Клиенты: {users}\n"
        "Серверы: {servers}\n"
        "Очередь jobs: {jobs}\n"
        "Needs review: {review}\n"
        "Баланс партнёра: {partner_balance}"
    ),
    "admin_wallets": (
        "{wallet} <b>Крипто-кошельки</b>\n\n"
        "{list}\n\n"
        "Чтобы задать адрес, отправьте:\n"
        "<code>/set_wallet NETWORK адрес</code>\n"
        "Сети: USDT_TRC20, USDT_BEP20, TON, USDT_TON, BTC"
    ),
    "admin_wallet_set": "Кошелёк {network} сохранён.",
    "maintenance": "Идут технические работы. Серверы работают, вернёмся в течение часа.",
    "banned": "Доступ ограничен. Напишите в поддержку.",
    "not_found": "Не найдено",
    "action_unavailable": "Сейчас это действие недоступно",
    "terms_gate": "Сначала примите условия использования.",
    "lang_set": "Язык сохранён.",
    "adm_btn_users": "Клиенты",
    "adm_btn_servers": "Серверы",
    "adm_btn_orders": "Заказы",
    "adm_btn_invoices": "Счета",
    "adm_btn_jobs": "Jobs",
    "adm_btn_fin": "Финансы",
    "adm_btn_settings": "Настройки",
    "adm_btn_wallets": "Кошельки",
    "adm_btn_audit": "Аудит",
    "adm_btn_broadcast": "Рассылка",
    "adm_btn_admin": "◀ Админка",
    "adm_users_title": "{users} <b>Клиенты</b> ({count})",
    "adm_servers_title": "{monitor} <b>Серверы</b> ({count})",
    "adm_orders_title": "{bag} <b>Заказы</b> ({count})",
    "adm_invoices_title": "{wallet} <b>Счета</b> ({count})",
    "adm_jobs_title": "{robot} <b>Jobs</b> ({count})",
    "adm_empty": "\n\nПусто.",
    "adm_ban": "Забанить",
    "adm_unban": "Разбанить",
    "adm_balance": "Баланс ±",
    "adm_user_servers": "Серверы",
    "adm_user_orders": "Заказы",
    "adm_attach_server": "➕ Сервер по Partner ID",
    "adm_attach_ask": "Partner ID сервера (число из панели Tihost) для клиента #{id}:",
    "adm_attach_ok": "Сервер #{sid} привязан (partner {pid}, IP {ip})",
    "adm_attach_fail": "Не удалось: {err}",
    "adm_open_user": "Клиент",
    "adm_freeze": "Заморозить",
    "adm_unfreeze": "Разморозить",
    "adm_retry": "Повторить",
    "adm_mark_paid": "Пометить оплаченным",
    "adm_cancel_job": "Отменить job",
    "adm_maint_tog": "Maintenance вкл/выкл",
    "adm_pay_on": "Включить оплату",
    "adm_pay_off": "Отключить оплату",
    "adm_set_addr": "Задать адрес",
    "adm_bal_ask": "Сумма корректировки для #{id} (например 10 или -5):",
    "adm_addr_ask": "Отправьте адрес для {network}:",
    "adm_bc_ask": "Пришлите сообщение для рассылки — как есть (текст/Markdown/фото/медиа):",
    "adm_settings": (
        "{hammer} <b>Настройки</b>\n\n"
        "├ Markup: <b>{markup}</b>\n"
        "├ Fee: <b>{fee}</b>\n"
        "├ Партнёр $4 → клиент: <b>{example}</b>\n"
        "├ Maintenance: <b>{maint}</b>\n"
        "├ Min topup: {min_topup}\n"
        "╰ Max servers: {max_servers}"
    ),
    "adm_wallet_card": (
        "{wallet} <b>{label}</b>\n\n"
        "├ ID: <code>{wid}</code>\n"
        "├ Сеть: {network}\n"
        "├ Актив: {asset}\n"
        "├ Адрес: <code>{address}</code>\n"
        "╰ Статус: <b>{status}</b>"
    ),
}
