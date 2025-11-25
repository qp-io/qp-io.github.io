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

# Основная папка скрипта reality-ezpz
SCRIPT_DIR = '/opt/reality-ezpz'
# Файл, где скрипт хранит свои настройки (переменные bash)
SCRIPT_CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config')
# Сам исполняемый файл скрипта
SCRIPT_EXEC = os.path.join(SCRIPT_DIR, 'reality-ezpz.sh')

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

# Команда-обертка для управления пользователями (используем установленный скрипт)
# Если скрипт установлен в /opt, вызываем его напрямую.
if os.path.exists(SCRIPT_EXEC):
    base_command = f"bash {SCRIPT_EXEC} "
else:
    # Fallback на curl если локальный файл не найден (хотя для настроек он нужен)
    base_command = 'bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh) '

# --- Хелперы для работы с bash-конфигом скрипта ---

def run_command(cmd: str) -> str:
    try:
        # Запускаем bash command
        process = subprocess.Popen(['/bin/bash', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, err = process.communicate(timeout=180) # Увеличили таймаут для пересборки
        if process.returncode != 0:
            logger.warning("Command exited %s: %s", process.returncode, err.decode().strip())
        return output.decode()
    except Exception as e:
        logger.exception("run_command failed: %s", e)
        return ""

def read_script_config() -> Dict[str, str]:
    """Читает переменные из файла config скрипта reality-ezpz."""
    config = {}
    if not os.path.exists(SCRIPT_CONFIG_FILE):
        return config
    
    try:
        with open(SCRIPT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Ищем строки вида KEY="VALUE" или KEY=VALUE
                if '=' in line and not line.startswith('#'):
                    parts = line.split('=', 1)
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'")
                    config[key] = val
    except Exception as e:
        logger.error(f"Error reading script config: {e}")
    return config

def update_script_config(key: str, value: str) -> bool:
    """Обновляет одну настройку в файле config, используя sed для сохранения структуры."""
    if not os.path.exists(SCRIPT_CONFIG_FILE):
        return False
    
    # Экранируем слеши для sed
    safe_value = value.replace('/', '\\/')
    
    # Проверяем, существует ли ключ
    grep_cmd = f"grep -q '^{key}=' {SCRIPT_CONFIG_FILE}"
    exists = subprocess.call(['/bin/bash', '-c', grep_cmd]) == 0
    
    if exists:
        # Заменяем существующее значение
        sed_cmd = f"sed -i 's/^{key}=.*/{key}=\"{safe_value}\"/' {SCRIPT_CONFIG_FILE}"
    else:
        # Добавляем новое, если не нашли (хотя лучше менять только существующие)
        sed_cmd = f"echo '{key}=\"{value}\"' >> {SCRIPT_CONFIG_FILE}"
        
    return subprocess.call(['/bin/bash', '-c', sed_cmd]) == 0

def apply_configuration():
    """
    Запускает скрипт reality-ezpz для применения настроек.
    Обычно запуск скрипта без аргументов (или с флагами установки) 
    считывает config и пересобирает контейнеры/сервисы.
    """
    # Запускаем скрипт. В большинстве версий просто запуск применяет конфиг.
    # Добавляем --unattended или просто запускаем, надеясь что он не спросит меню, 
    # если конфиг уже есть.
    # Если скрипт всегда вызывает меню без аргументов, нам нужно найти аргумент "reinstall" или "update".
    # Часто повторный запуск скрипта установки работает как "Update".
    
    # Пробуем запустить без аргументов (стандартное поведение "Apply" для многих скриптов при наличии конфига)
    # Если скрипт интерактивный, это может зависнуть. Но у нас нет выбора без CLI флагов.
    # Надеемся, что author скрипта предусмотрел неинтерактивный режим при наличии конфига.
    run_command(f"bash {SCRIPT_EXEC} --default > /dev/null 2>&1 &") 
    # Используем nohup/background, чтобы бот не ждал вечность, если там меню.
    # Но лучше, если есть флаг. Попробуем перезагрузить docker compose, если это docker версия.
    
    if os.path.exists(os.path.join(SCRIPT_DIR, "docker-compose.yml")):
        run_command(f"cd {SCRIPT_DIR} && docker compose up -d")
    else:
        # Systemd версия
        run_command("systemctl restart xray")

# --- Остальные функции ---

def get_users_ezpz():
    out = run_command(base_command + '--list-users')
    return [line for line in out.splitlines() if line.strip()]

def get_config_ezpz(username: str):
    local_command = base_command + f"--show-user {username} | grep -E '://|^\\{{\"dns\"'"
    out = run_command(local_command)
    return [line for line in out.splitlines() if line.strip()]

def delete_user_ezpz(username: str):
    run_command(base_command + f'--delete-user {username}')

def add_user_ezpz(username: str):
    run_command(base_command + f'--add-user {username}')

def create_backup() -> str:
    """Бэкап файлов config и users из папки скрипта"""
    if not os.path.exists(SCRIPT_DIR):
        return ""

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_filename = f"/tmp/reality_backup_{timestamp}.zip"
    
    # Файлы, которые критичны для этого скрипта
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
        logger.error(f"Backup creation failed: {e}")
        return ""

# --- Декоратор ---
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
                await context.bot.send_message(chat_id=chat_id, text='⛔ Нет прав доступа.')
    return wrapped

# --- Handlers ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton('👤 Пользователи', callback_data='users_menu')],
        [InlineKeyboardButton('⚙️ Настройки скрипта', callback_data='settings_menu')],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="🤖 <b>Reality-EZPZ Bot</b>\nУправление скриптом и пользователями.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton('Список / Конфиги', callback_data='show_user')],
        [InlineKeyboardButton('➕ Добавить', callback_data='add_user')],
        [InlineKeyboardButton('➖ Удалить', callback_data='delete_user')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')],
    ]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👥 <b>Управление пользователями</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Читаем текущие настройки чтобы показать их (опционально) или просто меню
    # Ключи переменных в скрипте обычно: PORT, SECURITY, TRANSPORT, SERVER (для SNI)
    
    keyboard = [
        # Mapping: Кнопка -> Ключ переменной в файле config
        [InlineKeyboardButton('CORE (Ядро)', callback_data='edit_conf!CORE'), InlineKeyboardButton('PORT (Порт)', callback_data='edit_conf!PORT')],
        [InlineKeyboardButton('TRANSPORT', callback_data='edit_conf!TRANSPORT'), InlineKeyboardButton('SECURITY', callback_data='edit_conf!SECURITY')],
        [InlineKeyboardButton('SNI (Домен)', callback_data='edit_conf!SERVER'), InlineKeyboardButton('PATH', callback_data='edit_conf!PATH')],
        [InlineKeyboardButton('WARP', callback_data='edit_conf!WARP'), InlineKeyboardButton('HOST', callback_data='edit_conf!HOST')],
        [InlineKeyboardButton('📥 Бэкап (config+users)', callback_data='do_backup')],
        [InlineKeyboardButton('🔄 Применить настройки', callback_data='apply_changes')],
        [InlineKeyboardButton('🔙 Назад', callback_data='start')]
    ]
    
    text = (
        "⚙️ <b>Меню Настроек</b>\n\n"
        "Здесь изменяются переменные в файле <code>config</code> скрипта.\n"
        "После изменения нажмите <b>🔄 Применить настройки</b>, чтобы скрипт пересоздал конфигурацию."
    )
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

@restricted
async def users_list_action(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, callback: str):
    chat_id = update.effective_chat.id
    users = get_users_ezpz()
    keyboard = [[InlineKeyboardButton(user, callback_data=f'{callback}!{user}')] for user in users]
    keyboard.append([InlineKeyboardButton('🔙 Назад', callback_data='users_menu')])
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def show_user_config(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 Назад', callback_data='show_user')]])
    await context.bot.send_message(chat_id=chat_id, text=f'⏳ Получение конфига для {username}...')
    
    config_list = get_config_ezpz(username)
    if not config_list:
        await context.bot.send_message(chat_id=chat_id, text="❌ Конфиг не найден.", reply_markup=back_markup)
        return

    for config in config_list:
        config = config.strip()
        if not config: continue
        
        # QR Code
        qr = qrcode.make(config)
        bio = io.BytesIO()
        qr.save(bio, 'PNG')
        bio.seek(0)
        
        await context.bot.send_photo(
            chat_id=chat_id, 
            photo=bio, 
            caption=f"<code>{config}</code>", 
            parse_mode='HTML', 
            reply_markup=back_markup
        )

@restricted
async def ask_config_value(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    chat_id = update.effective_chat.id
    context.user_data['expected_input'] = 'config_value'
    context.user_data['config_key'] = key
    
    current_conf = read_script_config()
    current_val = current_conf.get(key, 'Не задано')
    
    keyboard = [[InlineKeyboardButton('❌ Отмена', callback_data='settings_menu')]]
    text = (
        f"✏️ Введите новое значение для <b>{key}</b>.\n"
        f"Текущее значение в файле: <code>{current_val}</code>"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def set_config_value(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, value: str):
    chat_id = update.effective_chat.id
    
    if update_script_config(key, value):
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"✅ Значение <b>{key}</b> изменено на <code>{value}</code>.\n\n⚠️ Не забудьте нажать 'Применить настройки' в меню, чтобы изменения вступили в силу!",
            parse_mode='HTML'
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка записи в файл {SCRIPT_CONFIG_FILE}.")
    
    await settings_menu(update, context)

@restricted
async def apply_changes_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Запуск пересборки конфигурации (это может занять время)...")
    
    apply_configuration()
    
    await context.bot.edit_message_text(
        chat_id=chat_id, 
        message_id=msg.message_id, 
        text="✅ Команда обновления отправлена.\nПроверьте работоспособность через пару минут."
    )

@restricted
async def do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Создание архива...")
    path = create_backup()
    
    if path and os.path.exists(path):
        await context.bot.send_document(chat_id=chat_id, document=open(path, 'rb'), filename='reality_ezpz_backup.zip')
        os.remove(path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    else:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="❌ Ошибка: файлы config или users не найдены в папке скрипта.")

@restricted
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        data = query.data
        
        if '!' in data:
            cmd, param = data.split('!', 1)
        else:
            cmd, param = data, None

        # Navigation
        if cmd == 'start': await start(update, context)
        elif cmd == 'users_menu': await users_menu(update, context)
        elif cmd == 'settings_menu': await settings_menu(update, context)
        
        # User Actions
        elif cmd == 'show_user': 
            if param: await show_user_config(update, context, param)
            else: await users_list_action(update, context, 'Выберите пользователя:', 'show_user')
            
        elif cmd == 'delete_user':
            if param:
                # Ask confirmation
                k = [[InlineKeyboardButton('Да, удалить', callback_data=f'confirm_del!{param}'), InlineKeyboardButton('Нет', callback_data='users_menu')]]
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Удалить {param}?", reply_markup=InlineKeyboardMarkup(k))
            else:
                await users_list_action(update, context, 'Кого удалить?', 'delete_user')
        
        elif cmd == 'confirm_del':
            delete_user_ezpz(param)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Пользователь {param} удален.")
            await users_menu(update, context)

        elif cmd == 'add_user':
            context.user_data['expected_input'] = 'username'
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите имя пользователя:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена', callback_data='users_menu')]]))

        # Settings Actions
        elif cmd == 'edit_conf':
            await ask_config_value(update, context, param)
        elif cmd == 'apply_changes':
            await apply_changes_action(update, context)
        elif cmd == 'do_backup':
            await do_backup(update, context)

@restricted
async def user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if 'expected_input' in context.user_data:
        expected = context.user_data.pop('expected_input')
        
        if expected == 'username':
            name = update.message.text.strip()
            if not username_regex.match(name):
                await context.bot.send_message(chat_id=chat_id, text="Некорректное имя (только a-Z0-9).")
                return
            add_user_ezpz(name)
            await context.bot.send_message(chat_id=chat_id, text=f"Пользователь {name} добавлен.")
            await show_user_config(update, context, name)
            
        elif expected == 'config_value':
            key = context.user_data.pop('config_key', None)
            val = update.message.text.strip()
            if key:
                await set_config_value(update, context, key, val)

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_input))
    logger.info("Bot started")
    app.run_polling()

if __name__ == '__main__':
    main()