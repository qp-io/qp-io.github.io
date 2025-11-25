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

# --- Конфигурация Путей ---

# Папка, где установлен скрипт
SCRIPT_DIR = '/opt/reality-ezpz'
# Путь к исполняемому файлу скрипта
SCRIPT_EXEC = os.path.join(SCRIPT_DIR, 'reality-ezpz.sh')
# Файл с текущими переменными (только для чтения, чтобы показывать в меню)
SCRIPT_CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config')

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config from env
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    logger.error("BOT_TOKEN env is not set")
    raise SystemExit("BOT_TOKEN env is not set")

ADMIN = os.environ.get('BOT_ADMIN', '')
username_regex = re.compile(r"^[a-zA-Z0-9]+$")

# Команда запуска скрипта. 
# Используем sudo, если бот запущен не от root, но обычно в контейнере это root.
# Важно: Скрипт reality-ezpz должен быть исполняемым (chmod +x).
BASE_CMD = f"bash {SCRIPT_EXEC} "

# --- Хелперы ---

def run_shell(cmd: str, timeout: int = 300) -> str:
    """Запускает команду в оболочке и возвращает вывод."""
    try:
        logger.info(f"Executing: {cmd}")
        process = subprocess.Popen(['/bin/bash', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, err = process.communicate(timeout=timeout)
        if process.returncode != 0:
            err_msg = err.decode().strip()
            logger.warning(f"Command exited {process.returncode}: {err_msg}")
            return f"Error: {err_msg}"
        return output.decode()
    except Exception as e:
        logger.exception(f"run_shell failed: {e}")
        return str(e)

def read_current_config() -> Dict[str, str]:
    """Читает файл config для отображения текущих настроек в меню бота."""
    config = {}
    if not os.path.exists(SCRIPT_CONFIG_FILE):
        return config
    
    try:
        with open(SCRIPT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip().strip('"').strip("'")
    except Exception as e:
        logger.error(f"Error reading script config: {e}")
    return config

# --- Функции взаимодействия со скриптом ---

def get_users_list():
    out = run_shell(BASE_CMD + '--list-users')
    # Фильтруем вывод, оставляем только имена пользователей
    return [line.strip() for line in out.splitlines() if line.strip() and "Using config" not in line]

def get_user_config(username: str):
    # grep используется для вычленения ссылок и JSON конфигов из вывода скрипта
    cmd = BASE_CMD + f"--show-user {username} | grep -E '://|^\\{{\"dns\"'"
    out = run_shell(cmd)
    return [line for line in out.splitlines() if line.strip()]

def delete_user_cmd(username: str):
    run_shell(BASE_CMD + f'--delete-user {username}')

def add_user_cmd(username: str):
    run_shell(BASE_CMD + f'--add-user {username}')

def create_backup_zip() -> str:
    """Архивирует файлы config и users."""
    if not os.path.exists(SCRIPT_DIR):
        return ""

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_filename = f"/tmp/backup_{timestamp}.zip"
    
    files_to_backup = ['config', 'users']
    files_found = False

    try:
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in files_to_backup:
                file_path = os.path.join(SCRIPT_DIR, filename)
                if os.path.exists(file_path):
                    zipf.write(file_path, arcname=filename)
                    files_found = True
        
        if not files_found:
            if os.path.exists(backup_filename): os.remove(backup_filename)
            return ""
        return backup_filename
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return ""

def restart_services():
    """Вызывает скрипт с флагом рестарта."""
    return run_shell(BASE_CMD + "--restart")

# --- Декоратор прав доступа ---
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

# --- Handlers: Start & Main Menus ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton('👥 Пользователи', callback_data='menu_users')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='menu_settings')],
        [InlineKeyboardButton('🔄 Перезагрузить службы', callback_data='action_restart')],
        [InlineKeyboardButton('📥 Сделать Бэкап', callback_data='action_backup')],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="🤖 <b>Reality-EZPZ Panel</b>\nУправление сервером через скрипт.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# --- Handlers: Users ---

@restricted
async def menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton('📜 Список / QR-код', callback_data='users_list')],
        [InlineKeyboardButton('➕ Добавить', callback_data='users_add')],
        [InlineKeyboardButton('➖ Удалить', callback_data='users_del')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')],
    ]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👥 <b>Управление пользователями</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def users_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    """Показывает список пользователей для выбора (просмотр или удаление)."""
    users = get_users_list()
    if not users:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Список пользователей пуст.")
        return

    # action = 'show' or 'del'
    callback_prefix = "u_show" if action == 'show' else "u_del"
    
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(user, callback_data=f'{callback_prefix}!{user}')])
    
    keyboard.append([InlineKeyboardButton('🔙 Назад', callback_data='menu_users')])
    
    text = "Выберите пользователя:" if action == 'show' else "Выберите пользователя для удаления:"
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=text, 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def show_user_config(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ Загрузка конфига для <b>{username}</b>...", parse_mode='HTML')
    
    configs = get_user_config(username)
    back_btn = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 К списку', callback_data='users_list')]])
    
    await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)

    if not configs:
        await context.bot.send_message(chat_id=chat_id, text="❌ Конфиг не найден или ошибка генерации.", reply_markup=back_btn)
        return

    for conf in configs:
        if not conf.strip(): continue
        
        # Генерируем QR
        qr = qrcode.make(conf)
        bio = io.BytesIO()
        qr.save(bio, 'PNG')
        bio.seek(0)
        
        # Если конфиг длинный, обрезаем для подписи, но лучше отправлять как моноширинный текст
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=bio,
            caption=f"<code>{conf}</code>",
            parse_mode='HTML'
        )
    
    await context.bot.send_message(chat_id=chat_id, text="Готово.", reply_markup=back_btn)

# --- Handlers: Settings ---

@restricted
async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню настроек, отображает текущие значения."""
    conf = read_current_config()
    
    # Формируем текст с текущими значениями
    info_text = (
        "⚙️ <b>Настройки Сервера</b>\n\n"
        f"🔹 <b>Core:</b> {conf.get('core', '?')}\n"
        f"🔹 <b>Server:</b> {conf.get('server', '?')}\n"
        f"🔹 <b>Port:</b> {conf.get('port', '?')}\n"
        f"🔹 <b>Transport:</b> {conf.get('transport', '?')}\n"
        f"🔹 <b>Security:</b> {conf.get('security', '?')}\n"
        f"🔹 <b>SNI (Domain):</b> {conf.get('domain', '?')}\n"
        f"🔹 <b>Path:</b> /{conf.get('service_path', '?')}\n"
        f"🔹 <b>Host:</b> {conf.get('host_header', 'Не задан')}\n"
        f"🔹 <b>Warp:</b> {conf.get('warp', 'OFF')}\n"
    )

    # Кнопки для изменения
    keyboard = [
        [InlineKeyboardButton('Core (Ядро)', callback_data='set_menu_core'), InlineKeyboardButton('Transport', callback_data='set_menu_transport')],
        [InlineKeyboardButton('Security', callback_data='set_menu_security'), InlineKeyboardButton('Warp', callback_data='set_menu_warp')],
        [InlineKeyboardButton('Server IP', callback_data='ask_set!server'), InlineKeyboardButton('Port', callback_data='ask_set!port')],
        [InlineKeyboardButton('SNI Domain', callback_data='ask_set!domain'), InlineKeyboardButton('Path', callback_data='ask_set!path')],
        [InlineKeyboardButton('Host Header', callback_data='ask_set!host')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')]
    ]

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=info_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def submenu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, setting_type: str):
    """Меню выбора для Core, Transport, Security, Warp."""
    keyboard = []
    
    if setting_type == 'core':
        # --core <xray|sing-box>
        keyboard = [
            [InlineKeyboardButton('Xray', callback_data='run_set!core!xray')],
            [InlineKeyboardButton('Sing-Box', callback_data='run_set!core!sing-box')]
        ]
        text = "Выберите ядро (Core):"
        
    elif setting_type == 'transport':
        # --transport <tcp|http|xhttp|grpc|ws|tuic|hysteria2|shadowtls>
        # Разобьем на строки для удобства
        row1 = [InlineKeyboardButton(t, callback_data=f'run_set!transport!{t}') for t in ['tcp', 'http', 'grpc', 'ws']]
        row2 = [InlineKeyboardButton(t, callback_data=f'run_set!transport!{t}') for t in ['xhttp', 'tuic', 'hysteria2', 'shadowtls']]
        keyboard = [row1, row2]
        text = "Выберите транспорт:"

    elif setting_type == 'security':
        # --security <reality|letsencrypt|selfsigned|notls>
        opts = ['reality', 'letsencrypt', 'selfsigned', 'notls']
        keyboard = [[InlineKeyboardButton(o, callback_data=f'run_set!security!{o}')] for o in opts]
        text = "Выберите тип безопасности (Security):"

    elif setting_type == 'warp':
        # --enable-warp <true|false>
        keyboard = [
            [InlineKeyboardButton('Включить (ON)', callback_data='run_set!enable-warp!true')],
            [InlineKeyboardButton('Выключить (OFF)', callback_data='run_set!enable-warp!false')]
        ]
        text = "Управление Cloudflare WARP:"
    
    keyboard.append([InlineKeyboardButton('🔙 Отмена', callback_data='menu_settings')])
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def ask_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE, flag: str):
    """Запрос текстового значения (порта, домена и т.д.)."""
    chat_id = update.effective_chat.id
    # Сохраняем ожидаемый ввод
    context.user_data['input_mode'] = 'setting'
    context.user_data['setting_flag'] = flag # например 'port' или 'domain'
    
    text_map = {
        'port': 'Введите новый Порт (1-65535):',
        'server': 'Введите IP адрес или домен сервера:',
        'domain': 'Введите SNI домен (например, yahoo.com):',
        'path': 'Введите путь (Path), без начального слеша:',
        'host': 'Введите Host Header:'
    }
    
    msg_text = text_map.get(flag, f"Введите значение для {flag}:")
    
    keyboard = [[InlineKeyboardButton('❌ Отмена', callback_data='menu_settings')]]
    await context.bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def run_script_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, flag: str, value: str):
    """Запускает скрипт с нужным флагом."""
    chat_id = update.effective_chat.id
    
    # Маппинг внутренних ключей на флаги скрипта
    # flag приходит либо из callback (ask_set), либо из run_set
    
    flag_map = {
        'core': '--core',
        'transport': '--transport',
        'security': '--security',
        'enable-warp': '--enable-warp',
        'server': '--server',
        'port': '--port',
        'domain': '--domain',
        'path': '--path',
        'host': '--host'
    }
    
    script_flag = flag_map.get(flag, f"--{flag}")
    
    # Формируем команду
    cmd = f"{BASE_CMD} {script_flag} {value}"
    
    msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=f"⏳ Применяю настройку: <code>{script_flag} {value}</code>...\nЭто может занять время (регенерация ключей/рестарт).",
        parse_mode='HTML'
    )
    
    # Запускаем скрипт
    output = run_shell(cmd, timeout=300)
    
    # Проверка на успех (скрипт reality-ezpz обычно не пишет "Error" в stdout при успехе, но пишет инструкции)
    if "Error" in output or "Неверный" in output or "Ошибка" in output:
        res_text = f"❌ <b>Ошибка:</b>\n<pre>{output}</pre>"
    else:
        res_text = f"✅ Настройка применена успешно!\n\n<pre>{output[-200:]}</pre>" # Показываем последние 200 символов лога
        
    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=res_text, parse_mode='HTML')
    
    # Возвращаемся в меню настроек через небольшую паузу или даем кнопку
    await context.bot.send_message(
        chat_id=chat_id, 
        text="Вернуться в меню:", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 Меню настроек', callback_data='menu_settings')]])
    )

# --- Handlers: System Actions ---

@restricted
async def action_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Создание бэкапа (config + users)...")
    
    path = create_backup_zip()
    if path and os.path.exists(path):
        await context.bot.send_document(
            chat_id=chat_id, 
            document=open(path, 'rb'), 
            filename="reality_ezpz_backup.zip",
            caption="✅ Бэкап готов."
        )
        os.remove(path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Не удалось создать бэкап. Проверьте путь /opt/reality-ezpz.")

@restricted
async def action_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Перезагрузка служб (docker compose)...")
    
    output = restart_services()
    
    if "Error" not in output:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="✅ Службы успешно перезагружены.")
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"❌ Ошибка:\n{output}")

# --- Input Handling & Dispatcher ---

@restricted
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # Разбор данных: command!arg1!arg2
    parts = data.split('!')
    cmd = parts[0]
    args = parts[1:]

    # Навигация
    if cmd == 'start': await start(update, context)
    elif cmd == 'menu_users': await menu_users(update, context)
    elif cmd == 'menu_settings': await menu_settings(update, context)
    
    # Пользователи
    elif cmd == 'users_list': await users_list_handler(update, context, 'show')
    elif cmd == 'users_del': await users_list_handler(update, context, 'del')
    elif cmd == 'users_add':
        context.user_data['input_mode'] = 'add_user'
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите имя нового пользователя:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='menu_users')]]))
    
    elif cmd == 'u_show': await show_user_config(update, context, args[0])
    elif cmd == 'u_del':
        # Подтверждение
        username = args[0]
        kb = [[InlineKeyboardButton('🗑 Да, удалить', callback_data=f'confirm_del!{username}'), InlineKeyboardButton('Отмена', callback_data='menu_users')]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Удалить пользователя <b>{username}</b>?", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(kb))
    
    elif cmd == 'confirm_del':
        username = args[0]
        delete_user_cmd(username)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Пользователь {username} удален.")
        await menu_users(update, context)

    # Настройки
    elif cmd == 'set_menu_core': await submenu_choice(update, context, 'core')
    elif cmd == 'set_menu_transport': await submenu_choice(update, context, 'transport')
    elif cmd == 'set_menu_security': await submenu_choice(update, context, 'security')
    elif cmd == 'set_menu_warp': await submenu_choice(update, context, 'warp')
    
    elif cmd == 'ask_set': 
        # ask_set!port
        await ask_setting_value(update, context, args[0])
    
    elif cmd == 'run_set':
        # run_set!core!xray
        flag, value = args[0], args[1]
        await run_script_setting(update, context, flag, value)
    
    # Действия
    elif cmd == 'action_backup': await action_backup(update, context)
    elif cmd == 'action_restart': await action_restart(update, context)

@restricted
async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.pop('input_mode', None)
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if mode == 'add_user':
        if not username_regex.match(text):
            await context.bot.send_message(chat_id=chat_id, text="❌ Некорректное имя (только латиница и цифры). Попробуйте снова.")
            return
        add_user_cmd(text)
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Пользователь {text} создан.")
        await show_user_config(update, context, text)
    
    elif mode == 'setting':
        flag = context.user_data.pop('setting_flag', None)
        if flag:
            # Валидация порта
            if flag == 'port' and (not text.isdigit() or not (1 <= int(text) <= 65535)):
                await context.bot.send_message(chat_id=chat_id, text="❌ Порт должен быть числом от 1 до 65535.")
                return
            
            await run_script_setting(update, context, flag, text)

# Main
def main():
    if not os.path.exists(SCRIPT_EXEC):
        logger.error(f"SCRIPT NOT FOUND AT {SCRIPT_EXEC}. PLEASE CHECK PATH.")
        
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    logger.info("Bot started...")
    app.run_polling()

if __name__ == '__main__':
    main()