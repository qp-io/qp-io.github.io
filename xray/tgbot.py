#!/usr/bin/env python3
import os
import re
import io
import subprocess
import logging
import zipfile
import shutil
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

# Папка данных (в Docker контейнере она примонтирована сюда скриптом установки)
DATA_DIR = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')

# Основная команда запуска (как в оригинале - через curl)
# Это позволяет боту выполнять функции скрипта без наличия самого файла скрипта внутри контейнера
BASE_COMMAND = 'bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh) '

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
    """
    Запускает команду, используя curl-обертку (как в оригинале).
    cmd_args: аргументы, например '--add-user test'
    """
    full_cmd = BASE_COMMAND + cmd_args
    try:
        logger.info(f"Executing: {full_cmd}")
        # Запускаем через bash -c
        process = subprocess.Popen(
            ['/bin/bash', '-c', full_cmd], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE
        )
        output, err = process.communicate(timeout=timeout)
        
        # Если есть ошибка выполнения, логируем
        if process.returncode != 0:
            err_decoded = err.decode().strip()
            logger.warning(f"Command exited {process.returncode}: {err_decoded}")
            # Возвращаем ошибку в текст, чтобы бот мог показать её
            return f"Error: {err_decoded}" if err_decoded else output.decode()
            
        return output.decode()
    except Exception as e:
        logger.exception(f"run_command failed: {e}")
        return str(e)

def read_config_file() -> Dict[str, str]:
    """
    Читает файл config напрямую с диска для отображения меню.
    Это безопасно, так как файл проброшен через volume.
    """
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
    """Получает список пользователей через скрипт."""
    out = run_command('--list-users')
    # Фильтруем вывод (убираем пустые строки и возможные логи)
    return [line.strip() for line in out.splitlines() if line.strip() and "Using config" not in line and "Error" not in line]

def get_user_config(username: str):
    """Получает конфиг пользователя."""
    # Используем grep, чтобы вычленить только ссылки и json
    cmd = f"--show-user {username} | grep -E '://|^\\{{\"dns\"'"
    out = run_command(cmd)
    return [line for line in out.splitlines() if line.strip()]

def create_backup_zip() -> str:
    """
    Создает архив с файлами config и users.
    Эти файлы должны быть доступны по пути /opt/reality-ezpz (mount volume).
    """
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
        if username and username in raw_admins:
            admin_ok = True
        if user_id and str(user_id) in raw_admins:
            admin_ok = True

        if admin_ok:
            return await func(update, context, *args, **kwargs)
        else:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text='⛔ Нет доступа.')
    return wrapped

# --- Обработчики (Handlers) ---

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
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👥 <b>Меню Пользователей</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def users_list_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    # mode: 'show' или 'delete'
    users = get_users_list()
    keyboard = []
    
    # Префикс коллбека
    cb = "show_user" if mode == 'show' else "del_user"
    
    if not users:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Нет пользователей.")
        return

    for u in users:
        keyboard.append([InlineKeyboardButton(u, callback_data=f'{cb}!{u}')])
    
    keyboard.append([InlineKeyboardButton('🔙 Назад', callback_data='menu_users')])
    
    text = "Выберите пользователя для просмотра:" if mode == 'show' else "Выберите, кого удалить:"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def show_user(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ Получаю конфиг для <b>{username}</b>...", parse_mode='HTML')
    
    configs = get_user_config(username)
    await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    
    if not configs:
        await context.bot.send_message(chat_id=chat_id, text="❌ Ошибка получения конфига (возможно, пользователь не существует).")
        return

    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 К списку', callback_data='u_list')]])

    for conf in configs:
        if not conf.strip(): continue
        
        # QR Code
        qr = qrcode.make(conf)
        bio = io.BytesIO()
        qr.save(bio, 'PNG')
        bio.seek(0)
        
        await context.bot.send_photo(
            chat_id=chat_id, 
            photo=bio, 
            caption=f"<code>{conf}</code>", 
            parse_mode='HTML',
            reply_markup=back_markup
        )

# --- Настройки ---
@restricted
async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Читаем файл config, который проброшен через Docker volume
    conf = read_config_file()
    
    info = (
        "⚙️ <b>Текущие настройки</b>\n\n"
        f"🔹 <b>Core:</b> {conf.get('core', '?')}\n"
        f"🔹 <b>Transport:</b> {conf.get('transport', '?')}\n"
        f"🔹 <b>Security:</b> {conf.get('security', '?')}\n"
        f"🔹 <b>Port:</b> {conf.get('port', '?')}\n"
        f"🔹 <b>SNI:</b> {conf.get('domain', '?')}\n"
        f"🔹 <b>Path:</b> /{conf.get('service_path', '?')}\n"
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
        # Делим на 2 колонки
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
            [InlineKeyboardButton('Включить (ON)', callback_data='run!enable-warp!true')],
            [InlineKeyboardButton('Выключить (OFF)', callback_data='run!enable-warp!false')]
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
        'domain': 'SNI Домен (например yahoo.com)',
        'path': 'Path (без слеша)',
        'host': 'Host Header'
    }
    label = labels.get(param, param)
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"⌨️ Введите {label}:", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='menu_settings')]])
    )

@restricted
async def execute_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str, value: str):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ Применяю: <code>--{param} {value}</code>...", parse_mode='HTML')
    
    # Формируем аргументы для curl-скрипта
    args = f"--{param} {value}"
    
    # Запускаем
    out = run_command(args, timeout=300) # Таймаут побольше, так как может быть регенерация
    
    if "Error" in out:
        text = f"❌ Ошибка:\n<pre>{out}</pre>"
    else:
        # Обрезаем вывод, чтобы не спамить
        text = f"✅ Успешно!\n\n<pre>{out[-300:]}</pre>"
        
    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=text, parse_mode='HTML')
    
    # Кнопка возврата
    await context.bot.send_message(chat_id=chat_id, text="...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 Меню', callback_data='menu_settings')]]))

# --- Действия (Бэкап, Рестарт) ---

@restricted
async def action_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Архив файлов config и users...")
    
    path = create_backup_zip()
    
    if path and os.path.exists(path):
        await context.bot.send_document(
            chat_id=chat_id, 
            document=open(path, 'rb'), 
            filename="reality_backup.zip",
            caption="✅ Бэкап готов."
        )
        os.remove(path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Не удалось создать бэкап. Проверьте, что бот запущен в Docker с volume mount.")

@restricted
async def action_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Выполняется рестарт служб...")
    
    out = run_command("--restart")
    
    if "Error" in out:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"❌ Ошибка рестарта:\n{out}")
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="✅ Службы перезагружены.")

# --- Обработка кнопок и ввода ---

@restricted
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # data format: command!arg1!arg2
    parts = data.split('!')
    cmd = parts[0]
    arg1 = parts[1] if len(parts) > 1 else None
    
    # Main Navigation
    if cmd == 'start': await start(update, context)
    elif cmd == 'menu_users': await menu_users(update, context)
    elif cmd == 'menu_settings': await menu_settings(update, context)
    
    # Users
    elif cmd == 'u_list': await users_list_action(update, context, 'show')
    elif cmd == 'u_del_menu': await users_list_action(update, context, 'delete')
    elif cmd == 'u_add':
        context.user_data['input_action'] = 'add_user'
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите имя нового пользователя:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='menu_users')]]))
        
    elif cmd == 'show_user': await show_user(update, context, arg1)
    elif cmd == 'del_user':
        # Confirmation
        username = arg1
        kb = [[InlineKeyboardButton('🗑 Да, удалить', callback_data=f'confirm_del!{username}'), InlineKeyboardButton('Нет', callback_data='menu_users')]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Точно удалить <b>{username}</b>?", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(kb))
        
    elif cmd == 'confirm_del':
        username = arg1
        run_command(f'--delete-user {username}')
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Пользователь {username} удален.")
        await menu_users(update, context)

    # Settings
    elif cmd == 'set_sub': await settings_submenu(update, context, arg1)
    elif cmd == 'ask': await ask_value(update, context, arg1)
    elif cmd == 'run':
        # run!param!value
        param = parts[1]
        val = parts[2]
        await execute_setting(update, context, param, val)
        
    # Actions
    elif cmd == 'act_backup': await action_backup(update, context)
    elif cmd == 'act_restart': await action_restart(update, context)

@restricted
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.pop('input_action', None)
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if action == 'add_user':
        if not username_regex.match(text):
            await context.bot.send_message(chat_id=chat_id, text="❌ Некорректное имя (только a-Z0-9).")
            return
        
        msg = await context.bot.send_message(chat_id=chat_id, text="Создание пользователя...")
        out = run_command(f'--add-user {text}')
        
        if "Error" in out:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"❌ Ошибка:\n{out}")
        else:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            await context.bot.send_message(chat_id=chat_id, text=f"✅ Пользователь {text} создан.")
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