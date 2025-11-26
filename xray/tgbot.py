#!/usr/bin/env python3
import os
import re
import subprocess
import logging
import zipfile
import asyncio
from typing import Optional, Dict
from datetime import datetime

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    Application
)

# --- Пути и Команды ---

DATA_DIR = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')
RESTART_STATE_FILE = os.path.join(DATA_DIR, 'bot_restart_state.txt')

# Команда запуска с патчами:
# 1. Заглушка systemctl (тихая).
# 2. sed удаляет -it, чтобы не требовался TTY.
BASE_CMD = 'function systemctl() { :; }; export -f systemctl; bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh | sed "s/ -it / -i /g") '

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise SystemExit("BOT_TOKEN env is not set")
ADMIN = os.environ.get('BOT_ADMIN', '')
username_regex = re.compile(r"^[a-zA-Z0-9]+$")

# --- Управление состоянием (для приветствия после рестарта) ---

def save_restart_state(chat_id):
    """Сохраняем ID чата, чтобы написать туда после рестарта."""
    try:
        with open(RESTART_STATE_FILE, 'w') as f:
            f.write(str(chat_id))
    except Exception as e:
        logger.error(f"Save state failed: {e}")

async def check_startup_message(app: Application):
    """Запускается при старте бота."""
    if os.path.exists(RESTART_STATE_FILE):
        try:
            with open(RESTART_STATE_FILE, 'r') as f:
                chat_id = int(f.read().strip())
            os.remove(RESTART_STATE_FILE)
            # Отправляем главное меню
            await send_settings_menu(app.bot, chat_id, text="✅ Сервер успешно перезагружен! Настройки применены.")
        except Exception:
            pass

# --- Работа с конфигом и системой ---

def read_config() -> Dict[str, str]:
    config = {}
    if not os.path.exists(CONFIG_FILE): return config
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip().strip('"').strip("'")
    except: pass
    return config

def write_config(key: str, value: str):
    """Прямая запись в файл конфига."""
    if not os.path.exists(CONFIG_FILE): return
    safe_val = value.replace('/', '\\/').replace('&', '\\&')
    # Проверяем наличие
    ret = subprocess.call(f"grep -q '^{key}=' {CONFIG_FILE}", shell=True, executable='/bin/bash')
    if ret == 0:
        cmd = f"sed -i 's/^{key}=.*/{key}={safe_val}/' {CONFIG_FILE}"
    else:
        cmd = f"echo '{key}={value}' >> {CONFIG_FILE}"
    subprocess.run(cmd, shell=True, executable='/bin/bash')

def run_sync(args: str) -> str:
    """Для команд, которые НЕ убивают контейнер (список юзеров, show user)."""
    full = BASE_CMD + args
    try:
        proc = subprocess.Popen(full, shell=True, executable='/bin/bash', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=60)
        return out.decode()
    except Exception as e:
        return str(e)

def fire_and_forget_restart():
    """
    Запускает рестарт и НЕ ждет ответа.
    Контейнер умрет, Docker его поднимет. Бот проснется и выполнит check_startup_message.
    """
    full = BASE_CMD + "-r"
    subprocess.Popen(full, shell=True, executable='/bin/bash', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_users():
    out = run_sync('--list-users')
    return [l.strip() for l in out.splitlines() if l.strip() and "Using config" not in l and "Error" not in l]

def get_user_conf(name):
    out = run_sync(f"--show-user {name} | grep -E '://|^\\{{\"dns\"'")
    return [l.strip() for l in out.splitlines() if l.strip()]

def make_backup():
    if not os.path.exists(DATA_DIR): return None
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    fname = f"/tmp/backup_{ts}.zip"
    try:
        with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in ['config', 'users']:
                p = os.path.join(DATA_DIR, f)
                if os.path.exists(p): z.write(p, arcname=f)
        return fname
    except: return None

# --- Декоратор ---
def restricted(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        u = update.effective_user
        uid = u.id if u else 0
        uname = u.username if u else ""
        admins = [a.strip() for a in ADMIN.split(',') if a.strip()]
        if str(uid) in admins or (uname and uname in admins):
            return await func(update, context, *args, **kwargs)
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text='⛔ Нет доступа')
    return wrapped

# --- Меню ---

async def send_main_menu(bot, chat_id, text=None):
    if not text: text = "🤖 <b>Reality-EZPZ</b>"
    kb = [
        [InlineKeyboardButton('👥 Пользователи', callback_data='m_users')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='m_settings')],
        [InlineKeyboardButton('📥 Бэкап', callback_data='do_backup')]
    ]
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def send_settings_menu(bot, chat_id, text=None):
    c = read_config()
    warp_status = c.get('warp', 'OFF')
    
    if not text:
        text = (
            "⚙️ <b>Настройки</b>\n\n"
            f"Core: <code>{c.get('core','?')}</code>\n"
            f"Transport: <code>{c.get('transport','?')}</code>\n"
            f"Security: <code>{c.get('security','?')}</code>\n"
            f"Port: <code>{c.get('port','?')}</code>\n"
            f"SNI: <code>{c.get('domain','?')}</code>\n"
            f"Path: <code>/{c.get('service_path','')}</code>\n"
            f"WARP: <b>{warp_status}</b>"
        )

    # Динамическая кнопка WARP
    if warp_status == 'ON':
        warp_btn = InlineKeyboardButton('❌ Выключить WARP', callback_data='set!warp!OFF')
    else:
        warp_btn = InlineKeyboardButton('🔑 Включить WARP', callback_data='ask!warp_license')

    kb = [
        [InlineKeyboardButton('Core', callback_data='sub!core'), InlineKeyboardButton('Transport', callback_data='sub!transport')],
        [InlineKeyboardButton('Security', callback_data='sub!security'), warp_btn],
        [InlineKeyboardButton('Port', callback_data='ask!port'), InlineKeyboardButton('SNI', callback_data='ask!domain')],
        [InlineKeyboardButton('Path', callback_data='ask!path'), InlineKeyboardButton('Host', callback_data='ask!host_header')],
        [InlineKeyboardButton('🔄 Перезапуск служб', callback_data='do_restart')],
        [InlineKeyboardButton('🔙 Главное меню', callback_data='main')]
    ]
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# --- Handlers ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(context.bot, update.effective_chat.id)

@restricted
async def menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton('📜 Список', callback_data='u_list'), InlineKeyboardButton('➕ Добавить', callback_data='u_add')],
        [InlineKeyboardButton('➖ Удалить', callback_data='u_del_m'), InlineKeyboardButton('🔙 Назад', callback_data='main')]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="👥 <b>Пользователи</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

@restricted
async def users_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    users = get_users()
    kb = []
    cb = "u_show" if mode == 'show' else "u_del"
    if not users:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Пусто.")
        return
    for u in users:
        kb.append([InlineKeyboardButton(u, callback_data=f'{cb}!{u}')])
    kb.append([InlineKeyboardButton('🔙 Назад', callback_data='m_users')])
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Выберите:", reply_markup=InlineKeyboardMarkup(kb))

@restricted
async def ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str):
    context.user_data['state'] = 'setting'
    context.user_data['param'] = param
    
    txt = f"Введите значение для <b>{param}</b>:"
    if param == 'path': txt += "\n(Отправьте / для очистки)"
    
    kb = [[InlineKeyboardButton('Отмена', callback_data='m_settings')]]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

@restricted
async def apply_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, val: str):
    chat_id = update.effective_chat.id
    
    # 1. Запись в конфиг
    if param == 'warp_license':
        write_config('warp', 'ON')
        write_config('warp_license', val)
    elif param == 'warp' and val == 'OFF':
        write_config('warp', 'OFF')
    elif param == 'service_path' and (val == '/' or val == ''):
        write_config('service_path', '')
    else:
        write_config(param, val)
    
    # 2. Сохраняем стейт и рестартим
    save_restart_state(chat_id)
    await context.bot.send_message(chat_id=chat_id, text="⏳ Применяю настройки и перезагружаюсь... (10-15 сек)")
    fire_and_forget_restart()

@restricted
async def do_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    save_restart_state(chat_id)
    await context.bot.send_message(chat_id=chat_id, text="⏳ Перезагрузка служб... (10-15 сек)")
    fire_and_forget_restart()

@restricted
async def do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="📦 Архивация...")
    path = make_backup()
    if path:
        await context.bot.send_document(chat_id=chat_id, document=open(path, 'rb'), filename="backup.zip")
        os.remove(path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Ошибка бэкапа")

# --- Callbacks ---
@restricted
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('!')
    cmd = data[0]
    arg = data[1] if len(data)>1 else ""
    arg2 = data[2] if len(data)>2 else ""

    if cmd == 'main': await send_main_menu(context.bot, update.effective_chat.id)
    elif cmd == 'm_users': await menu_users(update, context)
    elif cmd == 'm_settings': await send_settings_menu(context.bot, update.effective_chat.id)
    elif cmd == 'u_list': await users_action(update, context, 'show')
    elif cmd == 'u_del_m': await users_action(update, context, 'del')
    
    elif cmd == 'u_show':
        confs = get_user_conf(arg)
        for c in confs:
            if not c: continue
            qr = qrcode.make(c)
            bio = io.BytesIO(); qr.save(bio, 'PNG'); bio.seek(0)
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=bio, caption=f"<code>{c}</code>", parse_mode='HTML')
            
    elif cmd == 'u_add':
        context.user_data['state'] = 'add_user'
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите имя:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='m_users')]]))
        
    elif cmd == 'u_del':
        kb = [[InlineKeyboardButton('Да', callback_data=f'confirm_del!{arg}'), InlineKeyboardButton('Нет', callback_data='m_users')]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Удалить {arg}?", reply_markup=InlineKeyboardMarkup(kb))
        
    elif cmd == 'confirm_del':
        # Удаление не требует полного рестарта контейнера (обычно), но если потребует - бот зависнет.
        # В оригинале add/del юзеров работает быстро. Оставим синхронно.
        run_sync(f'--delete-user {arg}')
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Удален.")
        await menu_users(update, context)
        
    elif cmd == 'ask': await ask_input(update, context, arg)
    elif cmd == 'set': await apply_setting(update, context, arg, arg2) # set!warp!OFF
    
    elif cmd == 'sub': # Submenu for Core, Transport etc
        kb = []
        if arg == 'core':
            kb = [[InlineKeyboardButton('Xray', callback_data='set!core!xray'), InlineKeyboardButton('Sing-Box', callback_data='set!core!sing-box')]]
        elif arg == 'transport':
             opts = ['tcp','http','grpc','ws','xhttp','tuic','hysteria2','shadowtls']
             kb = [ [InlineKeyboardButton(o, callback_data=f'set!transport!{o}') for o in opts[i:i+3]] for i in range(0, len(opts), 3) ]
        elif arg == 'security':
             kb = [[InlineKeyboardButton(o, callback_data=f'set!security!{o}')] for o in ['reality','letsencrypt','selfsigned','notls']]
        
        kb.append([InlineKeyboardButton('🔙', callback_data='m_settings')])
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Выберите {arg}:", reply_markup=InlineKeyboardMarkup(kb))
        
    elif cmd == 'do_restart': await do_restart(update, context)
    elif cmd == 'do_backup': await do_backup(update, context)

# --- Messages ---
@restricted
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.pop('state', None)
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    if state == 'add_user':
        if not username_regex.match(text):
            await context.bot.send_message(chat_id, "Некорректное имя.")
            return
        await context.bot.send_message(chat_id, "Создаю...")
        run_sync(f'--add-user {text}')
        await context.bot.send_message(chat_id, f"✅ Создан: {text}")
        # Показать конфиг
        confs = get_user_conf(text)
        for c in confs:
            if not c: continue
            qr = qrcode.make(c)
            bio = io.BytesIO(); qr.save(bio, 'PNG'); bio.seek(0)
            await context.bot.send_photo(chat_id, photo=bio, caption=f"<code>{c}</code>", parse_mode='HTML')

    elif state == 'setting':
        param = context.user_data.pop('param', None)
        # Валидация
        if param == 'port' and not text.isdigit():
            await context.bot.send_message(chat_id, "Порт должен быть числом.")
            return
        
        # Маппинг параметра для функции apply
        key = param
        if param == 'path': key = 'service_path'
        if param == 'domain': key = 'domain' # и так совпадает
        if param == 'host_header': key = 'host_header'
        
        await apply_setting(update, context, key, text)

# --- Main ---
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(check_startup_message).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    logger.info("Bot started.")
    app.run_polling()

if __name__ == '__main__':
    main()