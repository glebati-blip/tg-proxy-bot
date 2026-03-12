import requests
import re
import socket
import concurrent.futures
import time
import schedule
import threading
import os
import json
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('CHAT_ID'))

if not BOT_TOKEN or not ADMIN_ID:
    print(" ОШИБКА: Не найден BOT_TOKEN или CHAT_ID в .env файле!")
    print("Создай файл .env с содержимым:")
    print("BOT_TOKEN=твой_токен")
    print("CHAT_ID=твой_id")
    exit(1)

def init_database():
    conn = sqlite3.connect('proxy_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_allowed BOOLEAN DEFAULT 0,
            is_blocked BOOLEAN DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            interval_minutes INTEGER DEFAULT 0,
            joined_date TIMESTAMP,
            last_active TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_requests (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            request_date TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proxy_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy_link TEXT UNIQUE,
            region TEXT,
            ping REAL,
            domain TEXT,
            first_seen TIMESTAMP,
            last_checked TIMESTAMP,
            is_best BOOLEAN DEFAULT 0,
            times_selected INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS best_proxy (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            proxy_link TEXT,
            region TEXT,
            ping REAL,
            domain TEXT,
            selected_date TIMESTAMP,
            times_selected INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS check_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_date TIMESTAMP,
            total_found INTEGER,
            ru_found INTEGER,
            eu_found INTEGER,
            best_ping REAL,
            best_region TEXT
        )
    ''')
    
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, is_allowed, is_admin, joined_date)
        VALUES (?, ?, ?, ?, ?)
    ''', (ADMIN_ID, 'admin', 1, 1, datetime.now()))
    
    conn.commit()
    conn.close()
    print(" База данных инициализирована")

def execute_query(query, params=(), fetch_one=False, fetch_all=False, commit=False):
    conn = None
    try:
        conn = sqlite3.connect('proxy_bot.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
            return True
        
        if fetch_one:
            return cursor.fetchone()
        elif fetch_all:
            return cursor.fetchall()
        else:
            return cursor
    except Exception as e:
        print(f" Ошибка БД: {e}")
        return None if not fetch_one and not fetch_all else []
    finally:
        if conn:
            conn.close()

def is_allowed(user_id):
    if user_id == ADMIN_ID:
        return True
    result = execute_query(
        "SELECT is_allowed, is_blocked FROM users WHERE user_id = ?", 
        (user_id,), 
        fetch_one=True
    )
    if not result:
        return False
    return result['is_allowed'] == 1 and result['is_blocked'] == 0

def is_blocked(user_id):
    result = execute_query(
        "SELECT is_blocked FROM users WHERE user_id = ?", 
        (user_id,), 
        fetch_one=True
    )
    return result and result['is_blocked'] == 1

def has_pending_request(user_id):
    result = execute_query(
        "SELECT status FROM access_requests WHERE user_id = ?", 
        (user_id,), 
        fetch_one=True
    )
    return result and result['status'] == 'pending'

def add_user(user_id, username, first_name):
    execute_query('''
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, joined_date, last_active)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, datetime.now(), datetime.now()), commit=True)
    
    execute_query('''
        UPDATE users SET last_active = ? WHERE user_id = ?
    ''', (datetime.now(), user_id), commit=True)

def approve_user(user_id):
    execute_query('''
        UPDATE users SET is_allowed = 1, is_blocked = 0 WHERE user_id = ?
    ''', (user_id,), commit=True)
    
    execute_query(
        "DELETE FROM access_requests WHERE user_id = ?", 
        (user_id,), 
        commit=True
    )

def reject_user(user_id):
    execute_query(
        "DELETE FROM access_requests WHERE user_id = ?", 
        (user_id,), 
        commit=True
    )

def block_user(user_id):
    if user_id == ADMIN_ID:
        return False
    execute_query('''
        UPDATE users SET is_allowed = 0, is_blocked = 1 WHERE user_id = ?
    ''', (user_id,), commit=True)
    return True

def unblock_user(user_id):
    execute_query('''
        UPDATE users SET is_allowed = 1, is_blocked = 0 WHERE user_id = ?
    ''', (user_id,), commit=True)

def create_access_request(user_id, username, first_name):
    execute_query('''
        INSERT OR IGNORE INTO access_requests (user_id, username, first_name, request_date, status)
        VALUES (?, ?, ?, ?, 'pending')
    ''', (user_id, username, first_name, datetime.now()), commit=True)

def get_pending_requests():
    return execute_query(
        "SELECT * FROM access_requests WHERE status = 'pending' ORDER BY request_date DESC", 
        fetch_all=True
    ) or []

def get_pending_requests_count():
    result = execute_query(
        "SELECT COUNT(*) as count FROM access_requests WHERE status = 'pending'", 
        fetch_one=True
    )
    return result['count'] if result else 0

def get_allowed_users():
    return execute_query(
        "SELECT user_id, username, first_name, interval_minutes, joined_date, last_active FROM users WHERE is_allowed = 1 AND is_blocked = 0 AND user_id != ? ORDER BY joined_date DESC", 
        (ADMIN_ID,),
        fetch_all=True
    ) or []

def get_blocked_users():
    return execute_query(
        "SELECT user_id, username, first_name, joined_date, last_active FROM users WHERE is_blocked = 1 ORDER BY joined_date DESC", 
        fetch_all=True
    ) or []

def get_allowed_users_count():
    result = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE is_allowed = 1 AND is_blocked = 0 AND user_id != ?", 
        (ADMIN_ID,),
        fetch_one=True
    )
    return result['count'] if result else 0

def get_blocked_users_count():
    result = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE is_blocked = 1", 
        fetch_one=True
    )
    return result['count'] if result else 0

def set_user_interval(user_id, interval):
    execute_query(
        "UPDATE users SET interval_minutes = ? WHERE user_id = ?", 
        (interval, user_id), 
        commit=True
    )

def get_user_interval(user_id):
    result = execute_query(
        "SELECT interval_minutes FROM users WHERE user_id = ?", 
        (user_id,), 
        fetch_one=True
    )
    return result['interval_minutes'] if result else 0

def get_all_allowed_users_with_intervals():
    return execute_query(
        "SELECT user_id, interval_minutes FROM users WHERE is_allowed = 1 AND is_blocked = 0", 
        fetch_all=True
    ) or []

def save_proxy_to_cache(proxy_data):
    execute_query('''
        INSERT OR REPLACE INTO proxy_cache 
        (proxy_link, region, ping, domain, first_seen, last_checked)
        VALUES (?, ?, ?, ?, COALESCE(
            (SELECT first_seen FROM proxy_cache WHERE proxy_link = ?), 
            ?
        ), ?)
    ''', (
        proxy_data['link'], 
        proxy_data['region'], 
        proxy_data['ping'], 
        proxy_data.get('domain', 'unknown'),
        proxy_data['link'],
        datetime.now(),
        datetime.now()
    ), commit=True)

def get_cached_best_proxy():
    return execute_query(
        "SELECT * FROM best_proxy WHERE id = 1", 
        fetch_one=True
    )

def update_best_proxy(proxy_data):
    execute_query('''
        UPDATE proxy_cache 
        SET times_selected = times_selected + 1 
        WHERE proxy_link = ?
    ''', (proxy_data['link'],), commit=True)
    
    execute_query('''
        INSERT OR REPLACE INTO best_proxy (id, proxy_link, region, ping, domain, selected_date, times_selected)
        VALUES (1, ?, ?, ?, ?, ?, 
            (SELECT times_selected FROM proxy_cache WHERE proxy_link = ?)
        )
    ''', (
        proxy_data['link'],
        proxy_data['region'],
        proxy_data['ping'],
        proxy_data.get('domain', 'unknown'),
        datetime.now(),
        proxy_data['link']
    ), commit=True)

def save_check_stats(total, ru, eu, best_ping, best_region):
    execute_query('''
        INSERT INTO check_history (check_date, total_found, ru_found, eu_found, best_ping, best_region)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (datetime.now(), total, ru, eu, best_ping, best_region), commit=True)

SOURCES = [
    "https://raw.githubusercontent.com/SoliSpirit/mtproto/master/all_proxies.txt",
    "https://raw.githubusercontent.com/Grim1313/mtproto-for-telegram/refs/heads/master/all_proxies.txt",
    "https://raw.githubusercontent.com/ALIILAPRO/MTProtoProxy/main/mtproto.txt",
    "https://raw.githubusercontent.com/yemixzy/proxy-projects/main/proxies/mtproto.txt",
]

TIMEOUT = 2.0
MAX_WORKERS = 150

RU_DOMAINS = [
    '.ru', 'yandex', 'vk.com', 'mail.ru', 'ok.ru', 'dzen', 'rutube',
    'sber', 'tinkoff', 'vtb', 'gosuslugi', 'nalog', 'mos.ru',
    'ozon', 'wildberries', 'avito', 'kinopoisk', 'mts', 'beeline'
]

BLOCKED = ['instagram', 'facebook', 'twitter', 'bbc', 'meduza', 'linkedin', 'torproject']

def get_proxies_from_text(text: str):
    proxies = set()
    
    tg_pattern = re.compile(
        r'tg://proxy\?server=([^&\s]+)&port=(\d+)&secret=([A-Za-z0-9_=-]+)',
        re.IGNORECASE
    )
    for h, p, s in tg_pattern.findall(text):
        proxies.add((h, int(p), s))

    tme_pattern = re.compile(
        r't\.me/proxy\?server=([^&\s]+)&port=(\d+)&secret=([A-Za-z0-9_=-]+)',
        re.IGNORECASE
    )
    for h, p, s in tme_pattern.findall(text):
        proxies.add((h, int(p), s))

    simple_pattern = re.compile(
        r'([a-zA-Z0-9\.-]+):(\d+):([A-Fa-f0-9]{16,})'
    )
    for h, p, s in simple_pattern.findall(text):
        proxies.add((h, int(p), s))

    txt = text.strip()
    if txt.startswith('[') or txt.startswith('{'):
        try:
            data = json.loads(txt)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        host = item.get('host') or item.get('server')
                        port = item.get('port')
                        secret = item.get('secret')
                        if host and port and secret:
                            proxies.add((host, int(port), str(secret)))
        except Exception:
            pass

    return proxies

def decode_domain(secret: str):
    if not secret.startswith('ee'):
        return None
    try:
        chars = []
        for i in range(2, len(secret), 2):
            val = int(secret[i:i + 2], 16)
            if val == 0:
                break
            chars.append(chr(val))
        return "".join(chars).lower()
    except Exception:
        return None

def check_proxy(p):
    host, port, secret = p
    domain = decode_domain(secret)

    if len(secret) < 16:
        return None
    if domain:
        for b in BLOCKED:
            if b in domain:
                return None

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        start = time.time()
        s.connect((host, port))
        ping = time.time() - start
        s.close()
    except Exception:
        return None

    region = 'eu'
    if domain:
        for r in RU_DOMAINS:
            if r in domain:
                region = 'ru'
                break

    proxy_data = {
        'host': host,
        'port': port,
        'secret': secret,
        'link': f"tg://proxy?server={host}&port={port}&secret={secret}",
        'ping': ping,
        'region': region,
        'domain': domain or 'unknown'
    }
    
    save_proxy_to_cache(proxy_data)
    
    return proxy_data

def collect_proxies():
    print(f"\n [{datetime.now().strftime('%H:%M:%S')}] Начинаю сбор прокси...")
    all_raw = set()

    for url in SOURCES:
        name = url.split('/')[3]
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                print(f"  ✗ {name} -> HTTP {r.status_code}")
                continue
            extracted = get_proxies_from_text(r.text)
            all_raw.update(extracted)
            print(f"  ✓ {name} -> {len(extracted)}")
        except Exception as e:
            print(f"  ✗ {name} -> {e}")

    print(f" Проверяю {len(all_raw)} прокси...")
    valid = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as exc:
        futures = {exc.submit(check_proxy, p): p for p in all_raw}
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                valid.append(res)

    ru = sorted([x for x in valid if x['region'] == 'ru'], key=lambda x: x['ping'])
    eu = sorted([x for x in valid if x['region'] == 'eu'], key=lambda x: x['ping'])
    
    print(f" Найдено: RU={len(ru)}, EU={len(eu)}, Всего={len(valid)}")
    
    return ru, eu, valid

def get_best_proxy():
    ru, eu, valid = collect_proxies()
    
    best_ping = ru[0]['ping'] if ru else (eu[0]['ping'] if eu else None)
    best_region = 'ru' if ru else ('eu' if eu else None)
    save_check_stats(len(valid), len(ru), len(eu), best_ping, best_region)
    
    new_best = None
    new_region = None
    new_ping = None
    new_domain = None
    new_link = None
    
    if ru:
        new_best = ru[0]
        new_link = ru[0]['link']
        new_region = '🇷🇺 RU'
        new_ping = ru[0]['ping']
        new_domain = ru[0].get('domain', 'unknown')
    elif eu:
        new_best = eu[0]
        new_link = eu[0]['link']
        new_region = '🇪🇺 EU'
        new_ping = eu[0]['ping']
        new_domain = eu[0].get('domain', 'unknown')
    else:
        return None, None, None
    
    cached = get_cached_best_proxy()
    
    if cached:
        time_diff = datetime.now() - datetime.fromisoformat(cached['selected_date'].replace(' ', 'T'))
        hours_passed = time_diff.total_seconds() / 3600
        
        print(f" Кэшированный прокси выбран: {hours_passed:.1f} часов назад")
        print(f" Сравнение: старый пинг {cached['ping']*1000:.0f}ms vs новый {new_ping*1000:.0f}ms")
        
        if hours_passed > 24:
            print(f" Кэш устарел ({hours_passed:.1f} часов > 24), обновляю...")
            
            print(f" Пингую старый прокси: {cached['proxy_link'][:50]}...")
            old_proxy_data = None
            
            match = re.search(r'server=([^&]+)&port=(\d+)&secret=([^&\s]+)', cached['proxy_link'])
            if match:
                host, port, secret = match.groups()
                old_proxy_data = check_proxy((host, int(port), secret))
            
            if old_proxy_data:
                old_ping = old_proxy_data['ping']
                print(f"📡 Новый пинг старого прокси: {old_ping*1000:.0f}ms")
                
                if old_ping <= new_ping:
                    print(f" Старый прокси все еще лучше, обновляю кэш с новым пингом")
                    update_best_proxy({
                        'link': cached['proxy_link'],
                        'region': cached['region'],
                        'ping': old_ping,
                        'domain': cached['domain']
                    })
                    return cached['proxy_link'], cached['region'], old_ping
                else:
                    print(f" Новый прокси лучше, обновляю кэш")
                    update_best_proxy({
                        'link': new_link,
                        'region': new_region,
                        'ping': new_ping,
                        'domain': new_domain
                    })
                    return new_link, new_region, new_ping
            else:
                print(f" Старый прокси не отвечает, использую новый")
                update_best_proxy({
                    'link': new_link,
                    'region': new_region,
                    'ping': new_ping,
                    'domain': new_domain
                })
                return new_link, new_region, new_ping
        else:
            print(f" Кэш свежий ({hours_passed:.1f} часов), сравниваю старый пинг с новым")
            
            if cached['ping'] <= new_ping:
                print(f" Старый прокси лучше (по данным кэша), использую его")
                return cached['proxy_link'], cached['region'], cached['ping']
            else:
                print(f" Новый прокси лучше, обновляю кэш")
                update_best_proxy({
                    'link': new_link,
                    'region': new_region,
                    'ping': new_ping,
                    'domain': new_domain
                })
                return new_link, new_region, new_ping
    else:
        print(f" Первый лучший прокси, сохраняю в кэш")
        update_best_proxy({
            'link': new_link,
            'region': new_region,
            'ping': new_ping,
            'domain': new_domain
        })
        return new_link, new_region, new_ping

def send_telegram_message(chat_id, text, proxy_link=None, keyboard=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    if keyboard:
        data["reply_markup"] = keyboard
    elif proxy_link:
        keyboard = {
            "inline_keyboard": [[
                {
                    "text": " ПОДКЛЮЧИТЬСЯ",
                    "url": proxy_link
                }
            ]]
        }
        data["reply_markup"] = keyboard
    
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f" Ошибка отправки: {e}")
        return False

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text(" У вас нет прав администратора")
        return
    
    pending = get_pending_requests()
    allowed = get_allowed_users()
    blocked = get_blocked_users()
    
    text = "<b> ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
    
    text += f"<b> ОЖИДАЮТ ЗАПРОСОВ: {len(pending)}</b>\n"
    if pending:
        for req in pending[:3]:
            text += f"• {req['first_name']} (@{req['username']}) - {req['request_date'][:16]}\n"
    else:
        text += "• Нет ожидающих запросов\n"
    
    text += f"\n<b> РАЗРЕШЕНО: {len(allowed)}</b>\n"
    text += f"<b> ЗАБЛОКИРОВАНО: {len(blocked)}</b>\n"
    
    keyboard = [
        [InlineKeyboardButton(" СПИСОК ЗАПРОСОВ", callback_data="admin_list_requests")],
        [InlineKeyboardButton(" РАЗРЕШЕННЫЕ", callback_data="admin_list_allowed")],
        [InlineKeyboardButton(" ЗАБЛОКИРОВАННЫЕ", callback_data="admin_list_blocked")],
        [InlineKeyboardButton(" СТАТИСТИКА", callback_data="admin_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "no_username"
    first_name = user.first_name
    
    add_user(user_id, username, first_name)
    
    if is_blocked(user_id):
        return
    
    if is_allowed(user_id):
        interval = get_user_interval(user_id)
        interval_text = f"каждые {interval} минут" if interval > 0 else "выключена"
        
        await update.message.reply_text(
            f"<b> Добро пожаловать, {first_name}!</b>\n\n"
            f" У вас есть доступ к боту\n"
            f" Ваш интервал: {interval_text}\n\n"
            f"Команды:\n"
            f"/proxy - получить лучший прокси сейчас\n"
            f"/settings - настройки интервала\n"
            f"/stats - статистика\n"
            f"/cached - информация о кэше",
            parse_mode='HTML'
        )
    else:
        if has_pending_request(user_id):
            await update.message.reply_text(
                " <b>Ваш запрос уже рассматривается</b>\n\n"
                "Вы получите уведомление, когда администратор одобрит доступ.",
                parse_mode='HTML'
            )
        else:
            create_access_request(user_id, username, first_name)
            
            keyboard = {
                "inline_keyboard": [[
                    {"text": " РАЗРЕШИТЬ", "callback_data": f"approve_{user_id}"},
                    {"text": " ОТКЛОНИТЬ", "callback_data": f"reject_{user_id}"}
                ]]
            }
            
            admin_text = (
                f"<b> НОВЫЙ ЗАПРОС ДОСТУПА</b>\n\n"
                f"<b>Пользователь:</b> {first_name}\n"
                f"<b>Username:</b> @{username}\n"
                f"<b>ID:</b> <code>{user_id}</code>\n"
                f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}"
            )
            
            send_telegram_message(ADMIN_ID, admin_text, keyboard=keyboard)
            
            await update.message.reply_text(
                " <b>Запрос отправлен администратору</b>\n\n"
                "Вы получите уведомление, когда доступ будет одобрен.",
                parse_mode='HTML'
            )

async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_allowed(user_id):
        return
    
    msg = await update.message.reply_text(" Ищу лучший прокси... Подождите немного...")
    
    proxy_link, region, ping = get_best_proxy()
    
    if proxy_link:
        current_time = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
        
        cached = get_cached_best_proxy()
        cache_info = ""
        if cached:
            cache_info = f"\n В кэше с: {cached['times_selected']} использований"
        
        message = (
            f"<b> ЛУЧШИЙ ПРОКСИ СЕЙЧАС</b>\n\n"
            f"<code>{proxy_link}</code>\n\n"
            f"<b> Статус:</b>  Рабочий\n"
            f"<b> Проверен:</b> {current_time}\n"
            f"<b> Регион:</b> {region}\n"
            f"<b> Пинг:</b> {ping*1000:.0f}ms{cache_info}"
        )
        
        keyboard = [[InlineKeyboardButton(" ПОДКЛЮЧИТЬСЯ", url=proxy_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await msg.edit_text(
            message,
            parse_mode='HTML',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    else:
        await msg.edit_text(" Не найдено рабочих прокси. Попробуй позже.")

async def cached_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_allowed(user_id):
        return
    
    cached = get_cached_best_proxy()
    
    if cached:
        message = (
            f"<b> КЭШИРОВАННЫЙ ПРОКСИ</b>\n\n"
            f"<code>{cached['proxy_link']}</code>\n\n"
            f"<b> Регион:</b> {cached['region']}\n"
            f"<b> Пинг:</b> {cached['ping']*1000:.0f}ms\n"
            f"<b> Домен:</b> {cached['domain']}\n"
            f"<b> Использован:</b> {cached['times_selected']} раз\n"
            f"<b> Выбран:</b> {cached['selected_date']}"
        )
        
        keyboard = [[InlineKeyboardButton(" ПОДКЛЮЧИТЬСЯ", url=cached['proxy_link'])]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            message,
            parse_mode='HTML',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(" Кэш пуст")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_allowed(user_id):
        return
    
    current_interval = get_user_interval(user_id)
    
    keyboard = [
        [
            InlineKeyboardButton("5 мин", callback_data="interval_5"),
            InlineKeyboardButton("10 мин", callback_data="interval_10"),
            InlineKeyboardButton("15 мин", callback_data="interval_15")
        ],
        [
            InlineKeyboardButton("30 мин", callback_data="interval_30"),
            InlineKeyboardButton("1 час", callback_data="interval_60"),
            InlineKeyboardButton("8 часов", callback_data="interval_480")
        ],
        [
            InlineKeyboardButton("24 часа", callback_data="interval_1440"),
            InlineKeyboardButton("Выключить", callback_data="interval_0")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    interval_text = f"каждые {current_interval} минут" if current_interval > 0 else "выключена"
    
    await update.message.reply_text(
        f"<b> Настройки интервала</b>\n\n"
        f"Текущий интервал: <b>{interval_text}</b>\n\n"
        f"Выбери новый интервал:",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_allowed(user_id):
        return
    
    users_count = get_allowed_users_count()
    blocked_count = get_blocked_users_count()
    pending_count = get_pending_requests_count()
    
    last_check = execute_query(
        "SELECT * FROM check_history ORDER BY check_date DESC LIMIT 1", 
        fetch_one=True
    )
    
    cached = get_cached_best_proxy()
    
    stats_text = f"<b> СТАТИСТИКА БОТА</b>\n\n"
    stats_text += f" Пользователей: {users_count}\n"
    stats_text += f" Заблокировано: {blocked_count}\n"
    stats_text += f" Ожидают: {pending_count}\n"
    stats_text += f" Источников: {len(SOURCES)}\n\n"
    
    if last_check:
        stats_text += f"<b>Последняя проверка:</b>\n"
        stats_text += f" {last_check['check_date'][:19]}\n"
        stats_text += f" Всего: {last_check['total_found']} (RU:{last_check['ru_found']}, EU:{last_check['eu_found']})\n"
    
    if cached:
        stats_text += f"\n<b> Лучший в кэше:</b>\n"
        stats_text += f" Пинг: {cached['ping']*1000:.0f}ms\n"
        stats_text += f" Регион: {cached['region']}\n"
        stats_text += f" Использован: {cached['times_selected']} раз"
    
    if user_id == ADMIN_ID:
        stats_text += f"\n\n Вы администратор. Используйте /admin для панели управления."
    
    await update.message.reply_text(stats_text, parse_mode='HTML')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if any(data.startswith(prefix) for prefix in ['approve_', 'reject_', 'block_', 'unblock_', 'admin_']):
        if user_id != ADMIN_ID:
            await query.edit_message_text("У вас нет прав администратора")
            return
        
        if data.startswith('approve_'):
            target_id = int(data.split('_')[1])
            
            user_info = execute_query(
                "SELECT username, first_name FROM users WHERE user_id = ?", 
                (target_id,), 
                fetch_one=True
            )
            
            if user_info:
                username = user_info['username']
                first_name = user_info['first_name']
            else:
                username = "unknown"
                first_name = "unknown"
            
            approve_user(target_id)
            
            await query.edit_message_text(
                f"Пользователь {first_name} (ID: {target_id}) получил доступ"
            )
            
            send_telegram_message(
                target_id,
                "<b>Доступ разрешен!</b>\n\n"
                "Теперь вы можете пользоваться ботом.\n"
                "Отправьте /start для начала работы."
            )
        
        elif data.startswith('reject_'):
            target_id = int(data.split('_')[1])
            
            user_info = execute_query(
                "SELECT username, first_name FROM users WHERE user_id = ?", 
                (target_id,), 
                fetch_one=True
            )
            
            first_name = user_info['first_name'] if user_info else "Неизвестно"
            
            reject_user(target_id)
            
            await query.edit_message_text(
                f"Запрос от {first_name} (ID: {target_id}) отклонен"
            )
        
        elif data.startswith('block_'):
            target_id = int(data.split('_')[1])
            
            if target_id == ADMIN_ID:
                await query.edit_message_text("Нельзя заблокировать администратора")
                return
            
            user_info = execute_query(
                "SELECT username, first_name FROM users WHERE user_id = ?", 
                (target_id,), 
                fetch_one=True
            )
            
            first_name = user_info['first_name'] if user_info else "Неизвестно"
            
            if block_user(target_id):
                await query.edit_message_text(
                    f" Пользователь {first_name} (ID: {target_id}) заблокирован"
                )
        
        elif data.startswith('unblock_'):
            target_id = int(data.split('_')[1])
            
            user_info = execute_query(
                "SELECT username, first_name FROM users WHERE user_id = ?", 
                (target_id,), 
                fetch_one=True
            )
            
            first_name = user_info['first_name'] if user_info else "Неизвестно"
            
            unblock_user(target_id)
            
            await query.edit_message_text(
                f" Пользователь {first_name} (ID: {target_id}) разблокирован"
            )
        
        elif data == "admin_list_requests":
            pending = get_pending_requests()
            
            if not pending:
                await query.edit_message_text(" Нет ожидающих запросов")
                return
            
            text = "<b> ОЖИДАЮТ ЗАПРОСЫ</b>\n\n"
            
            keyboard = []
            for req in pending[:10]:
                user_id = req['user_id']
                first_name = req['first_name'][:15]
                btn_text = f"{first_name} (@{req['username']})"
                keyboard.append([
                    InlineKeyboardButton(f" {btn_text}", callback_data=f"approve_{user_id}"),
                    InlineKeyboardButton(f"", callback_data=f"reject_{user_id}")
                ])
            
            if len(pending) > 10:
                text += f"Показаны первые 10 из {len(pending)} запросов\n\n"
            
            keyboard.append([InlineKeyboardButton(" НАЗАД", callback_data="admin_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        
        elif data == "admin_list_allowed":
            users = get_allowed_users()
            
            if not users:
                await query.edit_message_text(" Нет разрешенных пользователей")
                return
            
            text = "<b>👥 РАЗРЕШЕННЫЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            
            keyboard = []
            for user in users[:10]:
                target_id = user['user_id']
                first_name = user['first_name'][:15]
                interval = user['interval_minutes']
                interval_text = f"{interval}мин" if interval > 0 else "выкл"
                btn_text = f"{first_name} (@{user['username']}) [{interval_text}]"
                keyboard.append([
                    InlineKeyboardButton(f" {btn_text}", callback_data=f"block_{target_id}")
                ])
            
            if len(users) > 10:
                text += f"Показаны первые 10 из {len(users)} пользователей\n\n"
            
            keyboard.append([InlineKeyboardButton(" НАЗАД", callback_data="admin_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        
        elif data == "admin_list_blocked":
            users = get_blocked_users()
            
            if not users:
                await query.edit_message_text(" Нет заблокированных пользователей")
                return
            
            text = "<b> ЗАБЛОКИРОВАННЫЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            
            keyboard = []
            for user in users[:10]:
                target_id = user['user_id']
                first_name = user['first_name'][:15]
                btn_text = f"{first_name} (@{user['username']})"
                keyboard.append([
                    InlineKeyboardButton(f" {btn_text}", callback_data=f"unblock_{target_id}")
                ])
            
            if len(users) > 10:
                text += f"Показаны первые 10 из {len(users)} пользователей\n\n"
            
            keyboard.append([InlineKeyboardButton(" НАЗАД", callback_data="admin_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        
        elif data == "admin_stats":
            users_count = get_allowed_users_count()
            blocked_count = get_blocked_users_count()
            pending_count = get_pending_requests_count()
            
            checks = execute_query(
                "SELECT * FROM check_history ORDER BY check_date DESC LIMIT 5", 
                fetch_all=True
            ) or []
            
            top_proxies = execute_query('''
                SELECT * FROM proxy_cache 
                ORDER BY times_selected DESC, ping ASC 
                LIMIT 5
            ''', fetch_all=True) or []
            
            text = "<b> ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n"
            text += f" Разрешено: {users_count}\n"
            text += f" Заблокировано: {blocked_count}\n"
            text += f" В очереди: {pending_count}\n\n"
            
            if checks:
                text += "<b>Последние проверки:</b>\n"
                for check in checks[:3]:
                    text += f"• {check['check_date'][:16]} - {check['total_found']} прокси\n"
            
            if top_proxies:
                text += f"\n<b> ТОП-5 ПРОКСИ:</b>\n"
                for i, proxy in enumerate(top_proxies, 1):
                    text += f"{i}. Пинг: {proxy['ping']*1000:.0f}ms, использован: {proxy['times_selected']} раз\n"
            
            keyboard = [[InlineKeyboardButton("◀️ НАЗАД", callback_data="admin_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        
        elif data == "admin_back":
            pending = get_pending_requests()
            allowed = get_allowed_users()
            blocked = get_blocked_users()
            
            text = "<b> ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
            text += f" Ожидают: {len(pending)}\n"
            text += f" Разрешено: {len(allowed)}\n"
            text += f" Заблокировано: {len(blocked)}\n"
            
            keyboard = [
                [InlineKeyboardButton(" СПИСОК ЗАПРОСОВ", callback_data="admin_list_requests")],
                [InlineKeyboardButton(" РАЗРЕШЕННЫЕ", callback_data="admin_list_allowed")],
                [InlineKeyboardButton(" ЗАБЛОКИРОВАННЫЕ", callback_data="admin_list_blocked")],
                [InlineKeyboardButton(" СТАТИСТИКА", callback_data="admin_stats")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
    
    elif data.startswith('interval_'):
        if not is_allowed(user_id):
            await query.edit_message_text(" У вас нет доступа")
            return
        
        interval = int(data.split('_')[1])
        set_user_interval(user_id, interval)
        
        interval_text = f"каждые {interval} минут" if interval > 0 else "выключена"
        
        await query.edit_message_text(
            f" Интервал изменен\n\n"
            f"Теперь авторассылка будет {interval_text}"
        )

def send_proxy_to_user(user_id, proxy_link, region, ping):
    if is_blocked(user_id):
        return False
    
    message = (
        f"<b> АВТОМАТИЧЕСКИЙ ПРОКСИ</b>\n\n"
        f"<code>{proxy_link}</code>\n\n"
        f"<b> Регион:</b> {region}\n"
        f"<b> Пинг:</b> {ping*1000:.0f}ms\n"
        f"<b> Время:</b> {datetime.now().strftime('%H:%M')}\n\n"
        f"<b>Нажми кнопку для подключения</b>"
    )
    return send_telegram_message(user_id, message, proxy_link)

def scheduled_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Запуск автоматической рассылки...")
    
    proxy_link, region, ping = get_best_proxy()
    
    if not proxy_link:
        print(" Нет прокси для отправки")
        return
    
    users = get_all_allowed_users_with_intervals()
    sent_count = 0
    
    for user in users:
        user_id = user['user_id']
        interval = user['interval_minutes']
        
        if interval > 0:
            if send_proxy_to_user(user_id, proxy_link, region, ping):
                sent_count += 1
            time.sleep(0.1)
    
    print(f" Отправлено {sent_count} пользователям")
async def set_bot_commands(app):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("proxy", "Получить лучший прокси"),
        BotCommand("settings", "Настройки интервала"),
        BotCommand("stats", "Статистика"),
        BotCommand("cached", "Информация о кэше"),
    ]
    await app.bot.set_my_commands(commands)
    print("Команды бота установлены (без админки)")
    
def run_telegram_bot():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Устанавливаем команды при запуске
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(set_bot_commands(app))
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("proxy", proxy_command))
    app.add_handler(CommandHandler("cached", cached_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admin", admin_command))  # админка остается, но в меню не видна
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Telegram бот запущен и ждет команды...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
def setup_schedules():
    schedule.every(15).minutes.do(scheduled_job)
    print(" Расписания настроены")

def run_scheduler():
    print(" Планировщик запущен...")
    
    
    setup_schedules()
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def main():
    init_database()
    
    print("=" * 50)
    print(" ЗАПУСК ПРОКСИ БОТА С ПОЛНОЙ АДМИНКОЙ")
    print("=" * 50)
    print(f" Админ ID: {ADMIN_ID}")
    print(f" Пользователей: {get_allowed_users_count()}")
    print(f" Заблокировано: {get_blocked_users_count()}")
    print(f" Ожидают: {get_pending_requests_count()}")
    print("=" * 50)
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    run_telegram_bot()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n Бот остановлен пользователем")
    except Exception as e:

        print(f"\n Критическая ошибка: {e}")



