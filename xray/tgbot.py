#!/usr/bin/env python3
"""
Reality-EZPZ Telegram Bot
Управление через Telegram. Меню транспорта и security фильтруются по выбранному ядру.
"""
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

DATA_DIR    = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')
USERS_FILE  = os.path.join(DATA_DIR, 'users')

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

# Транспорты по ядру
TRANSPORTS = {
    'xray':     ['tcp', 'http', 'xhttp', 'grpc', 'ws', 'mkcp'],
    'sing-box': ['tcp', 'http', 'grpc', 'ws', 'tuic', 'hysteria2', 'shadowtls'],
}

# Security по транспорту
SECURITY_FOR_TRANSPORT = {
    'tcp':       ['reality', 'letsencrypt', 'selfsigned', 'notls'],
    'http':      ['reality', 'letsencrypt', 'selfsigned', 'notls'],
    'xhttp':     ['reality', 'letsencrypt', 'selfsigned', 'notls'],
    'grpc':      ['reality', 'letsencrypt', 'selfsigned', 'notls'],
    'ws':        ['letsencrypt', 'selfsigned', 'notls'],
    'mkcp':      ['reality', 'notls', 'xicmp', 'xdns'],
    'tuic':      ['selfsigned'],
    'hysteria2': ['selfsigned'],
    'shadowtls': [],
}

SECURITY_HINTS = {
    'reality':     'REALITY (рекомендуется)',
    'letsencrypt': "Let's Encrypt (нужен домен)",
    'selfsigned':  'Самоподписанный сертификат',
    'notls':       'Без TLS',
    'xicmp':       'xICMP финальная маска (mkcp)',
    'xdns':        'xDNS финальная маска (mkcp)',
}


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
        combined = (out_s + '\n' + err_s).strip() if (proc.returncode != 0 and err_s) else out_s
        return proc.returncode, combined.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, "Команда заняла слишком много времени."
    except Exception as e:
        return 1, str(e)


def apply_reconfigure(timeout=300):
    _, out = run_script('', timeout=timeout)
    return out


def read_config():
    conf = {}
    if not os.path.exists(CONFIG_FILE):
        return conf
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return conf


def write_config(key, value):
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
    except Exception:
        return None


def get_available_transports(core):
    return TRANSPORTS.get(core, TRANSPORTS['xray'])


def get_available_security(transport):
    return SECURITY_FOR_TRANSPORT.get(transport, ['reality', 'letsencrypt', 'selfsigned', 'notls'])


def snippet(text, limit=3900):
    return text if len(text) <= limit else text[:limit] + '\n…(обрезано)'


def restricted(func):
    async def wrap(update, context, *a, **kw):
        u = update.effective_user
        if not u:
            await context.bot.send_message(update.effective_chat.id, "Не удалось определить пользователя.")
            return
        uid = str(u.id)
        uname = (u.username or '').lstrip('@')
        admins = {x.strip().lstrip('@') for x in ADMIN.split(',') if x.strip()}
        if uid in admins or (uname and uname in admins):
            return await func(update, context, *a, **kw)
        await context.bot.send_message(update.effective_chat.id, "⛔ Нет доступа.")
    return wrap


async def send_main_menu(bot, chat_id, text=None):
    text = text or "🤖 <b>Reality-EZPZ</b>"
    kb = [
        [InlineKeyboardButton("👥 Пользователи", callback_data="m_users")],
        [InlineKeyboardButton("⚙️ Настройки",    callback_data="m_settings")],
    ]
    await bot.send_message(chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def send_settings_menu(bot, chat_id, text=None):
    c = read_config()
    warp = c.get('warp', 'OFF')
    text = text or (
        "⚙️ <b>Настройки</b>\n"
        f"Core:      <code>{c.get('core','?')}</code>\n"
        f"Transport: <code>{c.get('transport','?')}</code>\n"
        f"Security:  <code>{c.get('security','?')}</code>\n"
        f"Port:      <code>{c.get('port','?')}</code>\n"
        f"Server:    <code>{c.get('server','?')}</code>\n"
        f"SNI:       <code>{c.get('domain','?')}</code>\n"
        f"Path:      <code>/{c.get('service_path','')}</code>\n"
        f"WARP:      <b>{warp}</b>"
    )
    warp_btn = InlineKeyboardButton(
        "⛔ WARP OFF" if warp == "ON" else "✅ WARP ON",
        callback_data="warp_off" if warp == "ON" else "sub!warp"
    )
    kb = [
        [InlineKeyboardButton("🔧 Core",       callback_data="sub!core"),
         InlineKeyboardButton("🚀 Transport",  callback_data="sub!transport")],
        [InlineKeyboardButton("🔒 Security",   callback_data="sub!security"),
         warp_btn],
        [InlineKeyboardButton("🖥 Server",     callback_data="ask!server"),
         InlineKeyboardButton("🔌 Port",       callback_data="ask!port")],
        [InlineKeyboardButton("🌐 SNI",        callback_data="ask!domain"),
         InlineKeyboardButton("📂 Path",       callback_data="ask!path")],
        [InlineKeyboardButton("🏠 Host",       callback_data="ask!host_header")],
        [InlineKeyboardButton("🔄 Перезапуск служб", callback_data="do_restart")],
        [InlineKeyboardButton("📥 Скачать бэкап",    callback_data="do_backup")],
        [InlineKeyboardButton("🔙 Главное меню",      callback_data="main")],
    ]
    await bot.send_message(chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


@restricted
async def menu_users(update, context):
    kb = [
        [InlineKeyboardButton("📜 Список",   callback_data="u_list"),
         InlineKeyboardButton("➕ Добавить", callback_data="u_add")],
        [InlineKeyboardButton("➖ Удалить",  callback_data="u_del_m"),
         InlineKeyboardButton("🔙 Назад",   callback_data="main")],
    ]
    await context.bot.send_message(
        update.effective_chat.id, "👥 <b>Пользователи</b>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )


@restricted
async def users_action(update, context, mode):
    users = get_users()
    if not users:
        await context.bot.send_message(update.effective_chat.id, "Список пользователей пуст.")
        return
    cb = "u_show" if mode == "show" else "u_del"
    kb = [[InlineKeyboardButton(u, callback_data=f"{cb}!{u}")] for u in users]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="m_users")])
    await context.bot.send_message(
        update.effective_chat.id, "Выберите пользователя:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


@restricted
async def ask_input(update, context, param):
    context.user_data['state'] = 'setting'
    context.user_data['param'] = param
    hints = {
        'server':       'Введите IP или домен сервера:',
        'domain':       'Введите SNI-домен:',
        'port':         'Введите порт (1–65535):',
        'path':         'Введите путь (без /):\n<i>Отправьте / для сброса</i>',
        'host_header':  'Введите Host-заголовок:',
        'warp_license': 'Введите лицензию WARP+:\n<i>Формат: xxxxxxxx-xxxxxxxx-xxxxxxxx</i>',
    }
    txt = hints.get(param, f"Введите значение для <b>{param}</b>:")
    kb = [[InlineKeyboardButton("Отмена", callback_data="m_settings")]]
    await context.bot.send_message(
        update.effective_chat.id, txt,
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )


@restricted
async def apply_setting(update, context, param, val):
    chat_id = update.effective_chat.id
    c = read_config()

    if param == 'transport':
        core = c.get('core', 'xray')
        allowed = get_available_transports(core)
        if val not in allowed:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Транспорт <b>{val}</b> недоступен для ядра <b>{core}</b>.\n"
                f"Доступные: {', '.join(allowed)}",
                parse_mode="HTML"
            )
            await send_settings_menu(context.bot, chat_id)
            return
        cur_security = c.get('security', 'reality')
        allowed_sec  = get_available_security(val)
        if allowed_sec and cur_security not in allowed_sec:
            new_sec = allowed_sec[0]
            write_config('security', new_sec)
            await context.bot.send_message(
                chat_id,
                f"ℹ️ Security автоматически изменён: <b>{cur_security}</b> → <b>{new_sec}</b>",
                parse_mode="HTML"
            )

    if param == 'security':
        transport = c.get('transport', 'tcp')
        allowed_sec = get_available_security(transport)
        if not allowed_sec:
            await context.bot.send_message(
                chat_id,
                f"ℹ️ Для транспорта <b>{transport}</b> security не настраивается.",
                parse_mode="HTML"
            )
            await send_settings_menu(context.bot, chat_id)
            return
        if val not in allowed_sec:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Security <b>{val}</b> недоступна для транспорта <b>{transport}</b>.\n"
                f"Доступные: {', '.join(allowed_sec)}",
                parse_mode="HTML"
            )
            await send_settings_menu(context.bot, chat_id)
            return
        if val == 'letsencrypt':
            server = c.get('server', '')
            if re.match(r'^(\d{1,3}\.){3}\d{1,3}$', server) or not server:
                await context.bot.send_message(
                    chat_id,
                    "⚠️ <b>letsencrypt</b> требует домен в поле Server (не IP).",
                    parse_mode="HTML"
                )
                await send_settings_menu(context.bot, chat_id)
                return

    if param == 'warp_license':
        await context.bot.send_message(chat_id, "⏳ Включаю WARP+…\nМожет занять 1–2 минуты.")
        rc, out = run_script(f'--warp-license {val}', timeout=300)
        await context.bot.send_message(
            chat_id,
            f"{'✅ WARP+ активирован.' if rc == 0 else '❌ Ошибка WARP+.'}\n"
            f"<blockquote>{snippet(out)}</blockquote>",
            parse_mode="HTML"
        )
        await send_settings_menu(context.bot, chat_id)
        return

    if param == 'service_path' and val in ('/', ''):
        write_config('service_path', '')
        await context.bot.send_message(chat_id, "⏳ Применяю настройки…")
        rc, out = run_script()
    else:
        write_config(param, val)
        await context.bot.send_message(chat_id, "⏳ Применяю настройки…")
        rc, out = run_script()

    await context.bot.send_message(
        chat_id,
        f"{'✅ Готово.' if rc == 0 else '❌ Ошибка.'}\n<blockquote>{snippet(out)}</blockquote>",
        parse_mode="HTML"
    )
    await send_settings_menu(context.bot, chat_id)


@restricted
async def do_restart(update, context):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "⏳ Перезапуск служб…")
    out = apply_reconfigure()
    await context.bot.send_message(
        chat_id,
        f"✅ Перезапуск завершён.\n<blockquote>{snippet(out)}</blockquote>",
        parse_mode="HTML"
    )
    await send_settings_menu(context.bot, chat_id)


@restricted
async def do_backup(update, context):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id, "📦 Создаю бэкап…")
    path = make_backup()
    if not path:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Ошибка бэкапа")
        return
    await context.bot.send_document(chat_id, document=open(path, 'rb'), filename='backup.zip')
    os.remove(path)
    await context.bot.delete_message(chat_id, msg.message_id)


@restricted
async def cb_handler(update, context):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('!')
    cmd  = parts[0]
    arg  = parts[1] if len(parts) > 1 else ''
    arg2 = parts[2] if len(parts) > 2 else ''
    chat_id = update.effective_chat.id
    c = read_config()

    if cmd == 'main':
        await send_main_menu(context.bot, chat_id)

    elif cmd == 'm_users':
        await menu_users(update, context)

    elif cmd == 'm_settings':
        await send_settings_menu(context.bot, chat_id)

    elif cmd == 'u_list':
        await users_action(update, context, 'show')

    elif cmd == 'u_del_m':
        await users_action(update, context, 'del')

    elif cmd == 'u_add':
        context.user_data['state'] = 'add_user'
        await context.bot.send_message(
            chat_id, "Введите имя нового пользователя:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="m_users")]])
        )

    elif cmd == 'u_show':
        confs = get_user_conf(arg)
        if not confs:
            await context.bot.send_message(chat_id, f"Конфигурация для {arg} не найдена.")
        for conf_str in confs:
            if not conf_str:
                continue
            try:
                qr = qrcode.make(conf_str)
                bio = io.BytesIO()
                qr.save(bio, 'PNG')
                bio.seek(0)
                await context.bot.send_photo(
                    chat_id, photo=bio,
                    caption=f"<code>{conf_str[:1000]}</code>", parse_mode="HTML"
                )
            except Exception:
                await context.bot.send_message(chat_id, f"<code>{conf_str[:3000]}</code>", parse_mode="HTML")
        await context.bot.send_message(
            chat_id, "↩️ Назад к пользователям",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="m_users")]])
        )

    elif cmd == 'u_del':
        kb = [[
            InlineKeyboardButton("✅ Да",  callback_data=f"confirm_del!{arg}"),
            InlineKeyboardButton("❌ Нет", callback_data="m_users")
        ]]
        await context.bot.send_message(
            chat_id, f"Удалить пользователя <b>{arg}</b>?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
        )

    elif cmd == 'confirm_del':
        run_script(f'--delete-user {arg}')
        await context.bot.send_message(chat_id, f"✅ Пользователь <b>{arg}</b> удалён.", parse_mode="HTML")
        await menu_users(update, context)

    elif cmd == 'ask':
        await ask_input(update, context, arg)

    elif cmd == 'set':
        await apply_setting(update, context, arg, arg2)

    elif cmd == 'warp_off':
        write_config('warp', 'OFF')
        await context.bot.send_message(chat_id, "⏳ Отключаю WARP…")
        rc, out = run_script()
        await context.bot.send_message(
            chat_id,
            f"{'✅ WARP отключён.' if rc == 0 else '❌ Ошибка.'}\n<blockquote>{snippet(out)}</blockquote>",
            parse_mode="HTML"
        )
        await send_settings_menu(context.bot, chat_id)

    elif cmd == 'warp_free':
        write_config('warp', 'ON')
        write_config('warp_license', '')
        await context.bot.send_message(chat_id, "⏳ Включаю WARP…\nЭто может занять 1–2 минуты.")
        rc, out = run_script(timeout=300)
        await context.bot.send_message(
            chat_id,
            f"{'✅ WARP включён.' if rc == 0 else '❌ Ошибка включения WARP.'}\n<blockquote>{snippet(out)}</blockquote>",
            parse_mode="HTML"
        )
        await send_settings_menu(context.bot, chat_id)

    elif cmd == 'sub':
        if arg == 'core':
            kb = [
                [InlineKeyboardButton("⚡ Xray",     callback_data="set!core!xray"),
                 InlineKeyboardButton("📦 sing-box", callback_data="set!core!sing-box")],
                [InlineKeyboardButton("🔙", callback_data="m_settings")],
            ]
            await context.bot.send_message(chat_id, "Выберите ядро:", reply_markup=InlineKeyboardMarkup(kb))

        elif arg == 'transport':
            core = c.get('core', 'xray')
            cur  = c.get('transport', 'tcp')
            opts = get_available_transports(core)
            rows = []
            for opt in opts:
                label = f"✅ {opt}" if opt == cur else opt
                rows.append([InlineKeyboardButton(label, callback_data=f"set!transport!{opt}")])
            rows.append([InlineKeyboardButton("🔙", callback_data="m_settings")])
            await context.bot.send_message(
                chat_id,
                f"Выберите транспорт <i>(ядро: {core})</i>:",
                reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML"
            )

        elif arg == 'security':
            transport = c.get('transport', 'tcp')
            cur       = c.get('security', 'reality')
            opts      = get_available_security(transport)
            if not opts:
                await context.bot.send_message(
                    chat_id,
                    f"ℹ️ Для транспорта <b>{transport}</b> security не настраивается (встроен TLS).",
                    parse_mode="HTML"
                )
                await send_settings_menu(context.bot, chat_id)
                return
            rows = []
            for opt in opts:
                hint  = SECURITY_HINTS.get(opt, opt)
                label = f"✅ {hint}" if opt == cur else hint
                rows.append([InlineKeyboardButton(label, callback_data=f"set!security!{opt}")])
            rows.append([InlineKeyboardButton("🔙", callback_data="m_settings")])
            await context.bot.send_message(
                chat_id,
                f"Выберите security <i>(транспорт: {transport})</i>:",
                reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML"
            )

        elif arg == 'warp':
            kb = [
                [InlineKeyboardButton("🆓 WARP бесплатный",     callback_data="warp_free")],
                [InlineKeyboardButton("⭐ WARP+ (с лицензией)", callback_data="ask!warp_license")],
                [InlineKeyboardButton("🔙",                      callback_data="m_settings")],
            ]
            await context.bot.send_message(chat_id, "Выберите тип WARP:", reply_markup=InlineKeyboardMarkup(kb))

    elif cmd == 'do_restart':
        await do_restart(update, context)

    elif cmd == 'do_backup':
        await do_backup(update, context)


@restricted
async def msg_handler(update, context):
    state = context.user_data.pop('state', None)
    text  = update.message.text.strip()
    chat_id = update.effective_chat.id

    if state == 'add_user':
        if not username_regex.match(text):
            await update.message.reply_text("❌ Только латиница и цифры (A-Z, a-z, 0-9).")
            return
        await update.message.reply_text(f"⏳ Создаю пользователя <b>{text}</b>…", parse_mode="HTML")
        run_script(f'--add-user {text}')
        confs = get_user_conf(text)
        if confs:
            for conf_str in confs:
                try:
                    qr = qrcode.make(conf_str)
                    bio = io.BytesIO()
                    qr.save(bio, 'PNG')
                    bio.seek(0)
                    await context.bot.send_photo(
                        chat_id, photo=bio,
                        caption=f"<code>{conf_str[:1000]}</code>", parse_mode="HTML"
                    )
                except Exception:
                    await context.bot.send_message(chat_id, f"<code>{conf_str[:3000]}</code>", parse_mode="HTML")
        else:
            await update.message.reply_text(f"✅ Пользователь <b>{text}</b> создан.", parse_mode="HTML")
        await context.bot.send_message(
            chat_id, "↩️",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К пользователям", callback_data="m_users")]])
        )

    elif state == 'setting':
        param = context.user_data.pop('param', None)
        if not param:
            await update.message.reply_text("Неизвестный параметр. Начните заново.")
            return
        if param == 'port':
            if not text.isdigit() or not (1 <= int(text) <= 65535):
                await update.message.reply_text("❌ Порт должен быть числом от 1 до 65535.")
                return
        if param == 'warp_license':
            if not re.match(r'^[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', text):
                await update.message.reply_text(
                    "❌ Неверный формат.\nОжидается: <code>xxxxxxxx-xxxxxxxx-xxxxxxxx</code>",
                    parse_mode="HTML"
                )
                return
        key = 'service_path' if param == 'path' else param
        await apply_setting(update, context, key, text)


async def start_handler(update, context):
    await send_main_menu(context.bot, update.effective_chat.id)


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
