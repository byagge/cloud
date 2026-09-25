from __future__ import annotations

TEXTS = {
    "welcome": (
        "Hi! This is <b>ARIX Cloud</b>: VPS in a couple of minutes.\n"
        "Pay with crypto via balance. Server right after payment.\n\n"
        "By continuing you accept: {terms_url}"
    ),
    "home": (
        "{cube} <b>ARIX Cloud</b>\n\n"
        "{wallet} Balance: <b>{balance}</b>\n"
        "{monitor} Servers: <b>{count}</b>{expiring_line}\n\n"
        "What next?"
    ),
    "home_expiring": "\nExpiring soon: {n}",
    "btn_buy": "Buy server",
    "btn_servers": "My servers",
    "btn_profile": "Profile",
    "btn_support": "Support",
    "btn_admin": "Admin",
    "btn_back": "◀ Back",
    "btn_menu": "◀ Menu",
    "btn_topup": "Top up balance",
    "btn_ref": "Referral program",
    "btn_orders": "Purchase history",
    "btn_ledger": "Transaction history",
    "btn_promo": "Promo code",
    "btn_autorenew": "Auto-renew",
    "btn_lang": "Language",
    "btn_accept_terms": "Accept & continue",
    "btn_write_manager": "Message manager",
    "btn_check_pay": "Check payment",
    "btn_enter_promo": "Enter promo code",
    "btn_pay": "Pay {price}",
    "btn_topup_missing": "Top up {missing}",
    "profile_title": "{user} <b>Profile</b>",
    "profile_body": (
        "├ ID: <code>{tg_id}</code>\n"
        "├ Username: {username}\n"
        "├ Name: {name}\n"
        "├ Balance: <b>{balance}</b>\n"
        "├ Servers: <b>{servers}</b>\n"
        "├ Auto-renew: <b>{auto}</b> on\n"
        "├ Referrals: <b>{refs}</b>\n"
        "├ Language: {lang_name}\n"
        "╰ Registered: {created}"
    ),
    "lang_ru": "Русский",
    "lang_en": "English",
    "choose_lang": "Choose language / Выберите язык",
    "lang_set": "Language saved.",
    "support_title": "{mega} <b>Support</b>",
    "support_body": (
        "\n\n{user} <b>Your personal manager:</b> {manager}\n"
        "{clock} <b>Hours:</b> <code>24/7</code>\n"
        "{check} <b>Always online</b>"
    ),
    "referral_title": "{users} <b>Referral program</b>",
    "referral_body": (
        "\n\nInvite friends with your link. After they accept terms:\n"
        "├ friend gets: <b>+$1</b>\n"
        "╰ you get: <b>+$2</b>\n\n"
        "├ Your link:\n"
        "│ <code>{link}</code>\n"
        "├ Invited: <b>{refs}</b>\n"
        "╰ Earned: <b>{earned}</b>"
    ),
    "referral_works": "Referral is active: bonuses are credited automatically.",
    "promo_title": "{gift} <b>Promo code</b>\n\nEnter a code — bonus credits to balance.",
    "promo_ask": "Enter promo code:",
    "promo_ok": "Promo applied. Balance +{amount}",
    "promo_bad": "Promo not found.",
    "promo_used": "You already used this promo.",
    "promo_gone": "Promo exhausted.",
    "autorenew_title": (
        "{clock} <b>Auto-renew</b>\n\n"
        "Toggle per server. Charged from balance 24h before expiry."
    ),
    "orders_title": "{bag} <b>Purchase history</b>",
    "orders_empty": "\n\nEmpty.",
    "ledger_title": "{chart} <b>Transaction history</b>",
    "ledger_empty": "\n\nEmpty.",
    "balance": (
        "{wallet} <b>Balance</b>\n\n"
        "Available: <b>{balance}</b>\n"
        "Top up with direct crypto payment."
    ),
    "topup_amount": "{wallet} Choose top-up amount:",
    "topup_network": (
        "{wallet} <b>Payment method</b>\n\n"
        "Amount: <b>{amount}</b>\n"
        "Choose network / coin:"
    ),
    "topup_own_ask": "Enter amount from {min} to {max}:",
    "invoice": (
        "{wallet} <b>Payment #{invoice_id}</b>\n\n"
        "├ Network: <b>{network}</b>\n"
        "├ Asset: <b>{asset}</b>\n"
        "├ Amount: <b>{pay_amount}</b> {asset}\n"
        "├ ≈ <b>{amount}</b>\n"
        "├ Address:\n"
        "│ <code>{address}</code>\n"
        "╰ Until: {expires}\n\n"
        "Send the exact amount. Then tap Check or wait for auto-credit (1–3 min)."
    ),
    "invoice_no_wallets": "Crypto addresses not configured yet. Contact support.",
    "pay_status_paid": "Payment received ✓",
    "pay_status_pending": "Not seen yet. Wait 1–2 min and check again.",
    "servers_empty": "{monitor} <b>My servers</b>\n\nEmpty. Buy your first server.",
    "servers_list": "{monitor} <b>My servers</b> ({count})\n\nPick a server:",
    "srv_btn_renew": "⌛ Extend rental",
    "srv_btn_monitor": "📊 Monitoring",
    "srv_btn_vnc": "🖥️ Console (VNC) on site",
    "srv_btn_deploy": "📦 Update project (zip)",
    "srv_btn_ai": "🤖 AI: fix project",
    "srv_btn_stop": "⛔ Power off",
    "srv_btn_start": "▶️ Power on",
    "srv_btn_restart": "🔄 Restart",
    "srv_btn_os": "🔃 Change OS",
    "srv_btn_pw": "🔑 Refresh password",
    "srv_pw_queued": "Password requested from provider. Re-open the server in ~20s.",
    "srv_pw_loading": "loading…",
    "srv_pw_missing": "—",
    "srv_btn_rename": "✏️ Rename",
    "srv_btn_ip": "🌐 Change IP",
    "srv_btn_script": "▶️ Run script",
    "srv_btn_upgrade": "🛠️ Upgrade plan",
    "srv_btn_autorenew_on": "✅ Enable auto-renew",
    "srv_btn_autorenew_off": "⛔ Disable auto-renew",
    "srv_btn_cancel": "Disconnect server",
    "srv_btn_back": "◀️ Back",
    "srv_monitor": (
        "📊 <b>Monitoring</b>\n\n"
        "├ Status: {status}\n"
        "├ IP: <code>{ip}</code>\n"
        "├ CPU: {cpu}\n"
        "├ RAM: {ram}\n"
        "├ Disk: {disk}\n"
        "╰ Stats refresh automatically."
    ),
    "agent_need_password": "SSH password is not ready yet. Open the server later or contact support.",
    "reinstall_os_groups": "<b>Change OS</b>\n\nPick a group:",
    "reinstall_os_versions": "<b>Change OS: {group}</b>\n\nPick an image:",
    "agent_deploy_ask": (
        "📦 <b>Update existing project</b>\n\n"
        "Send a <b>zip/tar</b> for a project that <b>already runs</b> on this VPS.\n"
        "Then choose overwrite or deploy alongside. AI only updates under /opt/arix-apps/…\n"
        "Optional caption: which app to update.\n\n"
        "During the run you get a <b>Stop</b> button."
    ),
    "agent_ai_ask": (
        "🤖 <b>AI: fix existing project</b>\n\n"
        "Describe what is broken (photo optional).\n"
        "AI analyzes first, proposes a plan, then waits for your OK before writing.\n"
        "Only patches projects already on the server — minimal edits + backups.\n\n"
        "During the run you get a <b>Stop</b> button."
    ),
    "agent_deploy_strategy_ask": (
        "📦 Archive saved.\n\n"
        "How should we deploy it on this server?"
    ),
    "agent_btn_dep_over": "♻️ Overwrite existing",
    "agent_btn_dep_side": "➕ Deploy alongside",
    "agent_btn_plan_go": "✅ Work on plan",
    "agent_btn_plan_no": "❌ Decline plan",
    "agent_plan_accepted": "⚙️ Starting work on the plan…",
    "agent_answer": "✅ {answer}\n\n💡 {suggestion}",
    "agent_plan_use_buttons": "Use the buttons under the plan (Work on plan / Stop).",
    "agent_need_gemini": "GEMINI_API_KEY is missing in server .env.",
    "agent_started": (
        "🛠 <b>AI session started</b>\n\n"
        "• Works only on existing projects\n"
        "• Asks if it needs a token/domain\n"
        "• Press <b>Stop</b> to cancel anytime"
    ),
    "agent_got_answer": "Got it — AI continues. You can still press Stop.",
    "agent_ask_user": "❓ AI asks:\n\n{question}\n\nReply in one message (or press Stop).",
    "agent_done": "✅ {summary}",
    "agent_fail": "❌ Agent: {err}",
    "agent_busy": "AI is waiting for your reply to the previous question.",
    "agent_no_ip": "Server has no IP yet.",
    "agent_file_too_big": "File is too large (max 40 MB).",
    "agent_file_bad_type": "Send a zip / tar / tar.gz archive.",
    "agent_need_detail": "Describe the problem in more detail (min 3 characters).",
    "agent_nopw_reset": "SSH password missing — resetting it now. Try AI again in a minute.",
    "agent_already_running": "AI is already working on this server. Wait for it to finish.",
    "agent_locked": "AI is busy on this server. Retrying shortly…",
    "agent_btn_stop": "⏹ Stop AI",
    "agent_stopping": "Stopping AI…",
    "server_card": (
        "🖥️ <b>Server</b>\n\n"
        "<b>Connection</b>\n"
        "IP: <code>{ip}</code>\n"
        "Login: <code>{login}</code>\n"
        "Password: <code>{password}</code>\n\n"
        "OS: {os}\n"
        "Name: <code>{name}</code>\n"
        "Location: {location}\n"
        "Status: {dot} <b>{status}</b>\n"
        "ID: <code>{sid}</code>\n\n"
        "<b>Resources</b>\n"
        "CPU: {cpu} · RAM: {ram} · Disk: {disk}\n"
        "Plan: {plan}\n\n"
        "Until: <b>{expires}</b>\n"
        "Auto-renew: {auto_renew}"
    ),
    "buy_location": "{pin} <b>Where to place the server?</b>",
    "buy_plan_title": "<b>Choose server configuration</b>",
    "buy_plan_meta": (
        "Location: {flag} {location}\n"
        "CPU: 🖥️ {cpu_model}\n"
        "Network: 🌐 {bandwidth}"
    ),
    "buy_plan_hint": "Pick a configuration from the list:",
    "buy_os_group_title": "<b>Choose OS group</b>",
    "buy_os_group_hint": "Select OS family:",
    "buy_os_title": "<b>Choose OS: {group}</b>",
    "buy_os_hint": "Select an operating system for your server:",
    "buy_spec": (
        "Location: {flag} {location}\n"
        "Config: ⚙️ {cpu} vCPU / 💾 {ram} GB RAM / 📂 {disk}GB SSD — {price}/mo\n"
        "CPU: 🖥️ {cpu_model}\n"
        "Network: 🌐 {bandwidth}"
    ),
    "buy_term": "{clock} {plan} · {location} · {os}\nFor how long?",
    "buy_confirm": (
        "{check} <b>Confirm order</b>\n\n"
        "Location: {location}\n"
        "Plan: {plan}\n"
        "OS: {os}\n"
        "Term: {months} mo (until {end_date})\n"
        "Total: <b>{price}</b>\n"
        "Balance: <b>{balance}</b>\n\n"
        "Server is created right after paying from balance."
    ),
    "order_accepted": (
        "{robot} Order <b>#{order_id}</b> accepted. Creating server (1–3 min).\n"
        "Credentials will arrive here."
    ),
    "order_stale": "Order expired",
    "need_funds": "Insufficient balance",
    "creds": (
        "{lock} <b>Credentials #{server_id}</b>\n\n"
        "Login: <code>{login}</code>\n"
        "Password: <code>{password}</code>\n\n"
        "{warn} Shown once. Deleted in 10 minutes."
    ),
    "creds_password": (
        "{lock} <b>New password #{server_id}</b>\n\n"
        "Login: <code>{login}</code>\n"
        "Password: <code>{password}</code>\n\n"
        "{warn} Save now. Deleted in 10 minutes."
    ),
    "admin_home": (
        "{crown} <b>Admin</b>\n\n"
        "Users: {users}\n"
        "Servers: {servers}\n"
        "Jobs: {jobs}\n"
        "Needs review: {review}\n"
        "Partner balance: {partner_balance}"
    ),
    "admin_wallets": (
        "{wallet} <b>Crypto wallets</b>\n\n"
        "{list}\n\n"
        "Set address:\n"
        "<code>/set_wallet NETWORK address</code>\n"
        "Networks: USDT_TRC20, USDT_BEP20, TON, USDT_TON, BTC"
    ),
    "admin_wallet_set": "Wallet {network} saved.",
    "maintenance": "Maintenance. Servers keep running.",
    "banned": "Access restricted. Contact support.",
    "not_found": "Not found",
    "action_unavailable": "Action unavailable now",
    "terms_gate": "Accept terms first.",
    "adm_btn_users": "Clients",
    "adm_btn_servers": "Servers",
    "adm_btn_orders": "Orders",
    "adm_btn_invoices": "Invoices",
    "adm_btn_jobs": "Jobs",
    "adm_btn_fin": "Finance",
    "adm_btn_settings": "Settings",
    "adm_btn_wallets": "Wallets",
    "adm_btn_audit": "Audit",
    "adm_btn_broadcast": "Broadcast",
    "adm_btn_admin": "◀ Admin",
    "adm_users_title": "{users} <b>Clients</b> ({count})",
    "adm_servers_title": "{monitor} <b>Servers</b> ({count})",
    "adm_orders_title": "{bag} <b>Orders</b> ({count})",
    "adm_invoices_title": "{wallet} <b>Invoices</b> ({count})",
    "adm_jobs_title": "{robot} <b>Jobs</b> ({count})",
    "adm_empty": "\n\nEmpty.",
    "adm_ban": "Ban",
    "adm_unban": "Unban",
    "adm_balance": "Balance ±",
    "adm_user_servers": "Servers",
    "adm_user_orders": "Orders",
    "adm_attach_server": "➕ Server by Partner ID",
    "adm_attach_ask": "Partner server ID (from Tihost panel) for client #{id}:",
    "adm_attach_ok": "Server #{sid} attached (partner {pid}, IP {ip})",
    "adm_attach_fail": "Failed: {err}",
    "adm_open_user": "Client",
    "adm_freeze": "Freeze",
    "adm_unfreeze": "Unfreeze",
    "adm_retry": "Retry",
    "adm_mark_paid": "Mark paid",
    "adm_cancel_job": "Cancel job",
    "adm_maint_tog": "Toggle maintenance",
    "adm_pay_on": "Enable payment",
    "adm_pay_off": "Disable payment",
    "adm_set_addr": "Set address",
    "adm_bal_ask": "Adjustment amount for #{id} (e.g. 10 or -5):",
    "adm_addr_ask": "Send address for {network}:",
    "adm_bc_ask": "Send the broadcast message as-is (text/Markdown/photo/media):",
    "adm_settings": (
        "{hammer} <b>Settings</b>\n\n"
        "├ Markup: <b>{markup}</b>\n"
        "├ Fee: <b>{fee}</b>\n"
        "├ Partner $4 → client: <b>{example}</b>\n"
        "├ Maintenance: <b>{maint}</b>\n"
        "├ Min topup: {min_topup}\n"
        "╰ Max servers: {max_servers}"
    ),
    "adm_wallet_card": (
        "{wallet} <b>{label}</b>\n\n"
        "├ ID: <code>{wid}</code>\n"
        "├ Network: {network}\n"
        "├ Asset: {asset}\n"
        "├ Address: <code>{address}</code>\n"
        "╰ Status: <b>{status}</b>"
    ),
}
