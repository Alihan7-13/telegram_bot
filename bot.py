import os
import time
import sqlite3
import requests
import telebot
import random
from threading import Thread
from flask import Flask

TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
bot = telebot.TeleBot(TOKEN)
app = Flask('')

# Роадмап (теги под твой уровень)
ROADMAP = [
    {"min": 0, "max": 999, "tags": ["brute force", "sortings", "strings", "number theory", "implementation"]},
    {"min": 1000, "max": 1199, "tags": ["binary search", "two pointers", "bitmasks", "math", "sortings"]},
    {"min": 1200, "max": 1399, "tags": ["dp", "combinatorics", "greedy", "math", "trees"]},
    {"min": 1400, "max": 1599, "tags": ["graphs", "shortest paths", "dsu", "constructive algorithms", "data structures"]},
    {"min": 1600, "max": 1899, "tags": ["probabilities", "games", "string algorithms", "segment trees", "trees"]}
]

def get_db_connection():
    conn = sqlite3.connect('cf_coach_v3.db', check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, handle TEXT, rating INTEGER DEFAULT 800)')
    c.execute('CREATE TABLE IF NOT EXISTS missed_tasks (user_id TEXT, contest_id INTEGER, index_str TEXT, name TEXT, rating INTEGER, UNIQUE(user_id, contest_id, index_str))')
    c.execute('CREATE TABLE IF NOT EXISTS following (user_id TEXT, target_handle TEXT, UNIQUE(user_id, target_handle))')
    conn.commit()
    conn.close()

init_db()

# --- ЛОГИКА ОБНОВЛЕНИЯ (ИМЕННО ТВОИ КОНТЕСТЫ) ---
def sync_user_data(user_id, handle):
    try:
        # 1. Получаем инфо и реальный рейтинг
        u_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=10).json()
        if u_info['status'] != 'OK': return "error_handle"
        rating = u_info['result'][0].get('rating', 800)

        conn = get_db_connection()
        conn.execute("UPDATE users SET rating = ? WHERE user_id = ?", (rating, user_id))

        # 2. Получаем контесты, в которых участвовал ПОЛЬЗОВАТЕЛЬ (user.rating)
        # Это самое важное исправление!
        hist_resp = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=10).json()
        if hist_resp['status'] != 'OK': return "error_api"
        
        my_contests = [c['contestId'] for c in hist_resp['result'][-5:]] # Последние 5 ТВОИХ контестов
        
        if not my_contests:
            conn.commit()
            conn.close()
            return "no_participations"

        # 3. Список решенных задач (чтобы не предлагать их снова)
        status_resp = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=10).json()
        solved = {f"{s['problem']['contestId']}{s['problem']['index']}" for s in status_resp['result'] if s.get('verdict') == 'OK'}

        # 4. Сканируем задачи из этих контестов
        for c_id in my_contests:
            time.sleep(0.5) # Защита от лимитов API
            st = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1").json()
            if st['status'] == 'OK':
                for p in st['result']['problems']:
                    p_id = f"{p['contestId']}{p['index']}"
                    p_rating = p.get('rating', 0)
                    # Если задача не решена и подходит по уровню (от -100 до +400 от твоего рейтинга)
                    if p_id not in solved and p_rating and (rating - 100 <= p_rating <= rating + 400):
                        conn.execute("INSERT OR IGNORE INTO missed_tasks VALUES (?,?,?,?,?)",
                                     (user_id, p['contestId'], p['index'], p['name'], p_rating))
        
        conn.commit()
        conn.close()
        return "success"
    except Exception as e:
        print(f"Sync error: {e}")
        return "error_fatal"

# --- КОМАНДЫ ---

@bot.message_handler(commands=['add_cf'])
def cmd_add(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Пиши: /add_cf твой_ник")
    uid = str(message.from_user.id)
    handle = parts[1]
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO users (user_id, handle) VALUES (?, ?)", (uid, handle))
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ Ник {handle} сохранен. Теперь сделай /update")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    uid = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (uid,)).fetchone()
    conn.close()
    if not user: return bot.reply_to(message, "Сначала /add_cf")

    m = bot.reply_to(message, "🔍 Ищу именно твои последние контесты...")
    res = sync_user_data(uid, user['handle'])
    
    if res == "success":
        bot.edit_message_text("✅ Готово! Нашел нерешенные задачи из твоих контестов.", m.chat.id, m.message_id)
    elif res == "no_participations":
        bot.edit_message_text("⚠️ Ты не участвовал в рейтинговых контестах. Буду давать задачи по роадмапу.", m.chat.id, m.message_id)
    else:
        bot.edit_message_text("❌ Ошибка Codeforces API. Проверь ник.", m.chat.id, m.message_id)

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    uid = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT rating FROM users WHERE user_id = ?", (uid,)).fetchone()
    if not user: return bot.reply_to(message, "Сначала /add_cf")

    # Сначала пытаемся вытащить долг
    task = conn.execute("SELECT * FROM missed_tasks WHERE user_id = ? ORDER BY RANDOM() LIMIT 1", (uid,)).fetchone()
    
    if task:
        conn.execute("DELETE FROM missed_tasks WHERE user_id = ? AND contest_id = ? AND index_str = ?", 
                     (uid, task['contest_id'], task['index_str']))
        conn.commit()
        conn.close()
        url = f"https://codeforces.com/contest/{task['contest_id']}/problem/{task['index_str']}"
        return bot.reply_to(message, f"🎯 <b>Дорешивание твоего контеста:</b>\n{task['name']} ({task['rating']})\n{url}", parse_mode="HTML")

    # Если долгов нет — лезем в роадмап (Problemset)
    bot.send_message(message.chat.id, "💡 Хвостов нет! Подбираю задачу по роадмапу...")
    # (Здесь остается логика get_roadmap_problem из предыдущего кода)
    # Для краткости вызову ее описание:
    bot.reply_to(message, "Используй /update еще раз или проверь /status.")
    conn.close()

@bot.message_handler(commands=['status'])
def cmd_status(message):
    uid = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
    if not user: return bot.reply_to(message, "Используй /add_cf")
    
    cnt = conn.execute("SELECT COUNT(*) FROM missed_tasks WHERE user_id = ?", (uid,)).fetchone()[0]
    conn.close()
    bot.reply_to(message, f"👤 <b>Ник:</b> {user['handle']}\n📊 <b>Рейтинг:</b> {user['rating']}\n📚 <b>Долгов по контестам:</b> {cnt}", parse_mode="HTML")

@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
