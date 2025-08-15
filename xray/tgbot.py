#!/usr/bin/env python3
import os
import re
import io
import subprocess
import logging
from typing import Optional

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

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config from env
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    logger.error("BOT_TOKEN env is not set")
    raise SystemExit("BOT_TOKEN env is not set")

ADMIN = os.environ.get('BOT_ADMIN', '')  # comma separated list of usernames OR numeric ids
username_regex = re.compile(r"^[a-zA-Z0-9]+$")

# The reality-ezpz installer command used by the bot
command = 'bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh) '

# Helpers
def run_command(cmd: str) -> str:
    try:
        process = subprocess.Popen(['/bin/bash', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, err = process.communicate(timeout=120)
        if process.returncode != 0:
            logger.warning("Command exited %s: %s", process.returncode, err.decode().strip())
        return output.decode()
    except Exception as e:
        logger.exception("run_command failed: %s", e)
        return ""

def get_users_ezpz():
    out = run_command(command + '--list-users')
    return [line for line in out.splitlines() if line.strip()]

def get_config_ezpz(username: str):
    local_command = command + f"--show-user {username} | grep -E '://|^\\{{\"dns\"'"
    out = run_command(local_command)
    return [line for line in out.splitlines() if line.strip()]

def delete_user_ezpz(username: str):
    run_command(command + f'--delete-user {username}')

def add_user_ezpz(username: str):
    run_command(command + f'--add-user {username}')

# Access control decorator
def restricted(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        username: Optional[str] = None
        user_id: Optional[int] = None

        if update.effective_user:
            username = update.effective_user.username
            user_id = update.effective_user.id

        # build admin list (strip spaces)
        raw_admins = [a.strip() for a in ADMIN.split(',') if a.strip()]
        # check username or numeric id
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
                await context.bot.send_message(chat_id=chat_id, text='You are not authorized to use this bot.')
            logger.warning("Unauthorized access attempt: username=%s id=%s", username, user_id)
    return wrapped

# Handlers
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton('Show User', callback_data='show_user')],
        [InlineKeyboardButton('Add User', callback_data='add_user')],
        [InlineKeyboardButton('Delete User', callback_data='delete_user')],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="Reality-EZPZ User Management Bot\n\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, callback: str):
    chat_id = update.effective_chat.id
    users = get_users_ezpz()
    keyboard = [[InlineKeyboardButton(user, callback_data=f'{callback}!{user}')] for user in users]
    keyboard.append([InlineKeyboardButton('Back', callback_data='start')])
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def show_user(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='show_user')]])
    await context.bot.send_message(chat_id=chat_id, text=f'Config for "{username}":', parse_mode='HTML')
    config_list = get_config_ezpz(username)
    ipv6_pattern = r'"server":"[0-9a-fA-F:]+"'

    if not config_list:
        await context.bot.send_message(chat_id=chat_id, text="No config found for this user.", reply_markup=back_markup)
        return

    for config in config_list:
        config = config.strip()
        if not config:
            continue

        if config.endswith("-ipv6") or re.search(ipv6_pattern, config):
            config_text = f"IPv6 Config:\n<pre>{config}</pre>"
        else:
            config_text = f"<pre>{config}</pre>"

        qr_img = qrcode.make(config)
        bio = io.BytesIO()
        qr_img.save(bio, 'PNG')
        bio.seek(0)

        await context.bot.send_photo(chat_id=chat_id, photo=bio, caption=config_text, parse_mode='HTML', reply_markup=back_markup)

@restricted
async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    chat_id = update.effective_chat.id
    users = get_users_ezpz()
    if len(users) <= 1:
        await context.bot.send_message(
            chat_id=chat_id,
            text='You cannot delete the only user.\nAt least one user is needed.\nCreate a new user, then delete this one.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='start')]])
        )
        return
    keyboard = [
        [InlineKeyboardButton('Delete', callback_data=f'approve_delete!{username}')],
        [InlineKeyboardButton('Cancel', callback_data='delete_user')]
    ]
    await context.bot.send_message(chat_id=chat_id, text=f'Are you sure to delete "{username}"?', reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data['expected_input'] = 'username'
    keyboard = [[InlineKeyboardButton('Cancel', callback_data='cancel')]]
    await context.bot.send_message(chat_id=chat_id, text='Enter the username:', reply_markup=InlineKeyboardMarkup(keyboard))

@restricted
async def approve_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    delete_user_ezpz(username)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f'User {username} has been deleted.')

@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('expected_input', None)
    await start(update, context)

@restricted
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        response = query.data.split('!')
        # single actions
        if len(response) == 1:
            cmd = response[0]
            if cmd == 'start':
                await start(update, context)
            elif cmd == 'cancel':
                await cancel(update, context)
            elif cmd == 'show_user':
                await users_list(update, context, 'Select user to view config:', 'show_user')
            elif cmd == 'delete_user':
                await users_list(update, context, 'Select user to delete:', 'delete_user')
            elif cmd == 'add_user':
                await add_user(update, context)
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f'Button pressed: {cmd}')
        # actions with parameter
        else:
            action, param = response[0], response[1]
            if action == 'show_user':
                await show_user(update, context, param)
            elif action == 'delete_user':
                await delete_user(update, context, param)
            elif action == 'approve_delete':
                await approve_delete(update, context, param)

@restricted
async def user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'expected_input' in context.user_data:
        expected = context.user_data.pop('expected_input', None)
        if expected == 'username':
            username = (update.message.text or '').strip()
            if not username:
                await context.bot.send_message(chat_id=update.effective_chat.id, text='Empty username, try again.')
                await add_user(update, context)
                return

            if username in get_users_ezpz():
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f'User "{username}" exists, try another username.')
                await add_user(update, context)
                return

            if not username_regex.match(username):
                await context.bot.send_message(chat_id=update.effective_chat.id, text='Username can only contain A-Z, a-z and 0-9.')
                await add_user(update, context)
                return

            add_user_ezpz(username)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f'User "{username}" is created.')
            await show_user(update, context, username)

# Main
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_input))

    logger.info("Starting bot")
    app.run_polling()

if __name__ == '__main__':
    main()