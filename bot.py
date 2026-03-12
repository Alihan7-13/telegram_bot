import os
import time
import requests
import telebot
import random
import sqlite3
from threading import Thread, Lock
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask('')

DB_NAME = 'bot_data.db'
db_lock = Lock()

# Кэш для задач и мониторинга
CF_CACHE = {"timestamp": 0, "problems": []}
LAST_SUB_ID = {}  # handle -> ID последней проверенной посылки

# --- Инициализация БД ---
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, my_cf TEXT, is_monitoring INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS friends
                     (user_id INTEGER, platform TEXT, handle TEXT,
                      UNIQUE(user_id, platform, handle))''')
        conn.commit()
        conn.close()

def execute_query(query, params=(), fetch=False, fetchall=False):
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        res = None
        if fetch: res = c.fetchone()
        elif fetchall: res = c.fetchall()
        else: conn.commit()
        conn.close()
        return res

# --- Фоновый мониторинг друзей (CF) ---
def monitor_friends():
    while True:
        try:
            # Получаем всех пользователей, у которых включен мониторинг
            users = execute_query("SELECT user_id FROM users WHERE is_monitoring = 1", fetchall=True)
            if not users:
                time.sleep(60)
                continue

            active_users = [u[0] for u in users]
            
            # Получаем все уникальные ники друзей на CF для активных пользователей
            friends_data = execute_query("SELECT DISTINCT handle FROM friends WHERE platform = 'cf' AND user_id IN ({})".format(','.join('?'*len(active_users))), active_users, fetchall=True)
            handles_to_check = [f[0] for f in friends_data]

            for handle in handles_to_check:
                try:
                    r = requests.get(f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=5", timeout=5).json()
                    if r['status'] == 'OK' and r['result']:
                        latest_sub = r['result'][0]
                        sub_id = latest_sub['id']
                        
                        # Если это новая посылка
                        if handle in LAST_SUB_ID and LAST_SUB_ID[handle] < sub_id:
                            # Уведомляем только об Accepted
                            if latest_sub.get('verdict') == 'OK':
                                p = latest_sub['problem']
                                msg = f"🟢 Твой друг <b>{handle}</b> только что решил задачу <b>{p['name']}</b> ({p.get('rating', 'N/A')})!"
                                
                                # Ищем, кому отправить
                                subs = execute_query("SELECT user_id FROM friends WHERE handle = ? AND platform = 'cf'", (handle,), fetchall=True)
                                for sub in subs:
                                    uid = sub[0]
                                    if uid in active_users:
                                        bot.send_message(uid, msg, parse_mode="HTML")
                        
                        # Обновляем последний ID (даже если WA, чтобы не спамить потом)
                        LAST_SUB_ID[handle] = sub_id
                except Exception as e:
                    print(f"Ошибка проверки {handle}: {e}")
                
                # Задержка, чтобы CF не забанил за частые запросы (HTTP 503)
                time.sleep(0.5)

        except Exception as e:
            print(f"Ошибка в главном цикле мониторинга: {e}")
        
        # Проверяем раз в минуту
        time.sleep(60)

# --- Логика Suggest ---
def get_problemset():
    if time.time() - CF_CACHE["timestamp"] > 3600:
        try:
            r = requests.get("https://codeforces.com/api/problemset.problems").json()
            if r['status'] == 'OK':
                CF_CACHE["problems"] = r['result']['problems']
                CF_CACHE["timestamp"] = time.time()
        except: pass
    return CF_CACHE["problems"]

def analyze_cf_weak_tag(handle, rating):
    try:
        if rating <= 999: target_tags, min_r, max_r = ["brute force", "sortings", "math"], 800, 1000
        elif rating <= 1199: target_tags, min_r, max_r = ["math", "binary search", "two pointers", "dp"], 1000, 1200
        elif rating <= 1399: target_tags, min_r, max_r = ["graphs", "dsu", "trees", "dp"], 1200, 1400
        elif rating <= 1599: target_tags, min_r, max_r = ["constructive algorithms", "shortest paths"], 1400, 1600
        else: target_tags, min_r, max_r = ["dp", "data structures", "greedy"], 1600, 2100

        r_contests = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=5).json()
        last_5_ids = [c['contestId'] for c in r_contests['result'][-5:]] if r_contests['status'] == 'OK' else []

        r_subs = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=5).json()
        solved_ids = set()
        failed_tags = []

        if r_subs['status'] == 'OK':
            for sub in r_subs['result']:
                p = sub['problem']
                p_id = f"{p.get('contestId')}{p.get('index')}"
                if sub['verdict'] == 'OK': solved_ids.add(p_id)
                elif p.get('contestId') in last_5_ids:
                    for tag in p.get('tags', []):
                        if tag in target_tags: failed_tags.append(tag)

        weak_tag = max(set(failed_tags), key=failed_tags.count) if failed_tags else random.choice(target_tags)
        return weak_tag, min_r, max_r, solved_ids
    except:
        return "greedy", 800, 1200, set()

# --- Команды ---

@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    execute_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    execute_query("UPDATE users SET is_monitoring = 1 WHERE user_id = ?", (uid,))
    bot.reply_to(message, "✅ Мониторинг включен. Я буду присылать уведомления, когда твои друзья решат задачи.\nДля остановки напиши /stop")

@bot.message_handler(commands=['stop'])
def cmd_stop(message):
    uid = message.from_user.id
    execute_query("UPDATE users SET is_monitoring = 0 WHERE user_id = ?", (uid,))
    bot.reply_to(message, "🛑 Мониторинг остановлен. Уведомления приходить не будут.")

@bot.message_handler(commands=['status'])
def cmd_status(message):
    uid = message.from_user.id
    user = execute_query("SELECT my_cf, is_monitoring FROM users WHERE user_id = ?", (uid,), fetch=True)
    if not user:
        execute_query("INSERT INTO users (user_id) VALUES (?)", (uid,))
        user = (None, 0)
    
    my_cf, is_monitoring = user
    friends_cf = [f[0] for f in execute_query("SELECT handle FROM friends WHERE user_id = ? AND platform = 'cf'", (uid,), fetchall=True)]
    friends_ac = [f[0] for f in execute_query("SELECT handle FROM friends WHERE user_id = ? AND platform = 'ac'", (uid,), fetchall=True)]
    
    status_text = (
        f"📡 <b>Мониторинг:</b> {'Включен 🟢' if is_monitoring else 'Выключен 🔴'}\n"
        f"👤 <b>Твой аккаунт (suggest):</b> <code>{my_cf if my_cf else 'Не задан'}</code>\n\n"
        "👥 <b>Друзья:</b>\n"
        f"🔹 <b>CF:</b> <code>{', '.join(friends_cf) if friends_cf else 'пусто'}</code>\n"
        f"🔹 <b>AC:</b> <code>{', '.join(friends_ac) if friends_ac else 'пусто'}</code>"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")

@bot.message_handler(commands=['register'])
def cmd_register(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Формат: /register [твой_ник_cf]")
    handle = parts[1]
    uid = message.from_user.id
    
    r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
    if r.get("status") != "OK": return bot.reply_to(message, "❌ Ник на CF не найден")

    execute_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    execute_query("UPDATE users SET my_cf = ? WHERE user_id = ?", (handle, uid))
    bot.reply_to(message, f"✅ Основной ник установлен: {handle}")

@bot.message_handler(commands=['add'])
def cmd_add(message):
    parts = message.text.split()
    if len(parts) < 3: return bot.reply_to(message, "Формат: /add [cf/ac] [ник]")
    plat, handle = parts[1].lower(), parts[2]
    uid = message.from_user.id

    if plat not in ['cf', 'ac']: return bot.reply_to(message, "Только cf или ac")
    
    if plat == "cf":
        r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if r.get("status") != "OK": return bot.reply_to(message, "❌ Ник не найден")

    try:
        execute_query("INSERT INTO friends (user_id, platform, handle) VALUES (?, ?, ?)", (uid, plat, handle))
        bot.reply_to(message, f"✅ {handle} добавлен в отслеживание {plat.upper()}")
    except sqlite3.IntegrityError:
        bot.reply_to(message, "Уже в списке.")

@bot.message_handler(commands=['remove'])
def cmd_remove(message):
    parts = message.text.split()
    if len(parts) < 3: return bot.reply_to(message, "Формат: /remove [cf/ac] [ник]")
    plat, handle = parts[1].lower(), parts[2]
    
    execute_query("DELETE FROM friends WHERE user_id = ? AND platform = ? AND handle = ?", (message.from_user.id, plat, handle))
    bot.reply_to(message, f"🗑 {handle} удален.")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    uid = message.from_user.id
    user = execute_query("SELECT my_cf FROM users WHERE user_id = ?", (uid,), fetch=True)
    if not user or not user[0]: return bot.reply_to(message, "Зарегистрируйся через /register [ник]")

    handle = user[0]
    bot.send_chat_action(message.chat.id, 'typing')

    u_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}").json()
    if u_info['status'] != 'OK': return bot.reply_to(message, "Ошибка API CF")
    
    rating = u_info['result'][0].get('rating', 800)
    weak_tag, min_r, max_r, solved_ids = analyze_cf_weak_tag(handle, rating)

    all_probs = get_problemset()
    pool = [p for p in all_probs if f"{p.get('contestId')}{p.get('index')}" not in solved_ids 
            and (min_r <= p.get('rating', 0) <= max_r) and weak_tag in p.get('tags', [])]

    if not pool: return bot.reply_to(message, "Подходящих задач не найдено.")

    p = random.choice(pool[:30])
    link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
    
    bot.reply_to(message, (
        f"🎯 <b>Suggest для {handle}</b>\n"
        f"Слабый тег по последним контестам: <code>#{weak_tag}</code>\n\n"
        f"Задача: <b>{p['name']}</b> ({p.get('rating')})\n"
        f"🔗 <a href='{link}'>Решить</a>"
    ), parse_mode="HTML")

@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    init_db()
    # Запуск фонового потока для мониторинга
    Thread(target=monitor_friends, daemon=True).start()
    # Запуск Flask сервера (для keep-alive)
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
    
    print("Бот запущен...")
    bot.infinity_polling()
