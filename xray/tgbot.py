#!/usr/bin/env python3
import os
import re
import subprocess
import logging
import zipfile
import asyncio
from typing import Optional, Dict
from datetime import datetime
import io

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    Application
)

# --- Пути и команды ---

DATA_DIR = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')
USERS_FILE = os.path.join(DATA_DIR, 'users')
RESTART_STATE_FILE = os.path.join(DATA_DIR, 'bot_restart_state.txt')

BASE_CMD = (
    'function systemctl() { :; }; export -f systemctl; '
    'bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh '
    '| sed "s/ -it / -i /g") '
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise SystemExit("BOT_TOKEN env is not set")
ADMIN = os.environ.get('BOT_ADMIN', '')

username_regex = re.compile(r"^[a-zA-Z0-9]+$")


# --- Системные функции ---

def run_sync(args: str) -> str:
    full = BASE_CMD + (args if args else "")
    try:
        proc = subprocess.Popen(
            full, shell=True, executable='/bin/bash',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = proc.communicate(timeout=120)
        out_s = out.decode(errors='ignore')
        err_s = err.decode(errors='ignore')
        if err_s:
            return (out_s + "\n" + err_s).strip()
        return out_s.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Команда заняла слишком много времени."
    except Exception as e:
        return str(e)


def apply_reconfigure() -> str:
    return run_sync("")


def read_config() -> Dict[str, str]:
    conf = {}
    if not os.path.exists(CONFIG_FILE):
        return conf
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except:
        pass
    return conf


def write_config(key: str, value: str):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        open(CONFIG_FILE, 'a').close()

    safe_val = value.replace('/', '\\/').replace('&', '\\&')
    exists = subprocess.call(f"grep -q '^{key}=' {CONFIG_FILE}",
                             shell=True, executable='/bin/bash') == 0

    if exists:
        cmd = f"sed -i 's/^{key}=.*/{key}={safe_val}/' {CONFIG_FILE}"
    else:
        cmd = f"echo '{key}={value}' >> {CONFIG_FILE}"

    subprocess.run(cmd, shell=True, executable='/bin/bash')


def get_users():
    out = run_sync("--list-users")
    return [
        u.strip() for u in out.splitlines()
        if u.strip() and "Using config" not in u and "Error" not in u
    ]


def get_user_conf(name):
    out = run_sync(f"--show-user {name} | grep -E '://|^\\{{\"dns\"'")
    return [l.strip() for l in out.splitlines() if l.strip()]


def make_backup():
    if not os.path.exists(DATA_DIR):
        return None
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    fname = f"/tmp/backup_{ts}.zip"
    try:
        with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in ['config', 'users']:
                path = os.path.join(DATA_DIR, f)
                if os.path.exists(path):
                    z.write(path, arcname=f)
        return fname
    except:
        return None


# --- Декоратор доступа ---

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if ADMIN and user_id != ADMIN:
            return
        return await func(update, context)
    return wrapper


# --- Главное меню ---

async def send_main_menu(bot, chat_id):
    kb = [
        [InlineKeyboardButton("👥 Пользователи", callback_data="m_users")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="m_settings")],
    ]
    await bot.send_message(
        chat_id,
        "🏠 <b>Главное меню</b>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )


# --- Настройки ---

async def send_settings_menu(bot, chat_id):
    kb = [
        [
            InlineKeyboardButton("Warp ON", callback_data="set!warp!ON"),
            InlineKeyboardButton("Warp OFF", callback_data="set!warp!OFF"),
        ],
        [
            InlineKeyboardButton("Перезапуск служб", callback_data="do_restart")
        ],
        [
            InlineKeyboardButton("📥 Скачать бэкап", callback_data="do_backup")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data="main")]
    ]

    await bot.send_message(
        chat_id,
        "<b>Настройки</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# --- Меню пользователей ---

@restricted
async def menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [
            InlineKeyboardButton("📜 Список", callback_data="u_list"),
            InlineKeyboardButton("➕ Добавить", callback_data="u_add")
        ],
        [
            InlineKeyboardButton("➖ Удалить", callback_data="u_del_m"),
            InlineKeyboardButton("🔙 Назад", callback_data="main")
        ]
    ]
    await context.bot.send_message(
        update.effective_chat.id,
        "👥 <b>Пользователи</b>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )


# --- Действия с пользователями ---

@restricted
async def users_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    users = get_users()
    kb = []

    if not users:
        await context.bot.send_message(update.effective_chat.id, "Список пуст.")
        return

    cb = "u_show" if mode == "show" else "u_del"

    for u in users:
        kb.append([InlineKeyboardButton(u, callback_data=f"{cb}!{u}")])

    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="m_users")])

    await context.bot.send_message(
        update.effective_chat.id,
        "Выберите пользователя:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# --- Запрос параметра ---

@restricted
async def ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str):
    context.user_data["state"] = "setting"
    context.user_data["param"] = param

    txt = f"Введите значение для <b>{param}</b>:"

    kb = [[InlineKeyboardButton("Отмена", callback_data="m_settings")]]
    await context.bot.send_message(
        update.effective_chat.id, txt,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )


# --- Применение настройки ---

@restricted
async def apply_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, val: str):
    chat_id = update.effective_chat.id

    if param == "warp_license":
        write_config("warp", "ON")
        write_config("warp_license", val)
    elif param == "warp" and val == "OFF":
        write_config("warp", "OFF")
    else:
        write_config(param, val)

    await context.bot.send_message(chat_id, "⏳ Применяю настройки...")
    out = apply_reconfigure()

    await context.bot.send_message(
        chat_id,
        f"✅ Настройки применены.\n\n<code>{out}</code>",
        parse_mode="HTML"
    )

    await send_settings_menu(context.bot, chat_id)


# --- Перезапуск ---

@restricted
async def do_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "⏳ Перезапуск служб...")
    out = apply_reconfigure()

    await context.bot.send_message(
        chat_id,
        f"✅ Перезапуск завершён.\n\n<code>{out}</code>",
        parse_mode="HTML"
    )
    await send_settings_menu(context.bot, chat_id)


# --- Создать бэкап ---

@restricted
async def do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id, "📦 Создаю бэкап...")

    path = make_backup()
    if not path:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text="❌ Ошибка создания бэкапа"
        )
        return

    await context.bot.send_document(chat_id, document=open(path, "rb"), filename="backup.zip")
    os.remove(path)
    await context.bot.delete_message(chat_id, msg.message_id)


# --- Обработчик callback'ов ---

@restricted
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split("!")

    cmd = data[0]
    arg = data[1] if len(data) > 1 else ""

    await query.answer()
    chat_id = update.effective_chat.id

    if cmd == "main":
        await send_main_menu(context.bot, chat_id)

    elif cmd == "m_users":
        await menu_users(update, context)

    elif cmd == "m_settings":
        await send_settings_menu(context.bot, chat_id)

    elif cmd == "u_list":
        await users_action(update, context, "show")

    elif cmd == "u_del_m":
        await users_action(update, context, "del")

    elif cmd == "u_add":
        context.user_data["state"] = "add_user"
        await context.bot.send_message(
            chat_id,
            "Введите имя пользователя:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="m_users")]])
        )

    elif cmd == "u_show":
        confs = get_user_conf(arg)
        for c in confs:
            qr = qrcode.make(c)
            bio = io.BytesIO()
            qr.save(bio, "PNG")
            bio.seek(0)

            await context.bot.send_photo(
                chat_id,
                photo=bio,
                caption=f"<code>{c}</code>",
                parse_mode="HTML"
            )

        await context.bot.send_message(
            chat_id,
            "↩️ Вернуться к пользователям",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]]
            )
        )

    elif cmd == "u_del":
        kb = [
            [
                InlineKeyboardButton("Да", callback_data=f"confirm_del!{arg}"),
                InlineKeyboardButton("Нет", callback_data="m_users")
            ]
        ]
        await context.bot.send_message(chat_id, f"Удалить {arg}?", reply_markup=InlineKeyboardMarkup(kb))

    elif cmd == "confirm_del":
        run_sync(f"--delete-user {arg}")
        await context.bot.send_message(chat_id, "Удалён.")
        await menu_users(update, context)

    elif cmd == "set":
        await apply_setting(update, context, arg, data[2] if len(data) > 2 else "")

    elif cmd == "do_restart":
        await do_restart(update, context)

    elif cmd == "do_backup":
        await do_backup(update, context)


# --- Текстовые сообщения ---

@restricted
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.pop("state", None)
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    if state == "add_user":
        if not username_regex.match(text):
            await update.message.reply_text("❌ Недопустимое имя.")
            return

        await update.message.reply_text("Создаю пользователя...")

        run_sync(f"--add-user {text}")

        await update.message.reply_text(f"✅ Создан: {text}")

        confs = get_user_conf(text)
        for c in confs:
            qr = qrcode.make(c)
            bio = io.BytesIO()
            qr.save(bio, "PNG")
            bio.seek(0)
            await context.bot.send_photo(
                chat_id,
                photo=bio,
                caption=f"<code>{c}</code>",
                parse_mode="HTML"
            )

        await context.bot.send_message(
            chat_id,
            "↩️ Вернуться к пользователям",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]])
        )

    elif state == "setting":
        param = context.user_data.pop("param", None)

        if param == "port" and not text.isdigit():
            await update.message.reply_text("Порт должен быть числом.")
            return

        key = param
        if param == "path":
            key = "service_path"

        await apply_setting(update, context, key, text)


# --- MAIN ---

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", send_main_menu))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()