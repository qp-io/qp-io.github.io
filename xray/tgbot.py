#!/usr/bin/env python3
import os
import re
import io
import subprocess
import logging
import zipfile
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
)

# --- Настройки путей и команд ---

DATA_DIR = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')

# Основная команда запуска.
# 1. systemctl заглушен (чтобы не спамил ошибками).
# 2. Скрипт скачивается и сразу патчится: заменяем ' -it ' на ' -i ', чтобы Docker не требовал TTY.
BASE_COMMAND = 'function systemctl() { :; }; export -f systemctl; bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh | sed "s/ -it / -i /g") '

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен и Админ
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    logger.error("BOT_TOKEN env is not set")
    raise SystemExit("BOT_TOKEN env is not set")

ADMIN = os.environ.get('BOT_ADMIN', '')
username_regex = re.compile(r"^[a-zA-Z0-9]+$")

# --- Хелперы ---

def run_command(cmd_args: str, timeout: int = 300) -> str:
    """Запускает команду скрипта в bash."""
    full_cmd = BASE_COMMAND + cmd_args
    try:
        logger.info(f"Executing args: {cmd_args}")
        # Используем executable='/bin/bash', так как синтаксис <(...) работает только в bash
        process = subprocess.Popen(
            full_cmd, 
            shell=True,
            executable='/bin/bash',
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE
        )
        output, err = process.communicate(timeout=timeout)
        
        if process.returncode != 0:
            err_decoded = err.decode().strip()
            # Игнорируем ошибки systemctl (код 127 или текст), если они все же пролезут
            if "systemctl" in err_decoded and (process.returncode == 127 or "command not found" in err_decoded):
                pass 
            else:
                logger.warning(f"Command exited {process.returncode}: {err_decoded}")
                return f"Error: {err_decoded}" if err_decoded else output.decode()
            
        return output.decode()
    except Exception as e:
        logger.exception(f"run_command failed: {e}")
        return str(e)

def modify_config_directly(key: str, value: str):
    """Прямая правка конфига через sed (для очистки полей)."""
    if not os.path.exists(CONFIG_FILE):
        return
    safe_val = value.replace('/', '\\/')
    cmd = f"sed -i 's/^{key}=.*/{key}={safe_val}/' {CONFIG_FILE}"
    subprocess.run(cmd, shell=True)

def read_config_file() -> Dict[str, str]:
    config = {}
    if not os.path.exists(CONFIG_FILE):
        return config
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip().strip('"').strip("'")
    except Exception as e:
        logger.error(f"Error reading config file: {e}")
    return config

def get_users_list():
    out = run_command('--list-users')
    return [line.strip() for line in out.splitlines() if line.strip() and "Using config" not in line and "Error" not in line]

def get_user_config(username: str):
    cmd = f"--show-user {username} | grep -E '://|^\\{{\"dns\"'"
    out = run_command(cmd)
    return [line for line in out.splitlines() if line.strip()]

def create_backup_zip() -> str:
    if not os.path.exists(DATA_DIR):
        return ""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_filename = f"/tmp/reality_backup_{timestamp}.zip"
    files_to_backup = ['config', 'users']
    files_found = False
    try:
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in files_to_backup:
                file_path = os.path.join(DATA_DIR, filename)
                if os.path.exists(file_path):
                    zipf.write(file_path, arcname=filename)
                    files_found = True
        if not files_found:
            if os.path.exists(backup_filename): os.remove(backup_filename)
            return ""
        return backup_filename
    except Exception as e:
        logger.error(f"Backup creation failed: {e}")
        return ""

# --- Декоратор доступа ---
def restricted(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        username: Optional[str] = None
        user_id: Optional[int] = None
        if update.effective_user:
            username = update.effective_user.username
            user_id = update.effective_user.id
        raw_admins = [a.strip() for a in ADMIN.split(',') if a.strip()]
        admin_ok = False
        if username and username in raw_admins: admin_ok = True
        if user_id and str(user_id) in raw_admins: admin_ok = True

        if admin_ok:
            return await func(update, context, *args, **kwargs)
        else:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id: await context.bot.send_message(chat_id=chat_id, text='⛔ Нет доступа.')
    return wrapped

# --- Обработчики ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton('👥 Пользователи', callback_data='menu_users')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='menu_settings')],
        [InlineKeyboardButton('🔄 Перезапуск служб', callback_data='act_restart')],
        [InlineKeyboardButton('📥 Скачать Бэкап', callback_data='act_backup')],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="🤖 <b>Reality-EZPZ Panel</b>\nУправление сервером.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# --- Пользователи ---
@restricted
async def menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton('📜 Список / QR', callback_data='u_list')],
        [InlineKeyboardButton('➕ Добавить', callback_data='u_add')],
        [InlineKeyboardButton('➖ Удалить', callback_data='u_del_menu')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')],
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="👥 <b>Меню Пользователей</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

@restricted
async def users_list_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    users = get_users_list()
    keyboard = []
    cb = "show_user" if mode == 'show' else "del_user"
    if not users:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Нет пользователей.")
        return
    for u in users:
        keyboard.append([InlineKeyboardButton(u, callback_data=f'{cb}!{u}')])
    keyboard.append([InlineKeyboardButton('🔙 Назад', callback_data='menu_users')])
    text = "Выберите пользователя:" if mode == 'show' else "Кого удалить:"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def show_user(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ Получаю конфиг для <b>{username}</b>...", parse_mode='HTML')
    configs = get_user_config(username)
    await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    if not configs:
        await context.bot.send_message(chat_id=chat_id, text="❌ Ошибка получения конфига.")
        return
    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 К списку', callback_data='u_list')]])
    for conf in configs:
        if not conf.strip(): continue
        qr = qrcode.make(conf)
        bio = io.BytesIO()
        qr.save(bio, 'PNG')
        bio.seek(0)
        await context.bot.send_photo(chat_id=chat_id, photo=bio, caption=f"<code>{conf}</code>", parse_mode='HTML', reply_markup=back_markup)

# --- Настройки ---
@restricted
async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conf = read_config_file()
    info = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🔹 <b>Core:</b> {conf.get('core', '?')}\n"
        f"🔹 <b>Transport:</b> {conf.get('transport', '?')}\n"
        f"🔹 <b>Security:</b> {conf.get('security', '?')}\n"
        f"🔹 <b>Port:</b> {conf.get('port', '?')}\n"
        f"🔹 <b>SNI:</b> {conf.get('domain', '?')}\n"
        f"🔹 <b>Path:</b> /{conf.get('service_path', '')}\n"
        f"🔹 <b>Host:</b> {conf.get('host_header', '-')}\n"
        f"🔹 <b>Warp:</b> {conf.get('warp', 'OFF')}\n"
    )
    keyboard = [
        [InlineKeyboardButton('Core (Ядро)', callback_data='set_sub!core'), InlineKeyboardButton('Transport', callback_data='set_sub!transport')],
        [InlineKeyboardButton('Security', callback_data='set_sub!security'), InlineKeyboardButton('Warp', callback_data='set_sub!warp')],
        [InlineKeyboardButton('Server IP', callback_data='ask!server'), InlineKeyboardButton('Port', callback_data='ask!port')],
        [InlineKeyboardButton('SNI Domain', callback_data='ask!domain'), InlineKeyboardButton('Path', callback_data='ask!path')],
        [InlineKeyboardButton('Host Header', callback_data='ask!host')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text=info, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

@restricted
async def settings_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    keyboard = []
    text = ""
    if category == 'core':
        text = "Выберите ядро (Core):"
        keyboard = [[InlineKeyboardButton('Xray', callback_data='run!core!xray')], [InlineKeyboardButton('Sing-Box', callback_data='run!core!sing-box')]]
    elif category == 'transport':
        text = "Выберите транспорт:"
        opts = ['tcp', 'http', 'grpc', 'ws', 'xhttp', 'tuic', 'hysteria2', 'shadowtls']
        row = []
        for opt in opts:
            row.append(InlineKeyboardButton(opt, callback_data=f'run!transport!{opt}'))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
    elif category == 'security':
        text = "Выберите безопасность:"
        keyboard = [
            [InlineKeyboardButton('Reality', callback_data='run!security!reality')],
            [InlineKeyboardButton('LetsEncrypt', callback_data='run!security!letsencrypt')],
            [InlineKeyboardButton('SelfSigned', callback_data='run!security!selfsigned')],
            [InlineKeyboardButton('NoTLS', callback_data='run!security!notls')]
        ]
    elif category == 'warp':
        text = "Управление WARP:"
        keyboard = [
            [InlineKeyboardButton('✅ Включить', callback_data='ask!warp_license')],
            [InlineKeyboardButton('❌ Выключить', callback_data='run!enable-warp!false')]
        ]

    keyboard.append([InlineKeyboardButton('🔙 Отмена', callback_data='menu_settings')])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str):
    chat_id = update.effective_chat.id
    context.user_data['input_action'] = 'setting'
    context.user_data['setting_param'] = param
    
    labels = {
        'port': 'новый Порт (число)',
        'server': 'IP адрес или Домен сервера',
        'domain': 'SNI Домен',
        'path': 'Path (без слеша). Отправьте / чтобы очистить.',
        'host': 'Host Header',
        'warp_license': 'Ключ лицензии WARP+'
    }
    label = labels.get(param, param)
    
    extra_buttons = []
    if param == 'path':
        extra_buttons.append(InlineKeyboardButton('🗑 Очистить (сделать пустым)', callback_data='run!path!EMPTY'))
        
    buttons = [extra_buttons] if extra_buttons else []
    buttons.append([InlineKeyboardButton('❌ Отмена', callback_data='menu_settings')])
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"⌨️ Введите {label}:", 
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@restricted
async def execute_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, value: str):
    chat_id = update.effective_chat.id
    
    # 1. WARP
    if param == 'warp_license':
        # Включаем и добавляем лицензию одновременно
        args = f"--enable-warp true --warp-license {value}"
        msg_text = f"⏳ Включаю WARP с лицензией..."
        
    # 2. Очистка Path
    elif param == 'path' and (value == '/' or value == 'EMPTY' or value == ''):
        modify_config_directly('service_path', '')
        args = "--restart"
        msg_text = "⏳ Очищаю Path и перезапускаю..."
        value = "(пусто)"
        
    # 3. Стандарт
    else:
        script_flag = param.replace('_', '-')
        cmd_val = value if value else "''"
        args = f"--{script_flag} {cmd_val}"
        msg_text = f"⏳ Применяю: <code>--{script_flag} {cmd_val}</code>..."

    msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode='HTML')
    
    out = run_command(args, timeout=300)

    if "Error" in out and "systemctl" not in out:
        text = f"❌ Ошибка:\n<pre>{out}</pre>"
    else:
        text = f"✅ Успешно! ({param}={value})\n\n<pre>{out[-250:]}</pre>"
        
    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=text, parse_mode='HTML')
    await context.bot.send_message(chat_id=chat_id, text="...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 Меню', callback_data='menu_settings')]]))

# --- Действия ---
@restricted
async def action_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Создаю архив...")
    path = create_backup_zip()
    if path and os.path.exists(path):
        await context.bot.send_document(chat_id=chat_id, document=open(path, 'rb'), filename="reality_backup.zip", caption="✅ Бэкап готов.")
        os.remove(path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Ошибка бэкапа.")

@restricted
async def action_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Перезапуск служб...")
    out = run_command("--restart")
    if "Error" in out:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"❌ Ошибка:\n{out}")
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="✅ Службы перезагружены.")

# --- Callback & Message ---
@restricted
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split('!')
    cmd = parts[0]
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""

    if cmd == 'start': await start(update, context)
    elif cmd == 'menu_users': await menu_users(update, context)
    elif cmd == 'menu_settings': await menu_settings(update, context)
    elif cmd == 'u_list': await users_list_action(update, context, 'show')
    elif cmd == 'u_del_menu': await users_list_action(update, context, 'delete')
    elif cmd == 'u_add':
        context.user_data['input_action'] = 'add_user'
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите имя нового пользователя:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='menu_users')]]))
    elif cmd == 'show_user': await show_user(update, context, arg1)
    elif cmd == 'del_user':
        kb = [[InlineKeyboardButton('🗑 Удалить', callback_data=f'confirm_del!{arg1}'), InlineKeyboardButton('Нет', callback_data='menu_users')]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Удалить <b>{arg1}</b>?", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(kb))
    elif cmd == 'confirm_del':
        run_command(f'--delete-user {arg1}')
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Пользователь {arg1} удален.")
        await menu_users(update, context)
    elif cmd == 'set_sub': await settings_submenu(update, context, arg1)
    elif cmd == 'ask': await ask_value(update, context, arg1)
    elif cmd == 'run':
        val = arg2 if arg2 else ""
        await execute_setting(update, context, arg1, val)
    elif cmd == 'act_backup': await action_backup(update, context)
    elif cmd == 'act_restart': await action_restart(update, context)

@restricted
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.pop('input_action', None)
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if action == 'add_user':
        if not username_regex.match(text):
            await context.bot.send_message(chat_id=chat_id, text="❌ Некорректное имя.")
            return
        msg = await context.bot.send_message(chat_id=chat_id, text="Создание...")
        out = run_command(f'--add-user {text}')
        if "Error" in out:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"❌ Ошибка:\n{out}")
        else:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            await context.bot.send_message(chat_id=chat_id, text=f"✅ Создан {text}")
            await show_user(update, context, text)
    elif action == 'setting':
        param = context.user_data.pop('setting_param', None)
        if param == 'port' and not text.isdigit():
             await context.bot.send_message(chat_id=chat_id, text="❌ Порт должен быть числом.")
             return
        await execute_setting(update, context, param, text)

# Main
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("Bot started.")
    app.run_polling()

if __name__ == '__main__':
    main()