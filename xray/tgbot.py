#!/usr/bin/env python3
"""
Reality-EZPZ Telegram Bot
Управление VPN-панелью через Telegram.
"""

import asyncio
import io
import logging
import os
import re
import subprocess
import zipfile
from datetime import datetime
from functools import wraps

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────
# Конфигурация
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reality-bot")

DATA_DIR    = "/opt/reality-ezpz"
CONFIG_FILE = os.path.join(DATA_DIR, "config")
USERS_FILE  = os.path.join(DATA_DIR, "users")

TOKEN = os.environ.get("BOT_TOKEN") or ""
if not TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан")

ADMIN = os.environ.get("BOT_ADMIN", "")


def _find_script() -> str:
    """Ищет reality-ezpz.sh в стандартных местах установки."""
    candidates = [
        os.path.join(os.path.dirname(DATA_DIR.rstrip("/")), "reality-ezpz.sh"),
        os.path.join(DATA_DIR, "reality-ezpz.sh"),
        "/opt/reality-ezpz.sh",
        os.path.expanduser("~/reality-ezpz/reality-ezpz.sh"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            logger.info(f"Скрипт найден: {p}")
            return p
    logger.warning(f"reality-ezpz.sh не найден! Проверьте установку. Искал в: {candidates}")
    return candidates[0]


SCRIPT_PATH = _find_script()
# Подавляем systemctl внутри Docker-контейнера
BASE_CMD = f"function systemctl() {{ :; }}; export -f systemctl; bash {SCRIPT_PATH} "

# ─────────────────────────────────────────────
# Матрица совместимости транспорт × ядро × security
# Источник: xray v26.2.6 + sing-box 1.12
# ─────────────────────────────────────────────

TRANSPORT_COMPAT: dict = {
    "tcp":         {"cores": ["xray", "sing-box"], "security": ["reality", "letsencrypt", "selfsigned", "notls"]},
    "http":        {"cores": ["xray", "sing-box"], "security": ["letsencrypt", "selfsigned", "notls"]},
    "ws":          {"cores": ["xray", "sing-box"], "security": ["letsencrypt", "selfsigned", "notls"]},
    "grpc":        {"cores": ["xray", "sing-box"], "security": ["reality", "letsencrypt", "selfsigned", "notls"]},
    "xhttp":       {"cores": ["xray"],             "security": ["reality", "letsencrypt", "selfsigned", "notls"]},
    "httpupgrade": {"cores": ["sing-box"],         "security": ["letsencrypt", "selfsigned", "notls"]},
    "mkcp":        {"cores": ["xray"],             "security": ["reality", "letsencrypt", "selfsigned", "notls", "xicmp", "xdns"]},
    "hysteria2":   {"cores": ["sing-box"],         "security": ["selfsigned", "letsencrypt"]},
    "tuic":        {"cores": ["sing-box"],         "security": ["selfsigned", "letsencrypt"]},
    "shadowtls":   {"cores": ["sing-box"],         "security": ["reality"]},
}

SECURITY_LABELS = {
    "reality":     "🔒 Reality",
    "letsencrypt": "🔐 Let's Encrypt",
    "selfsigned":  "📜 Self-Signed",
    "notls":       "🔓 No TLS",
    "xicmp":       "📡 xICMP (finalmask)",
    "xdns":        "🌐 xDNS (finalmask)",
}

TRANSPORT_LABELS = {
    "tcp":         "TCP",
    "http":        "HTTP/2",
    "ws":          "WebSocket",
    "grpc":        "gRPC",
    "xhttp":       "XHTTP ✦",
    "httpupgrade": "HTTPUpgrade",
    "mkcp":        "mKCP ✦",
    "hysteria2":   "Hysteria2",
    "tuic":        "TUIC",
    "shadowtls":   "ShadowTLS",
}

# ─────────────────────────────────────────────
# Утилиты: скрипт, конфиг, пользователи
# ─────────────────────────────────────────────

def run_script(extra_args: str = "", timeout: int = 300) -> tuple:
    """Запускает reality-ezpz.sh синхронно. Возвращает (returncode, output)."""
    cmd = BASE_CMD + extra_args
    try:
        proc = subprocess.Popen(
            cmd, shell=True, executable="/bin/bash",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=timeout)
        out_s = out.decode(errors="ignore").strip()
        err_s = err.decode(errors="ignore").strip()
        if proc.returncode != 0 and err_s:
            combined = (out_s + "\n" + err_s).strip()
        else:
            combined = out_s
        return proc.returncode, combined
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, f"⏱ Таймаут {timeout}с. Операция прервана."
    except Exception as exc:
        return 1, str(exc)


def run_script_bg(extra_args: str) -> None:
    """Запускает скрипт в фоне без ожидания (для DELETE к CF API и т.п.)."""
    cmd = BASE_CMD + extra_args
    subprocess.Popen(
        cmd, shell=True, executable="/bin/bash",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def read_config() -> dict:
    conf = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.error(f"read_config: {exc}")
    return conf


def write_config(key: str, value: str) -> None:
    """Атомарная запись key=value. Создаёт файл если не существует."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        open(CONFIG_FILE, "a").close()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        new_lines, found = [], False
        for line in lines:
            if re.match(rf"^{re.escape(key)}\s*=", line.strip()):
                new_lines.append(f"{key}={value}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}\n")
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as exc:
        logger.error(f"write_config({key}={value}): {exc}")


def get_users() -> list:
    users = []
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    users.append(line.split("=", 1)[0].strip())
    except FileNotFoundError:
        pass
    return users


def get_user_links(name: str) -> list:
    """Возвращает share-ссылки/конфиги пользователя."""
    _, out = run_script(f"--show-user {name}", timeout=60)
    return [
        line.strip() for line in out.splitlines()
        if line.strip() and ("://" in line or line.strip().startswith('{"dns"'))
    ]


def make_backup() -> str:
    ts    = datetime.now().strftime("%Y-%m-%d_%H-%M")
    fname = f"/tmp/backup_{ts}.zip"
    try:
        with zipfile.ZipFile(fname, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in ("config", "users"):
                p = os.path.join(DATA_DIR, name)
                if os.path.exists(p):
                    zf.write(p, arcname=name)
        return fname
    except Exception as exc:
        logger.error(f"make_backup: {exc}")
        return ""


def _snippet(text: str, limit: int = 3800) -> str:
    return (text[:limit] + "\n…(обрезано)") if len(text) > limit else text


# ─────────────────────────────────────────────
# Авторизация
# ─────────────────────────────────────────────

def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        user    = update.effective_user
        chat_id = update.effective_chat.id
        if not user:
            await context.bot.send_message(chat_id, "❌ Неизвестный пользователь.")
            return
        uid    = str(user.id)
        uname  = (user.username or "").lstrip("@")
        admins = {x.strip().lstrip("@") for x in ADMIN.split(",") if x.strip()}
        if uid in admins or (uname and uname in admins):
            return await func(update, context, *a, **kw)
        logger.warning(f"Доступ запрещён: id={uid} username={uname}")
        await context.bot.send_message(chat_id, "🚫 Нет доступа.")
    return wrapper


# ─────────────────────────────────────────────
# Меню: главное
# ─────────────────────────────────────────────

async def send_main_menu(bot, chat_id: int) -> None:
    kb = [
        [InlineKeyboardButton("👥 Пользователи", callback_data="m_users")],
        [InlineKeyboardButton("⚙️ Настройки",    callback_data="m_settings")],
    ]
    await bot.send_message(
        chat_id, "🤖 <b>Reality-EZPZ</b>\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# Меню: настройки
# ─────────────────────────────────────────────

async def send_settings_menu(bot, chat_id: int) -> None:
    c         = read_config()
    warp      = c.get("warp", "OFF")
    core      = c.get("core", "?")
    transport = c.get("transport", "?")
    security  = c.get("security", "?")

    warp_icon  = "🟢" if warp == "ON" else "🔴"
    sec_label  = SECURITY_LABELS.get(security, security)
    tran_label = TRANSPORT_LABELS.get(transport, transport)

    text = (
        "⚙️ <b>Настройки сервера</b>\n\n"
        f"🖥  Ядро:      <code>{core}</code>\n"
        f"🚇  Транспорт: <code>{tran_label}</code>\n"
        f"🔐  Security:  <code>{sec_label}</code>\n"
        f"🌐  Сервер:    <code>{c.get('server', '?')}</code>\n"
        f"🔌  Порт:      <code>{c.get('port', '?')}</code>\n"
        f"📡  SNI:       <code>{c.get('domain', '?')}</code>\n"
        f"📂  Path:      <code>/{c.get('service_path', '')}</code>\n"
        f"{warp_icon}  WARP:      <b>{warp}</b>"
    )

    warp_btn = (
        InlineKeyboardButton("🔴 Выкл WARP",   callback_data="warp_off")
        if warp == "ON" else
        InlineKeyboardButton("🟢 Вкл WARP",    callback_data="sub!warp")
    )

    kb = [
        [InlineKeyboardButton("🖥 Ядро",        callback_data="sub!core"),
         InlineKeyboardButton("🚇 Транспорт",   callback_data="sub!transport")],
        [InlineKeyboardButton("🔐 Security",    callback_data="sub!security"),
         warp_btn],
        [InlineKeyboardButton("🌐 Сервер",      callback_data="ask!server"),
         InlineKeyboardButton("🔌 Порт",        callback_data="ask!port")],
        [InlineKeyboardButton("📡 SNI",         callback_data="ask!domain"),
         InlineKeyboardButton("📂 Path",        callback_data="ask!path")],
        [InlineKeyboardButton("🏷 Host Header", callback_data="ask!host_header")],
        [InlineKeyboardButton("🔄 Применить / Перезапуск", callback_data="do_restart")],
        [InlineKeyboardButton("📦 Бэкап",       callback_data="do_backup")],
        [InlineKeyboardButton("🔙 Главное меню",callback_data="main")],
    ]
    await bot.send_message(
        chat_id, text,
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# Меню: пользователи
# ─────────────────────────────────────────────

@restricted
async def menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = [
        [InlineKeyboardButton("📜 Список",   callback_data="u_list"),
         InlineKeyboardButton("➕ Добавить", callback_data="u_add")],
        [InlineKeyboardButton("➖ Удалить",  callback_data="u_del_m"),
         InlineKeyboardButton("🔙 Назад",    callback_data="main")],
    ]
    await context.bot.send_message(
        update.effective_chat.id, "👥 <b>Пользователи</b>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML,
    )


@restricted
async def users_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    users   = get_users()
    chat_id = update.effective_chat.id
    if not users:
        await context.bot.send_message(chat_id, "ℹ️ Список пользователей пуст.")
        return
    prefix = "u_show" if mode == "show" else "u_del"
    kb  = [[InlineKeyboardButton(u, callback_data=f"{prefix}!{u}")] for u in users]
    kb += [[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]]
    await context.bot.send_message(
        chat_id, "Выберите пользователя:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def _send_user_links(bot, chat_id: int, name: str) -> None:
    """Отправляет QR-коды + ссылки пользователя."""
    links = get_user_links(name)
    if not links:
        await bot.send_message(chat_id, f"⚠️ Конфигурация для <b>{name}</b> не найдена.", parse_mode=ParseMode.HTML)
        return
    for link in links:
        caption = f"<code>{link[:1000]}</code>"
        try:
            qr  = qrcode.make(link)
            bio = io.BytesIO()
            qr.save(bio, "PNG")
            bio.seek(0)
            await bot.send_photo(chat_id, photo=bio, caption=caption, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML)
    await bot.send_message(
        chat_id, "↩️",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]]),
    )


# ─────────────────────────────────────────────
# Ввод значений
# ─────────────────────────────────────────────

INPUT_HINTS = {
    "server":       "🌐 Введите IP или домен сервера:",
    "domain":       "📡 Введите SNI-домен:",
    "port":         "🔌 Введите порт (1–65535):",
    "path":         "📂 Введите path (без /):\n<i>Отправьте / для сброса</i>",
    "host_header":  "🏷 Введите Host-заголовок:",
    "warp_license": "⭐ Введите лицензию WARP+:\n<code>xxxxxxxx-xxxxxxxx-xxxxxxxx</code>",
}


@restricted
async def ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str) -> None:
    context.user_data["state"] = "setting"
    context.user_data["param"] = param
    hint = INPUT_HINTS.get(param, f"Введите значение для <b>{param}</b>:")
    kb   = [[InlineKeyboardButton("❌ Отмена", callback_data="m_settings")]]
    await context.bot.send_message(
        update.effective_chat.id, hint,
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# Применение настройки
# ─────────────────────────────────────────────

@restricted
async def apply_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, val: str) -> None:
    chat_id = update.effective_chat.id

    # Валидация: транспорт ↔ ядро
    if param == "transport":
        c      = read_config()
        core   = c.get("core", "xray")
        t_info = TRANSPORT_COMPAT.get(val, {})
        if core not in t_info.get("cores", []):
            allowed = " / ".join(t_info.get("cores", ["xray"]))
            await context.bot.send_message(
                chat_id,
                f"⚠️ Транспорт <b>{val}</b> работает только с ядром <b>{allowed}</b>.\n"
                "Сначала смените ядро.",
                parse_mode=ParseMode.HTML,
            )
            await send_settings_menu(context.bot, chat_id)
            return

    # Валидация: security ↔ транспорт
    if param == "security":
        c         = read_config()
        transport = c.get("transport", "tcp")
        core      = c.get("core", "xray")
        t_info    = TRANSPORT_COMPAT.get(transport, {})
        allowed   = t_info.get("security", []) if core in t_info.get("cores", []) else []
        if val not in allowed:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Security <b>{val}</b> несовместим с транспортом <b>{transport}</b>.\n"
                f"Доступные: {', '.join(allowed) or '—'}",
                parse_mode=ParseMode.HTML,
            )
            await send_settings_menu(context.bot, chat_id)
            return

    # WARP+ лицензия — отдельный путь
    if param == "warp_license":
        msg = await context.bot.send_message(chat_id, "⏳ Активирую WARP+…")
        rc, out = run_script(f"--warp-license {val}", timeout=120)
        status  = "🟢 WARP+ активирован." if rc == 0 else "❌ Ошибка активации WARP+."
        await context.bot.edit_message_text(
            f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await send_settings_menu(context.bot, chat_id)
        return

    # Path: сброс по "/"
    if param == "service_path" and val in ("/", ""):
        val = ""

    # Запись + перезапуск
    write_config(param, val)
    msg = await context.bot.send_message(
        chat_id, f"⏳ Применяю <b>{param}</b> = <code>{val or '(пусто)'}</code>…",
        parse_mode=ParseMode.HTML,
    )
    rc, out = run_script(timeout=120)
    status  = "✅ Готово." if rc == 0 else "⚠️ Завершено с ошибкой."
    await context.bot.edit_message_text(
        f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
        chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
    )
    await send_settings_menu(context.bot, chat_id)


# ─────────────────────────────────────────────
# Подменю выбора параметра
# ─────────────────────────────────────────────

async def _handle_sub(bot, chat_id: int, arg: str) -> None:
    c    = read_config()
    core = c.get("core", "xray")
    back = [InlineKeyboardButton("🔙 Назад", callback_data="m_settings")]

    if arg == "core":
        current = c.get("core", "")
        kb = [
            [InlineKeyboardButton(("✅ " if "xray"     == current else "") + "Xray",
                                  callback_data="set!core!xray"),
             InlineKeyboardButton(("✅ " if "sing-box" == current else "") + "Sing-Box",
                                  callback_data="set!core!sing-box")],
            back,
        ]
        await bot.send_message(chat_id, "🖥 Выберите ядро:", reply_markup=InlineKeyboardMarkup(kb))

    elif arg == "transport":
        opts    = [t for t, info in TRANSPORT_COMPAT.items() if core in info["cores"]]
        current = c.get("transport", "")
        rows: list = []
        row:  list = []
        for t in opts:
            mark  = "✅ " if t == current else ""
            label = f"{mark}{TRANSPORT_LABELS.get(t, t)}"
            row.append(InlineKeyboardButton(label, callback_data=f"set!transport!{t}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append(back)
        await bot.send_message(
            chat_id,
            f"🚇 Выберите транспорт:\n<i>Ядро: {core}</i>",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML,
        )

    elif arg == "security":
        transport = c.get("transport", "tcp")
        t_info    = TRANSPORT_COMPAT.get(transport, {})
        sec_opts  = t_info.get("security", []) if core in t_info.get("cores", []) else ["notls"]
        current   = c.get("security", "")
        rows = [
            [InlineKeyboardButton(
                ("✅ " if s == current else "") + SECURITY_LABELS.get(s, s),
                callback_data=f"set!security!{s}",
            )]
            for s in sec_opts
        ]
        rows.append(back)
        await bot.send_message(
            chat_id,
            f"🔐 Выберите security:\n<i>Транспорт: {transport}  Ядро: {core}</i>",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML,
        )

    elif arg == "warp":
        kb = [
            [InlineKeyboardButton("🆓 WARP бесплатный",    callback_data="warp_free")],
            [InlineKeyboardButton("⭐ WARP+ с лицензией",  callback_data="ask!warp_license")],
            back,
        ]
        await bot.send_message(chat_id, "📡 Выберите тип WARP:", reply_markup=InlineKeyboardMarkup(kb))

    else:
        await bot.send_message(chat_id, f"⚠️ Неизвестный раздел: {arg}")


# ─────────────────────────────────────────────
# Callback handler
# ─────────────────────────────────────────────

@restricted
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    await query.answer()
    parts   = query.data.split("!")
    cmd     = parts[0]
    arg     = parts[1] if len(parts) > 1 else ""
    arg2    = parts[2] if len(parts) > 2 else ""
    chat_id = update.effective_chat.id
    bot     = context.bot

    if cmd == "main":
        await send_main_menu(bot, chat_id)

    elif cmd == "m_users":
        await menu_users(update, context)

    elif cmd == "m_settings":
        await send_settings_menu(bot, chat_id)

    elif cmd == "u_list":
        await users_action(update, context, "show")

    elif cmd == "u_del_m":
        await users_action(update, context, "del")

    elif cmd == "u_add":
        context.user_data["state"] = "add_user"
        await bot.send_message(
            chat_id, "➕ Введите имя пользователя (латиница и цифры):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="m_users")]]),
        )

    elif cmd == "u_show":
        await _send_user_links(bot, chat_id, arg)

    elif cmd == "u_del":
        kb = [[
            InlineKeyboardButton(f"✅ Да, удалить {arg}", callback_data=f"confirm_del!{arg}"),
            InlineKeyboardButton("❌ Отмена",             callback_data="m_users"),
        ]]
        await bot.send_message(
            chat_id, f"Удалить пользователя <b>{arg}</b>?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML,
        )

    elif cmd == "confirm_del":
        msg = await bot.send_message(chat_id, f"⏳ Удаляю <b>{arg}</b>…", parse_mode=ParseMode.HTML)
        rc, out = run_script(f"--delete-user {arg}")
        status  = f"✅ Пользователь <b>{arg}</b> удалён." if rc == 0 else f"❌ Ошибка удаления <b>{arg}</b>."
        await bot.edit_message_text(
            f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await menu_users(update, context)

    elif cmd == "ask":
        await ask_input(update, context, arg)

    elif cmd == "set":
        await apply_setting(update, context, arg, arg2)

    elif cmd == "warp_off":
        # DELETE к CF API — в фоне чтобы не блокировать при зависании CF
        msg = await bot.send_message(chat_id, "⏳ Отключаю WARP…")
        run_script_bg("--warp OFF")
        await asyncio.sleep(1)
        # Перезапускаем compose без WARP
        rc, out = run_script(timeout=60)
        await bot.edit_message_text(
            f"🔴 WARP отключён.\n<blockquote>{_snippet(out)}</blockquote>",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await send_settings_menu(bot, chat_id)

    elif cmd == "warp_free":
        msg = await bot.send_message(
            chat_id,
            "⏳ Включаю WARP…\n<i>Регистрация аккаунта, до ~30 секунд.</i>",
            parse_mode=ParseMode.HTML,
        )
        rc, out = run_script("--warp ON", timeout=120)
        status  = "🟢 WARP включён." if rc == 0 else "❌ Ошибка включения WARP."
        await bot.edit_message_text(
            f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await send_settings_menu(bot, chat_id)

    elif cmd == "sub":
        await _handle_sub(bot, chat_id, arg)

    elif cmd == "do_restart":
        msg = await bot.send_message(chat_id, "⏳ Применяю конфигурацию…")
        rc, out = run_script(timeout=180)
        status  = "✅ Готово." if rc == 0 else "⚠️ Завершено с ошибкой."
        await bot.edit_message_text(
            f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await send_settings_menu(bot, chat_id)

    elif cmd == "do_backup":
        msg  = await bot.send_message(chat_id, "📦 Создаю бэкап…")
        path = make_backup()
        if not path:
            await bot.edit_message_text("❌ Ошибка создания бэкапа.", chat_id=chat_id, message_id=msg.message_id)
            return
        await bot.send_document(chat_id, document=open(path, "rb"), filename=os.path.basename(path))
        os.remove(path)
        await bot.delete_message(chat_id, msg.message_id)


# ─────────────────────────────────────────────
# Обработчик текстовых сообщений (ввод)
# ─────────────────────────────────────────────

@restricted
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state   = context.user_data.pop("state", None)
    text    = update.message.text.strip()
    chat_id = update.effective_chat.id

    if state == "add_user":
        if not re.match(r"^[a-zA-Z0-9]+$", text):
            await update.message.reply_text("❌ Имя должно содержать только латиницу и цифры.")
            return
        msg = await update.message.reply_text(
            f"⏳ Создаю пользователя <b>{text}</b>…", parse_mode=ParseMode.HTML,
        )
        rc, out = run_script(f"--add-user {text}")
        if rc != 0:
            await context.bot.edit_message_text(
                f"❌ Ошибка:\n<blockquote>{_snippet(out)}</blockquote>",
                chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
            )
            return
        await context.bot.edit_message_text(
            f"✅ Пользователь <b>{text}</b> создан.",
            chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
        )
        await _send_user_links(context.bot, chat_id, text)

    elif state == "setting":
        param = context.user_data.pop("param", None)
        if not param:
            return
        if param == "port":
            if not text.isdigit() or not (1 <= int(text) <= 65535):
                await update.message.reply_text("❌ Порт: целое число от 1 до 65535.")
                return
        if param == "warp_license":
            if not re.match(r"^[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$", text):
                await update.message.reply_text(
                    "❌ Неверный формат.\nОжидается: <code>xxxxxxxx-xxxxxxxx-xxxxxxxx</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
        key = "service_path" if param == "path" else param
        await apply_setting(update, context, key, text)


# ─────────────────────────────────────────────
# /start  /restart  /backup
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(context.bot, update.effective_chat.id)


@restricted
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    msg     = await context.bot.send_message(chat_id, "⏳ Перезапуск…")
    rc, out = run_script(timeout=180)
    status  = "✅ Готово." if rc == 0 else "⚠️ Завершено с ошибкой."
    await context.bot.edit_message_text(
        f"{status}\n<blockquote>{_snippet(out)}</blockquote>",
        chat_id=chat_id, message_id=msg.message_id, parse_mode=ParseMode.HTML,
    )
    await send_settings_menu(context.bot, chat_id)


@restricted
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    msg     = await context.bot.send_message(chat_id, "📦 Создаю бэкап…")
    path    = make_backup()
    if not path:
        await context.bot.edit_message_text("❌ Ошибка.", chat_id=chat_id, message_id=msg.message_id)
        return
    await context.bot.send_document(chat_id, document=open(path, "rb"), filename=os.path.basename(path))
    os.remove(path)
    await context.bot.delete_message(chat_id, msg.message_id)


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────

def main() -> None:
    logger.info(f"Запуск Reality-EZPZ Bot | скрипт: {SCRIPT_PATH} | DATA_DIR: {DATA_DIR}")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("backup",  cmd_backup))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    logger.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
