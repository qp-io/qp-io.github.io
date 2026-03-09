#!/usr/bin/env python3
"""
Reality-EZPZ Telegram Bot.

Стратегия:
- Обычные параметры (transport, security, port, server, domain, path, host_header, core):
  записываем в конфиг-файл через write_config(), затем запускаем скрипт БЕЗ аргументов.
  Скрипт читает файл и применяет всё сам.
- WARP включение: записываем warp=ON, запускаем скрипт — он видит warp=ON без ключей
  и автоматически вызывает warp_create_account.
- WARP+ лицензия: запускаем скрипт с --warp-license XXX (скрипт сам ставит warp=ON).
- WARP выключение: записываем warp=OFF, запускаем скрипт — он удаляет аккаунт.
"""

import io
import logging
import os
import re
import subprocess
import zipfile
from datetime import datetime

import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────────────

DATA_DIR    = '/opt/reality-ezpz'
CONFIG_FILE = os.path.join(DATA_DIR, 'config')
USERS_FILE  = os.path.join(DATA_DIR, 'users')

SCRIPT_URL = 'https://raw.githubusercontent.com/qp-io/qp-io.github.io/refs/heads/main/xray/reality-ezpz.sh'

# sed убирает -it чтобы docker работал без TTY в среде бота
BASE_CMD = (
    'function systemctl() { :; }; export -f systemctl; '
    f'bash <(curl -sL {SCRIPT_URL} | sed "s/docker run --rm -it/docker run --rm/g") '
)

TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise SystemExit('BOT_TOKEN env is not set')

ADMIN = os.environ.get('BOT_ADMIN', '')


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def run_script(extra_args: str = '', timeout: int = 300) -> tuple[int, str]:
    """
    Запускает основной скрипт. extra_args добавляются после команды.
    Возвращает (exit_code, output).
    При extra_args='' — скрипт читает конфиг и пересоздаёт всё.
    """
    cmd = BASE_CMD + extra_args
    logger.info(f'run_script: {extra_args!r}')
    try:
        proc = subprocess.Popen(
            cmd, shell=True, executable='/bin/bash',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=timeout)
        out_s = out.decode(errors='ignore').strip()
        err_s = err.decode(errors='ignore').strip()
        if proc.returncode != 0 and err_s:
            combined = out_s + '\n\n[stderr]\n' + err_s
        else:
            combined = out_s
        return proc.returncode, combined.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, f'⏱ Таймаут {timeout}с превышен.'
    except Exception as e:
        return 1, str(e)


def read_config() -> dict:
    conf = {}
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f'read_config: {e}')
    return conf


def write_config(key: str, value: str):
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
        logger.error(f'write_config({key}={value!r}): {e}')


def get_users() -> list[str]:
    """Читает список пользователей из файла напрямую."""
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


def get_user_links(name: str) -> list[str]:
    """Возвращает vless:// / tuic:// / hy2:// / JSON ссылки пользователя."""
    rc, out = run_script(f'--show-user {name}', timeout=120)
    result = []
    for line in out.splitlines():
        s = line.strip()
        if s and ('://' in s or s.startswith('{"dns"')):
            result.append(s)
    return result


def make_backup() -> str | None:
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')
    path = f'/tmp/backup_{ts}.zip'
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in ('config', 'users'):
                p = os.path.join(DATA_DIR, name)
                if os.path.exists(p):
                    z.write(p, arcname=name)
        return path
    except Exception:
        return None


# ─── Матрица совместимости ────────────────────────────────────────────────────

def check_transport_conflict(transport: str, core: str, security: str) -> tuple[str | None, list]:
    """
    Проверяет совместимость выбранного transport с текущими core и security.
    Возвращает (сообщение_ошибки, клавиатура_предложений) или (None, []).
    """
    xray_only  = ('xhttp', 'xhttp3')
    sbox_only  = ('tuic', 'hysteria2', 'shadowtls')
    no_reality = ('ws', 'tuic', 'hysteria2', 'xhttp3')
    need_tls   = ('xhttp3',)   # нельзя с notls
    no_notls   = ('xhttp3',)

    if transport in xray_only and core != 'xray':
        return (
            f'<b>{transport}</b> работает только с ядром <b>xray</b>.',
            [[InlineKeyboardButton('Сменить core → xray', callback_data='set!core!xray')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    if transport in sbox_only and core != 'sing-box':
        return (
            f'<b>{transport}</b> работает только с ядром <b>sing-box</b>.',
            [[InlineKeyboardButton('Сменить core → sing-box', callback_data='set!core!sing-box')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    if transport in no_reality and security == 'reality':
        return (
            f'<b>{transport}</b> несовместим с <b>reality</b>.',
            [[InlineKeyboardButton('security → selfsigned',  callback_data='set!security!selfsigned'),
              InlineKeyboardButton('security → letsencrypt', callback_data='set!security!letsencrypt')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    if transport in no_notls and security == 'notls':
        return (
            f'<b>{transport}</b> (packet-up) требует TLS. Нельзя использовать с <b>notls</b>.',
            [[InlineKeyboardButton('security → selfsigned',  callback_data='set!security!selfsigned'),
              InlineKeyboardButton('security → letsencrypt', callback_data='set!security!letsencrypt')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    return None, []


def check_core_conflict(new_core: str, transport: str) -> tuple[str | None, list]:
    if new_core == 'sing-box' and transport in ('xhttp', 'xhttp3'):
        return (
            f'Транспорт <b>{transport}</b> несовместим с <b>sing-box</b>. Сначала смените транспорт.',
            [[InlineKeyboardButton('transport → tcp',   callback_data='set!transport!tcp'),
              InlineKeyboardButton('transport → ws',    callback_data='set!transport!ws')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    if new_core == 'xray' and transport in ('tuic', 'hysteria2', 'shadowtls'):
        return (
            f'Транспорт <b>{transport}</b> несовместим с <b>xray</b>. Сначала смените транспорт.',
            [[InlineKeyboardButton('transport → tcp',   callback_data='set!transport!tcp'),
              InlineKeyboardButton('transport → ws',    callback_data='set!transport!ws')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    return None, []


def check_security_conflict(new_sec: str, transport: str) -> tuple[str | None, list]:
    if new_sec == 'reality' and transport in ('ws', 'tuic', 'hysteria2', 'xhttp3'):
        return (
            f'Security <b>reality</b> несовместима с транспортом <b>{transport}</b>.',
            [[InlineKeyboardButton('transport → tcp',   callback_data='set!transport!tcp'),
              InlineKeyboardButton('transport → xhttp', callback_data='set!transport!xhttp')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    if new_sec == 'notls' and transport in ('xhttp3',):
        return (
            f'Security <b>notls</b> несовместима с транспортом <b>{transport}</b> (требует TLS).',
            [[InlineKeyboardButton('transport → xhttp', callback_data='set!transport!xhttp'),
              InlineKeyboardButton('transport → tcp',   callback_data='set!transport!tcp')],
             [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')]]
        )
    return None, []


# ─── Декоратор доступа ────────────────────────────────────────────────────────

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        u = update.effective_user
        if not u:
            return
        uid   = str(u.id)
        uname = (u.username or '').lstrip('@')
        admins = {x.strip().lstrip('@') for x in ADMIN.split(',') if x.strip()}
        if uid in admins or (uname and uname in admins):
            return await func(update, context, *a, **kw)
        await context.bot.send_message(update.effective_chat.id, '⛔ Нет доступа.')
    return wrapper


# ─── Отправка результата ──────────────────────────────────────────────────────

async def send_result(bot, chat_id: int, rc: int, out: str,
                      ok_text='✅ Готово.', fail_text='⚠️ Ошибка.'):
    ok = rc == 0 and 'Команда успешно выполнена' in out
    label = ok_text if ok else fail_text
    snippet = out[:3800] if out else '(нет вывода)'
    await bot.send_message(
        chat_id,
        f'{label}\n<blockquote>{snippet}</blockquote>',
        parse_mode='HTML',
    )


# ─── Меню настроек ───────────────────────────────────────────────────────────

async def show_main(bot, chat_id: int):
    await bot.send_message(
        chat_id,
        '🤖 <b>Reality-EZPZ</b>\nВыберите раздел:',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('👥 Пользователи', callback_data='m_users')],
            [InlineKeyboardButton('⚙️ Настройки',    callback_data='m_settings')],
        ]),
        parse_mode='HTML',
    )


async def show_settings(bot, chat_id: int):
    c = read_config()
    transport = c.get('transport', '?')
    security  = c.get('security',  '?')
    warp      = c.get('warp',      'OFF')
    server    = c.get('server',    '?')

    # Подсказка для xhttp3
    note = ''
    if transport == 'xhttp3':
        note = (
            '\n\n💡 <b>xhttp3</b> = xhttp + mode=packet-up\n'
            '• Работает поверх TCP/TLS через HAProxy\n'
            '• Оптимален для CDN/обхода DPI\n'
            '• Security: letsencrypt или selfsigned'
        )

    text = (
        '⚙️ <b>Настройки</b>\n\n'
        f'Core:       <code>{c.get("core","?")}</code>\n'
        f'Transport:  <code>{transport}</code>\n'
        f'Security:   <code>{security}</code>\n'
        f'Port:       <code>{c.get("port","?")}</code>\n'
        f'Server:     <code>{server}</code>\n'
        f'SNI/Domain: <code>{c.get("domain","?")}</code>\n'
        f'Path:       <code>/{c.get("service_path","")}</code>\n'
        f'Host hdr:   <code>{c.get("host_header","—") or "—"}</code>\n'
        f'WARP:       <b>{warp}</b>'
        + note
    )

    warp_btn = (
        [InlineKeyboardButton('🔴 Выключить WARP', callback_data='warp_off')]
        if warp == 'ON' else
        [InlineKeyboardButton('🟢 Включить WARP',  callback_data='sub!warp')]
    )

    kb = [
        [InlineKeyboardButton('🔧 Core',      callback_data='sub!core'),
         InlineKeyboardButton('🚀 Transport', callback_data='sub!transport')],
        [InlineKeyboardButton('🔒 Security',  callback_data='sub!security'),
         *warp_btn],
        [InlineKeyboardButton('🌐 Server',    callback_data='ask!server'),
         InlineKeyboardButton('🔌 Port',      callback_data='ask!port')],
        [InlineKeyboardButton('📋 SNI/Domain',callback_data='ask!domain'),
         InlineKeyboardButton('📁 Path',      callback_data='ask!path')],
        [InlineKeyboardButton('🏷 Host Header',callback_data='ask!host_header')],
        [InlineKeyboardButton('🔄 Применить / Перезапустить', callback_data='do_apply')],
        [InlineKeyboardButton('📥 Бэкап',    callback_data='do_backup'),
         InlineKeyboardButton('🔙 Главное',  callback_data='main')],
    ]
    await bot.send_message(chat_id, text,
                           reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')


# ─── ask_value ────────────────────────────────────────────────────────────────

async def ask_value(bot, chat_id: int, user_data: dict, param: str):
    user_data['state'] = 'setting'
    user_data['param'] = param
    hints = {
        'server': (
            '🌐 <b>Введите IP или домен сервера</b>\n'
            '<i>Пример: 1.2.3.4 или vpn.example.com</i>\n\n'
            'Используется в адресе подключения клиента.'
        ),
        'domain': (
            '📋 <b>Введите SNI/Domain</b>\n'
            '<i>Для reality — маскировочный домен (напр. yahoo.com)\n'
            'Для TLS — домен вашего сертификата</i>'
        ),
        'port':        '🔌 Введите порт (1–65535)',
        'path':        '📁 Введите путь без /\n<i>Отправьте пустую строку для сброса</i>',
        'host_header': '🏷 Введите Host заголовок:\n<i>Пример: example.com</i>',
        'warp_license':'⭐ Введите лицензию WARP+:\n<i>Формат: xxxxxxxx-xxxxxxxx-xxxxxxxx</i>',
    }
    txt = hints.get(param, f'Введите значение для <b>{param}</b>:')
    await bot.send_message(
        chat_id, txt,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('❌ Отмена', callback_data='m_settings')]
        ]),
        parse_mode='HTML',
    )


# ─── Применение параметра ─────────────────────────────────────────────────────

async def apply_param(bot, chat_id: int, param: str, val: str, conf: dict):
    """Записывает параметр в конфиг и перезапускает скрипт."""

    # Проверки совместимости
    if param == 'transport':
        err, btns = check_transport_conflict(val, conf.get('core','xray'), conf.get('security','reality'))
        if err:
            await bot.send_message(chat_id, f'⚠️ {err}',
                                   parse_mode='HTML', reply_markup=InlineKeyboardMarkup(btns))
            return

    if param == 'core':
        err, btns = check_core_conflict(val, conf.get('transport','tcp'))
        if err:
            await bot.send_message(chat_id, f'⚠️ {err}',
                                   parse_mode='HTML', reply_markup=InlineKeyboardMarkup(btns))
            return

    if param == 'security':
        err, btns = check_security_conflict(val, conf.get('transport','tcp'))
        if err:
            await bot.send_message(chat_id, f'⚠️ {err}',
                                   parse_mode='HTML', reply_markup=InlineKeyboardMarkup(btns))
            return

    # Нормализация
    if param == 'path':
        param = 'service_path'
        val = val.lstrip('/')

    # Запись
    write_config(param, val)

    # Синхронизация domain с server для TLS-режимов
    if param == 'server':
        sec = conf.get('security', 'reality')
        tr  = conf.get('transport', 'tcp')
        if sec not in ('reality', 'notls') and tr != 'shadowtls':
            write_config('domain', val)

    # Применить
    await bot.send_message(chat_id, '⏳ Применяю настройки...')
    rc, out = run_script()
    await send_result(bot, chat_id, rc, out)
    await show_settings(bot, chat_id)


# ─── QR-код и ссылки ─────────────────────────────────────────────────────────

async def send_user_links(bot, chat_id: int, name: str, links: list[str]):
    if not links:
        await bot.send_message(chat_id, f'❌ Не удалось получить конфиг <b>{name}</b>.\nПопробуйте <b>Применить / Перезапустить</b>.', parse_mode='HTML')
        return
    for link in links:
        try:
            qr  = qrcode.make(link)
            bio = io.BytesIO()
            qr.save(bio, 'PNG')
            bio.seek(0)
            await bot.send_photo(chat_id, photo=bio,
                                 caption=f'<code>{link[:1000]}</code>', parse_mode='HTML')
        except Exception:
            await bot.send_message(chat_id, f'<code>{link[:3000]}</code>', parse_mode='HTML')


# ─── Хэндлеры ────────────────────────────────────────────────────────────────

@restricted
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main(context.bot, update.effective_chat.id)


@restricted
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data  = q.data
    chat  = update.effective_chat.id
    udata = context.user_data

    # ── Навигация ─────────────────────────────────────────────────────────────

    if data == 'main':
        await show_main(context.bot, chat)
        return

    if data == 'm_settings':
        await show_settings(context.bot, chat)
        return

    if data == 'm_users':
        kb = [
            [InlineKeyboardButton('📜 Список',   callback_data='u_list'),
             InlineKeyboardButton('➕ Добавить', callback_data='u_add')],
            [InlineKeyboardButton('➖ Удалить',  callback_data='u_del_m'),
             InlineKeyboardButton('🔙 Назад',    callback_data='main')],
        ]
        await context.bot.send_message(chat, '👥 <b>Пользователи</b>',
                                       reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        return

    # ── Пользователи ──────────────────────────────────────────────────────────

    if data in ('u_list', 'u_del_m'):
        mode = 'show' if data == 'u_list' else 'del'
        users = get_users()
        if not users:
            await context.bot.send_message(chat, 'Нет пользователей.')
            return
        cb_pfx = 'u_show' if mode == 'show' else 'u_del'
        kb = [[InlineKeyboardButton(u, callback_data=f'{cb_pfx}!{u}')] for u in users]
        kb.append([InlineKeyboardButton('🔙 Назад', callback_data='m_users')])
        await context.bot.send_message(chat, 'Выберите пользователя:',
                                       reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == 'u_add':
        udata['state'] = 'add_user'
        await context.bot.send_message(
            chat, 'Введите имя нового пользователя (A-Z, a-z, 0-9):',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('❌ Отмена', callback_data='m_users')]
            ]),
        )
        return

    if data.startswith('u_show!'):
        name = data[7:]
        await context.bot.send_message(chat, f'⏳ Получаю конфиг <b>{name}</b>...', parse_mode='HTML')
        links = get_user_links(name)
        await send_user_links(context.bot, chat, name, links)
        await context.bot.send_message(
            chat, '↩️',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔙 Пользователи', callback_data='m_users')]
            ]),
        )
        return

    if data.startswith('u_del!'):
        name = data[6:]
        await context.bot.send_message(
            chat, f'Удалить <b>{name}</b>?', parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Да',  callback_data=f'confirm_del!{name}'),
                 InlineKeyboardButton('❌ Нет', callback_data='m_users')],
            ]),
        )
        return

    if data.startswith('confirm_del!'):
        name = data[12:]
        rc, out = run_script(f'--delete-user {name}')
        await context.bot.send_message(
            chat,
            '✅ Удалён.' if rc == 0 else f'❌ Ошибка:\n{out[:500]}'
        )
        users = get_users()
        if users:
            kb = [[InlineKeyboardButton(u, callback_data=f'u_del!{u}')] for u in users]
            kb.append([InlineKeyboardButton('🔙 Назад', callback_data='m_users')])
            await context.bot.send_message(chat, 'Пользователи:',
                                           reply_markup=InlineKeyboardMarkup(kb))
        return

    # ── Подменю выбора параметра ───────────────────────────────────────────────

    if data.startswith('sub!'):
        what = data[4:]
        c = read_config()
        cur_core      = c.get('core',      'xray')
        cur_transport = c.get('transport', 'tcp')
        cur_security  = c.get('security',  'reality')
        cur_warp      = c.get('warp',      'OFF')

        if what == 'core':
            kb = [
                [InlineKeyboardButton(('✅ ' if cur_core=='xray'     else '') + '⚡ xray',
                                     callback_data='set!core!xray'),
                 InlineKeyboardButton(('✅ ' if cur_core=='sing-box' else '') + '📦 sing-box',
                                     callback_data='set!core!sing-box')],
                [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')],
            ]
            await context.bot.send_message(chat, '🔧 Выберите ядро:',
                                           reply_markup=InlineKeyboardMarkup(kb))

        elif what == 'transport':
            def t_btn(k: str, label: str) -> InlineKeyboardButton:
                pfx = '✅ ' if k == cur_transport else ''
                return InlineKeyboardButton(pfx + label, callback_data=f'set!transport!{k}')

            kb = [
                [t_btn('tcp','TCP'),       t_btn('http','HTTP'),     t_btn('grpc','gRPC')],
                [t_btn('ws','WS'),         t_btn('xhttp','xHTTP'),   t_btn('xhttp3','xHTTP3★')],
                [t_btn('tuic','TUIC'),     t_btn('hysteria2','Hy2'), t_btn('shadowtls','ShadowTLS')],
                [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')],
            ]
            note = (
                '🚀 <b>Выберите транспорт</b>\n\n'
                '★ xHTTP3 = xhttp + mode=packet-up (CDN-режим, TCP/TLS)'
            )
            await context.bot.send_message(chat, note,
                                           reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

        elif what == 'security':
            def s_btn(k: str, label: str) -> InlineKeyboardButton:
                pfx = '✅ ' if k == cur_security else ''
                return InlineKeyboardButton(pfx + label, callback_data=f'set!security!{k}')

            kb = [
                [s_btn('reality','🔐 reality'),       s_btn('letsencrypt','🔑 letsencrypt')],
                [s_btn('selfsigned','📜 selfsigned'), s_btn('notls','🔓 notls')],
                [InlineKeyboardButton('🔙 Назад', callback_data='m_settings')],
            ]
            await context.bot.send_message(chat, '🔒 Выберите security:',
                                           reply_markup=InlineKeyboardMarkup(kb))

        elif what == 'warp':
            kb = [
                [InlineKeyboardButton('🆓 WARP бесплатный',    callback_data='warp_free')],
                [InlineKeyboardButton('⭐ WARP+ (с лицензией)', callback_data='ask!warp_license')],
                [InlineKeyboardButton('🔙 Назад',              callback_data='m_settings')],
            ]
            await context.bot.send_message(
                chat,
                '🌐 <b>Cloudflare WARP</b>\n\n'
                '• <b>Бесплатный</b> — базовое шифрование, без лимитов\n'
                '• <b>WARP+</b> — быстрее, нужна лицензия (Cloudflare One)',
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode='HTML',
            )
        return

    # ── Запрос ввода текста ───────────────────────────────────────────────────

    if data.startswith('ask!'):
        param = data[4:]
        await ask_value(context.bot, chat, udata, param)
        return

    # ── WARP управление ───────────────────────────────────────────────────────

    if data == 'warp_free':
        # Пишем warp=ON без лицензии → скрипт сам создаст аккаунт
        write_config('warp', 'ON')
        write_config('warp_license', '')
        await context.bot.send_message(
            chat,
            '⏳ Включаю WARP (бесплатный)...\n'
            'Это может занять 1–2 минуты.'
        )
        rc, out = run_script(timeout=240)
        await send_result(context.bot, chat, rc, out, '✅ WARP включён.', '❌ Ошибка WARP.')
        await show_settings(context.bot, chat)
        return

    if data == 'warp_off':
        write_config('warp', 'OFF')
        await context.bot.send_message(chat, '⏳ Отключаю WARP...')
        rc, out = run_script()
        await send_result(context.bot, chat, rc, out, '✅ WARP отключён.', '❌ Ошибка.')
        await show_settings(context.bot, chat)
        return

    # ── Применить / Перезапустить ─────────────────────────────────────────────

    if data == 'do_apply':
        await context.bot.send_message(chat, '⏳ Применяю конфигурацию...')
        rc, out = run_script()
        await send_result(context.bot, chat, rc, out)
        await show_settings(context.bot, chat)
        return

    if data == 'do_backup':
        msg = await context.bot.send_message(chat, '📦 Создаю бэкап...')
        path = make_backup()
        if not path:
            await context.bot.edit_message_text(
                chat_id=chat, message_id=msg.message_id, text='❌ Ошибка бэкапа.'
            )
            return
        with open(path, 'rb') as f:
            await context.bot.send_document(chat, document=f, filename='backup.zip')
        os.remove(path)
        await context.bot.delete_message(chat, msg.message_id)
        return

    # ── set!param!value ───────────────────────────────────────────────────────

    if data.startswith('set!'):
        parts = data.split('!')
        if len(parts) < 3:
            return
        param, val = parts[1], parts[2]
        conf = read_config()
        await apply_param(context.bot, chat, param, val, conf)
        return


@restricted
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.pop('state', None)
    text  = (update.message.text or '').strip()
    chat  = update.effective_chat.id

    if state == 'add_user':
        if not re.match(r'^[a-zA-Z0-9]{1,32}$', text):
            await update.message.reply_text('❌ Только A-Z, a-z, 0-9 (до 32 символов).')
            return
        await update.message.reply_text(
            f'⏳ Создаю пользователя <b>{text}</b>...', parse_mode='HTML'
        )
        rc, out = run_script(f'--add-user {text}')
        if rc != 0:
            await update.message.reply_text(f'❌ Ошибка:\n{out[:500]}')
            return
        await update.message.reply_text(
            f'✅ Пользователь <b>{text}</b> создан.', parse_mode='HTML'
        )
        links = get_user_links(text)
        await send_user_links(context.bot, chat, text, links)
        await context.bot.send_message(
            chat, '↩️',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔙 Пользователи', callback_data='m_users')]
            ]),
        )
        return

    if state == 'setting':
        param = context.user_data.pop('param', None)
        if not param:
            return

        # Валидация
        if param == 'port':
            if not text.isdigit() or not 1 <= int(text) <= 65535:
                await update.message.reply_text('❌ Порт: число от 1 до 65535.')
                return

        if param == 'server':
            ip_re  = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
            dom_re = re.compile(r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$')
            if not ip_re.match(text) and not dom_re.match(text):
                await update.message.reply_text(
                    '❌ Неверный формат.\nОжидается IP (1.2.3.4) или домен (example.com).'
                )
                return

        if param == 'warp_license':
            if not re.match(r'^[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', text):
                await update.message.reply_text(
                    '❌ Неверный формат.\nОжидается: <code>xxxxxxxx-xxxxxxxx-xxxxxxxx</code>',
                    parse_mode='HTML',
                )
                return
            # WARP+ — передаём лицензию аргументом (скрипт сам создаёт аккаунт + добавляет лицензию)
            await update.message.reply_text('⏳ Включаю WARP+...\nМожет занять 1–2 минуты.')
            rc, out = run_script(f'--warp-license {text}', timeout=240)
            await send_result(context.bot, chat, rc, out, '✅ WARP+ включён.', '❌ Ошибка WARP+.')
            await show_settings(context.bot, chat)
            return

        conf = read_config()
        await apply_param(context.bot, chat, param, text, conf)
        return

    # Неожиданное сообщение
    await show_main(context.bot, chat)


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    logger.info('Bot started.')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
