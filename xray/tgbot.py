#!/usr/bin/env python3
import os
import re
import subprocess
import logging
import zipfile
from datetime import datetime
import io
import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_DIR = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')
USERS_FILE = os.path.join(DATA_DIR, 'users')

SCRIPT_URL = 'https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh'
BASE_CMD = (
    'function systemctl() { :; }; export -f systemctl; '
    f'bash <(curl -sL {SCRIPT_URL} | sed "s/docker run --rm -it/docker run --rm/g") '
)

TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise SystemExit("BOT_TOKEN env is not set")

ADMIN = os.environ.get('BOT_ADMIN', '')
username_regex = re.compile(r"^[a-zA-Z0-9]+$")


def run_script(extra_args='', timeout=300):
    cmd = BASE_CMD + extra_args
    try:
        proc = subprocess.Popen(
            cmd, shell=True, executable='/bin/bash',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = proc.communicate(timeout=timeout)
        out_s = out.decode(errors='ignore').strip()
        err_s = err.decode(errors='ignore').strip()
        if proc.returncode != 0 and err_s:
            combined = out_s + '\n' + err_s
        else:
            combined = out_s
        return proc.returncode, combined.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, "Команда заняла слишком много времени."
    except Exception as e:
        return 1, str(e)


def apply_reconfigure():
    _, out = run_script('')
    return out


def read_config():
    conf = {}
    if not os.path.exists(CONFIG_FILE):
        return conf
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for l in f:
                l = l.strip()
                if '=' in l and not l.startswith('#'):
                    k, v = l.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except:
        pass
    return conf


def write_config(key, value):
    """Атомарная запись key=value. Безопасна для любых значений."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        open(CONFIG_FILE, 'a').close()
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f'{key}='):
                new_lines.append(f'{key}={value}\n')
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f'{key}={value}\n')
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.error(f'write_config({key}): {e}')


def get_users():
    users = []
    try:
        with open(USERS_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    users.append(line.split('=', 1)[0].strip())
    except FileNotFoundError:
        pass
    return users


def get_user_conf(name):
    _, out = run_script(f'--show-user {name}', timeout=120)
    result = []
    for line in out.splitlines():
        s = line.strip()
        if s and ('://' in s or s.startswith('{"dns"')):
            result.append(s)
    return result


def make_backup():
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    fname = f"/tmp/backup_{ts}.zip"
    try:
        with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in ['config', 'users']:
                p = os.path.join(DATA_DIR, f)
                if os.path.exists(p):
                    z.write(p, arcname=f)
        return fname
    except:
        return None


def restricted(func):
    async def wrap(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        u = update.effective_user
        if not u:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Не удалось определить пользователя.")
            return
        uid = str(u.id)
        uname = (u.username or '').lstrip('@')
        admins = {x.strip().lstrip('@') for x in ADMIN.split(',') if x.strip()}
        if uid in admins or (uname and uname in admins):
            return await func(update, context, *a, **kw)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Нет доступа")
    return wrap


async def send_main_menu(bot, chat_id, text=None):
    if not text:
        text = "🤖 <b>Reality-EZPZ</b>"
    kb = [
        [InlineKeyboardButton("👥 Пользователи", callback_data="m_users")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="m_settings")]
    ]
    await bot.send_message(chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def send_settings_menu(bot, chat_id, text=None):
    c = read_config()
    warp = c.get("warp", "OFF")
    if not text:
        text = (
            "⚙️ <b>Настройки</b>\n"
            f"Core: <code>{c.get('core','?')}</code>\n"
            f"Transport: <code>{c.get('transport','?')}</code>\n"
            f"Security: <code>{c.get('security','?')}</code>\n"
            f"Port: <code>{c.get('port','?')}</code>\n"
            f"Server: <code>{c.get('server','?')}</code>\n"
            f"SNI: <code>{c.get('domain','?')}</code>\n"
            f"Path: <code>/{c.get('service_path','')}</code>\n"
            f"WARP: <b>{warp}</b>"
        )
    warp_btn = InlineKeyboardButton(
        "WARP OFF" if warp == "ON" else "WARP ON",
        callback_data="warp_off" if warp == "ON" else "sub!warp"
    )
    kb = [
        [
            InlineKeyboardButton("Core", callback_data="sub!core"),
            InlineKeyboardButton("Transport", callback_data="sub!transport")
        ],
        [
            InlineKeyboardButton("Security", callback_data="sub!security"),
            warp_btn
        ],
        [
            InlineKeyboardButton("Server", callback_data="ask!server"),
            InlineKeyboardButton("Port", callback_data="ask!port")
        ],
        [
            InlineKeyboardButton("SNI", callback_data="ask!domain"),
            InlineKeyboardButton("Path", callback_data="ask!path")
        ],
        [
            InlineKeyboardButton("Host", callback_data="ask!host_header")
        ],
        [InlineKeyboardButton("🔄 Перезапуск служб", callback_data="do_restart")],
        [InlineKeyboardButton("📥 Скачать бэкап", callback_data="do_backup")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main")]
    ]
    await bot.send_message(chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


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
        update.effective_chat.id, "👥 <b>Пользователи</b>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )


@restricted
async def users_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    users = get_users()
    if not users:
        await context.bot.send_message(update.effective_chat.id, "Список пуст.")
        return
    cb = "u_show" if mode == "show" else "u_del"
    kb = [[InlineKeyboardButton(u, callback_data=f"{cb}!{u}")] for u in users]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="m_users")])
    await context.bot.send_message(
        update.effective_chat.id, "Выберите пользователя:", reply_markup=InlineKeyboardMarkup(kb)
    )


@restricted
async def ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str):
    context.user_data["state"] = "setting"
    context.user_data["param"] = param
    hints = {
        'server':      'Введите IP или домен сервера:',
        'domain':      'Введите SNI/домен:',
        'port':        'Введите порт (1–65535):',
        'path':        'Введите путь (без /):\n(Отправьте / для очистки)',
        'host_header': 'Введите Host заголовок:',
        'warp_license':'Введите лицензию WARP+:\n<i>Формат: xxxxxxxx-xxxxxxxx-xxxxxxxx</i>',
    }
    txt = hints.get(param, f"Введите значение для <b>{param}</b>:")
    kb = [[InlineKeyboardButton("Отмена", callback_data="m_settings")]]
    await context.bot.send_message(
        update.effective_chat.id, txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )


@restricted
async def apply_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, val: str):
    chat_id = update.effective_chat.id
    if param == "transport":
        c = read_config()
        core = c.get("core", "xray")
        security = c.get("security", "reality")
        xray_only = {'xhttp', 'xicmp', 'xdns'}
        mkcp_only = {'xicmp', 'xdns'}
        if val in xray_only and core != 'xray':
            await context.bot.send_message(chat_id, f"⚠️ Транспорт <b>{val}</b> работает только с ядром <b>xray</b>. Сначала смените ядро.", parse_mode="HTML")
            await send_settings_menu(context.bot, chat_id)
            return
        if val in mkcp_only and security != 'reality':
            await context.bot.send_message(chat_id, f"⚠️ Транспорт <b>{val}</b> работает поверх mKCP и требует <b>security=reality</b>. Сначала смените security.", parse_mode="HTML")
            await send_settings_menu(context.bot, chat_id)
            return
    if param == "warp_license":
        await context.bot.send_message(chat_id, "⏳ Включаю WARP+...\nМожет занять 1–2 минуты.")
        rc, out = run_script(f'--warp-license {val}', timeout=240)
    elif param == "service_path" and (val == "/" or val == ""):
        write_config("service_path", "")
        await context.bot.send_message(chat_id, "⏳ Применяю настройки...")
        rc, out = run_script()
    else:
        write_config(param, val)
        await context.bot.send_message(chat_id, "⏳ Применяю настройки...")
        rc, out = run_script()
    snippet = out if len(out) < 3900 else out[:3900] + "\n...(truncated)"
    await context.bot.send_message(
        chat_id, f"✅ Готово.\n<blockquote>{snippet}</blockquote>", parse_mode="HTML"
    )
    await send_settings_menu(context.bot, chat_id)


@restricted
async def do_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "⏳ Перезапуск служб...")
    out = apply_reconfigure()
    snippet = out if len(out) < 3900 else out[:3900] + "\n...(truncated)"
    await context.bot.send_message(
        chat_id, f"✅ Перезапуск завершён.\n<blockquote>{snippet}</blockquote>", parse_mode="HTML"
    )
    await send_settings_menu(context.bot, chat_id)


@restricted
async def do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id, "📦 Создаю бэкап...")
    path = make_backup()
    if not path:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Ошибка создания бэкапа")
        return
    await context.bot.send_document(chat_id, document=open(path, "rb"), filename="backup.zip")
    os.remove(path)
    await context.bot.delete_message(chat_id, msg.message_id)


@restricted
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data
    data = raw.split("!")
    cmd = data[0]
    arg = data[1] if len(data) > 1 else ""
    arg2 = data[2] if len(data) > 2 else ""
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
            chat_id, "Введите имя пользователя:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="m_users")]])
        )
    elif cmd == "u_show":
        confs = get_user_conf(arg)
        for c in confs:
            if not c:
                continue
            try:
                qr = qrcode.make(c)
                bio = io.BytesIO()
                qr.save(bio, "PNG")
                bio.seek(0)
                await context.bot.send_photo(chat_id, photo=bio, caption=f"<code>{c[:1000]}</code>", parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id, f"<code>{c[:3000]}</code>", parse_mode="HTML")
        await context.bot.send_message(
            chat_id, "↩️ Вернуться к пользователям",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]])
        )
    elif cmd == "u_del":
        kb = [[
            InlineKeyboardButton("Да", callback_data=f"confirm_del!{arg}"),
            InlineKeyboardButton("Нет", callback_data="m_users")
        ]]
        await context.bot.send_message(chat_id, f"Удалить {arg}?", reply_markup=InlineKeyboardMarkup(kb))
    elif cmd == "confirm_del":
        run_script(f"--delete-user {arg}")
        await context.bot.send_message(chat_id, "Удалён.")
        await menu_users(update, context)
    elif cmd == "ask":
        await ask_input(update, context, arg)
    elif cmd == "set":
        await apply_setting(update, context, arg, arg2)
    elif cmd == "warp_off":
        write_config("warp", "OFF")
        await context.bot.send_message(chat_id, "⏳ Отключаю WARP...")
        rc, out = run_script()
        snippet = out if len(out) < 3900 else out[:3900] + "\n...(truncated)"
        await context.bot.send_message(
            chat_id, f"✅ WARP отключён.\n<blockquote>{snippet}</blockquote>", parse_mode="HTML"
        )
        await send_settings_menu(context.bot, chat_id)
    elif cmd == "warp_free":
        write_config("warp", "ON")
        write_config("warp_license", "")
        await context.bot.send_message(chat_id, "⏳ Включаю WARP...\nМожет занять 1–2 минуты.")
        rc, out = run_script(timeout=240)
        snippet = out if len(out) < 3900 else out[:3900] + "\n...(truncated)"
        await context.bot.send_message(
            chat_id, f"✅ WARP включён.\n<blockquote>{snippet}</blockquote>", parse_mode="HTML"
        )
        await send_settings_menu(context.bot, chat_id)
    elif cmd == "sub":
        if arg == "core":
            kb = [[
                InlineKeyboardButton("Xray", callback_data="set!core!xray"),
                InlineKeyboardButton("Sing-Box", callback_data="set!core!sing-box")
            ]]
        elif arg == "transport":
            opts = ['tcp', 'http', 'grpc', 'ws', 'xhttp', 'tuic', 'hysteria2', 'shadowtls', 'xicmp', 'xdns']
            kb = [
                [InlineKeyboardButton(o, callback_data=f"set!transport!{o}") for o in opts[i:i+3]]
                for i in range(0, len(opts), 3)
            ]
        elif arg == "security":
            kb = [
                [InlineKeyboardButton(o, callback_data=f"set!security!{o}")]
                for o in ['reality', 'letsencrypt', 'selfsigned', 'notls']
            ]
        elif arg == "warp":
            kb = [
                [InlineKeyboardButton("🆓 WARP бесплатный",    callback_data="warp_free")],
                [InlineKeyboardButton("⭐ WARP+ (с лицензией)", callback_data="ask!warp_license")],
                [InlineKeyboardButton("🔙", callback_data="m_settings")],
            ]
            await context.bot.send_message(chat_id, "Выберите тип WARP:", reply_markup=InlineKeyboardMarkup(kb))
            return
        kb.append([InlineKeyboardButton("🔙", callback_data="m_settings")])
        await context.bot.send_message(chat_id, f"Выберите {arg}:", reply_markup=InlineKeyboardMarkup(kb))
    elif cmd == "do_restart":
        await do_restart(update, context)
    elif cmd == "do_backup":
        await do_backup(update, context)


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
        run_script(f"--add-user {text}")
        await update.message.reply_text(f"✅ Создан: {text}")
        confs = get_user_conf(text)
        for c in confs:
            try:
                qr = qrcode.make(c)
                bio = io.BytesIO()
                qr.save(bio, "PNG")
                bio.seek(0)
                await context.bot.send_photo(chat_id, photo=bio, caption=f"<code>{c[:1000]}</code>", parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id, f"<code>{c[:3000]}</code>", parse_mode="HTML")
        await context.bot.send_message(
            chat_id, "↩️ Вернуться к пользователям",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]])
        )
    elif state == "setting":
        param = context.user_data.pop("param", None)
        if param == "port" and not text.isdigit():
            await update.message.reply_text("Порт должен быть числом.")
            return
        if param == "warp_license":
            if not re.match(r'^[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', text):
                await update.message.reply_text(
                    "❌ Неверный формат.\nОжидается: <code>xxxxxxxx-xxxxxxxx-xxxxxxxx</code>",
                    parse_mode="HTML"
                )
                return
        key = param
        if param == "path":
            key = "service_path"
        await apply_setting(update, context, key, text)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(context.bot, update.effective_chat.id)


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
