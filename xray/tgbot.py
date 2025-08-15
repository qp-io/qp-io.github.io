import os
import re
import subprocess
import io
import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Определение состояний для ConversationHandler
USERNAME, CONFIRM_DELETE = range(2)

token = os.environ['BOT_TOKEN']
admin = os.environ['BOT_ADMIN']
username_regex = re.compile("^[a-zA-Z0-9]+$")
command = 'bash <(curl -sL https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh) '

def run_command(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True)
    except subprocess.CalledProcessError as e:
        return f"Error: {e}"

def get_users_ezpz():
    local_command = command + '--list-users'
    result = run_command(local_command)
    return [user for user in result.split('\n') if user.strip()]

def get_config_ezpz(username):
    local_command = command + f'--show-config {username}'
    return run_command(local_command)

def add_user_ezpz(username):
    local_command = command + f'--add-user {username}'
    return run_command(local_command)

def delete_user_ezpz(username):
    local_command = command + f'--delete-user {username}'
    return run_command(local_command)

def restricted(func):
    async def wrapped(update, context, *args, **kwargs):
        username = None
        if update.message:
            username = update.message.from_user.username
        elif update.callback_query and update.callback_query.message:
            username = update.callback_query.from_user.username
        
        if not username:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text='Cannot identify your username. Please set a public username in Telegram settings.'
            )
            return
        
        admin_list = admin.split(',')
        if username in admin_list:
            return await func(update, context, *args, **kwargs)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text='You are not authorized to use this bot.'
            )
    return wrapped

@restricted
async def start(update, context):
    commands_text = "Reality-EZPZ User Management Bot\nChoose an option:"
    keyboard = [
        [InlineKeyboardButton('Add User', callback_data='add_user')],
        [InlineKeyboardButton('Delete User', callback_data='delete_user')],
        [InlineKeyboardButton('View Users', callback_data='show_user')],
        [InlineKeyboardButton('View Server Config', callback_data='server_config')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=commands_text, 
        reply_markup=reply_markup
    )

@restricted
async def users_list(update, context, message, action):
    users = get_users_ezpz()
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(user, callback_data=f'{action}!{user}')])
    keyboard.append([InlineKeyboardButton('Back', callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=message, 
        reply_markup=reply_markup
    )

@restricted
async def show_user(update, context, username):
    keyboard = [[InlineKeyboardButton('Back', callback_data='show_user')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=f'Config for "{username}":', 
        parse_mode='HTML'
    )
    config_list = get_config_ezpz(username)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=f'<pre>{config_list}</pre>', 
        parse_mode='HTML',
        reply_markup=reply_markup
    )

@restricted
async def add_user(update, context):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text='Enter username:'
    )
    context.user_data['expected_input'] = 'username'
    return USERNAME

@restricted
async def user_input(update, context):
    if 'expected_input' not in context.user_
        return
    
    expected_input = context.user_data['expected_input']
    del context.user_data['expected_input']
    
    if expected_input == 'username':
        username = update.message.text
        if username in get_users_ezpz():
            await update.message.reply_text(f'User "{username}" exists, try another username.')
            await add_user(update, context)
            return
        if not username_regex.match(username):
            await update.message.reply_text('Username can only contain A-Z, a-z and 0-9, try another username.')
            await add_user(update, context)
            return
        add_user_ezpz(username)
        await update.message.reply_text(f'User "{username}" is created.')
        await show_user(update, context, username)
    
    return ConversationHandler.END

@restricted
async def button(update, context):
    query = update.callback_query
    await query.answer()
    
    response = query.data.split('!')
    
    if response[0] == 'start':
        await start(update, context)
    elif response[0] == 'show_user':
        if len(response) > 1:
            await show_user(update, context, response[1])
        else:
            await users_list(update, context, 'Select user to view:', 'show_user')
    elif response[0] == 'delete_user':
        if len(response) > 1:
            context.user_data['user_to_delete'] = response[1]
            keyboard = [
                [InlineKeyboardButton('Yes', callback_data='approve_delete!'+response[1])],
                [InlineKeyboardButton('No', callback_data='delete_user')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f'Are you sure you want to delete user "{response[1]}"?',
                reply_markup=reply_markup
            )
        else:
            await users_list(update, context, 'Select user to delete:', 'delete_user')
    elif response[0] == 'add_user':
        await add_user(update, context)
    elif response[0] == 'approve_delete' and len(response) > 1:
        delete_user_ezpz(response[1])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f'User "{response[1]}" has been deleted.'
        )
        await start(update, context)
    elif response[0] == 'server_config':
        local_command = command + '--show-server-config'
        config = run_command(local_command)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f'<pre>{config}</pre>',
            parse_mode='HTML'
        )
        await start(update, context)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f'Button pressed: {response[0]}'
        )

async def cancel(update, context):
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

def main():
    application = Application.builder().token(token).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))
    
    # Обработчик для добавления пользователя (с использованием ConversationHandler)
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_user, pattern='^add_user$')],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()