import os
import time
import sqlite3
import requests
import telebot
import random
from threading import Thread
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
bot = telebot.TeleBot(TOKEN)
app = Flask('')

# Роадмап теги
ROADMAP = [
    {"min": 0, "max": 999, "tags": ["brute force", "sortings", "strings", "number theory", "implementation"]},
    {"min": 1000, "max": 1199, "tags": ["binary search", "two pointers", "bitmasks", "math", "sortings"]},
    {"min": 1200, "max": 1399, "tags": ["dp", "combinatorics", "greedy", "math", "trees"]},
    {"min": 1400, "max": 1599, "tags": ["graphs", "shortest paths", "dsu", "constructive algorithms", "data structures"]},
    {"min": 1600, "max": 1899, "tags": ["probabilities", "games", "string algorithms", "segment trees", "trees"]}
]

def get_db_connection():
    conn = sqlite3.connect('cf_coach.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, handle TEXT, rating INTEGER DEFAULT 800)')
    c.execute('CREATE TABLE IF NOT EXISTS following (user_id TEXT, target_handle TEXT, UNIQUE(user_id, target_handle))')
    c.execute('''CREATE TABLE IF NOT EXISTS missed_tasks 
                 (user_id TEXT, contest_id INTEGER, index_str TEXT, name TEXT, rating INTEGER, tags TEXT, 
                  UNIQUE(user_id, contest_id, index_str))''')
    c.execute('CREATE TABLE IF NOT EXISTS last_solved (handle TEXT PRIMARY KEY, last_id TEXT)')
    conn.commit()
    conn.close()

init_db()

# ====== ЛОГИКА РОАДМАПА ======

def get_roadmap_problem(rating):
    # Находим нужный уровень
    current_tags = ["implementation", "greedy"] # дефолт
    for level in ROADMAP:
        if level["min"] <= rating <= level["max"]:
            current_tags = level["tags"]
            break
            
    try:
        tag = random.choice(current_tags)
        url = f"https://codeforces.com/api/problemset.problems?tags={tag}"
        resp = requests.get(url, timeout=10).json()
        if resp['status'] == 'OK':
            # Фильтруем задачи по рейтингу (твой рейтинг до +200)
            valid = [p for p in resp['result']['problems'] 
                     if p.get('rating') and (rating <= p['rating'] <= rating + 200)]
            return random.choice(valid) if valid else None
    except: return None

# ====== ОБНОВЛЕНИЕ ЗАДАЧ ======

def update_user_tasks(user_id, handle):
    try:
        # 1. Инфо и рейтинг
        u_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if u_info['status'] != 'OK': return "error_handle"
        
        rating = u_info['result'][0].get('rating', 800)
        conn = get_db_connection()
        conn.execute("UPDATE users SET rating = ? WHERE user_id = ?", (rating, user_id))
        
        # 2. Контесты, в которых участвовал
        c_resp = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=5).json()
        if c_resp['status'] != 'OK': return "error_api"
        
        participated_ids = [c['contestId'] for c in c_resp['result'][-5:]]
        if not participated_ids:
            conn.close()
            return "no_contests"

        # 3. Сабмиты
        s_resp = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=5).json()
        solved = {f"{s['problem']['contestId']}{s['problem']['index']}" for s in s_resp['result'] if s.get('verdict') == 'OK'}

        # 4. Сбор задач
        for c_id in participated_ids:
            time.sleep(0.5)
            # ИСПРАВЛЕНО: правильный домен .com
            st = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1").json()
            if st['status'] == 'OK':
                for p in st['result']['problems']:
                    p_id = f"{p['contestId']}{p['index']}"
                    p_r = p.get('rating', 0)
                    if p_id not in solved and p_r and (rating - 100 <= p_r <= rating + 400):
                        conn.execute("INSERT OR IGNORE INTO missed_tasks VALUES (?,?,?,?,?,?)",
                                     (user_id, p['contestId'], p['index'], p['name'], p_r, ",".join(p.get('tags', []))))
        conn.commit()
        conn.close()
        return "success"
    except Exception as e:
        print(f"Update error: {e}")
        return "error_fatal"

# ====== КОМАНДЫ ======

@bot.message_handler(commands=['status'])
def cmd_status(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    
    if not user:
        conn.close()
        return bot.reply_to(message, "⚠️ Ты еще не привязал ник. Используй /add_cf [ник]")

    follows = conn.execute("SELECT target_handle FROM following WHERE user_id = ?", (user_id,)).fetchall()
    tasks_count = conn.execute("SELECT COUNT(*) FROM missed_tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
    conn.close()

    text = f"👤 <b>Профиль:</b> {user['handle']}\n"
    text += f"📈 Рейтинг: {user['rating']}\n"
    text += f"📚 Задач в очереди (долги): <b>{tasks_count}</b>\n\n"
    text += "👥 <b>Подписки:</b>\n"
    if follows:
        for f in follows: text += f"• <code>{f['target_handle']}</code>\n"
    else: text += "Нет подписок."
    
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    
    # Сначала проверяем, есть ли пользователь в базе
    user = conn.execute("SELECT rating FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return bot.reply_to(message, "Сначала привяжи ник через /add_cf")

    # 1. Пытаемся взять из долгов
    task = conn.execute("SELECT * FROM missed_tasks WHERE user_id = ? ORDER BY RANDOM() LIMIT 1", (user_id,)).fetchone()
    
    if task:
        conn.execute("DELETE FROM missed_tasks WHERE id = (SELECT id FROM missed_tasks WHERE user_id = ? LIMIT 1)", (user_id,)) # Упрощенно
        # Лучше удалять по ключу:
        conn.execute("DELETE FROM missed_tasks WHERE user_id = ? AND contest_id = ? AND index_str = ?", 
                     (user_id, task['contest_id'], task['index_str']))
        conn.commit()
        conn.close()
        link = f"https://codeforces.com/contest/{task['contest_id']}/problem/{task['index_str']}"
        return bot.reply_to(message, f"🚩 <b>Дорешивание контеста:</b>\n{task['name']} ({task['rating']})\n{link}", parse_mode="HTML")

    # 2. Если долгов нет — Роадмап
    conn.close()
    bot.send_message(message.chat.id, "🔍 Долгов нет! Ищу задачу по роадмапу...")
    rp = get_roadmap_problem(user['rating'])
    if rp:
        link = f"https://codeforces.com/problemset/problem/{rp['contestId']}/{rp['index']}"
        bot.reply_to(message, f"🚀 <b>Роадмап:</b>\n{rp['name']} ({rp.get('rating')})\n{link}", parse_mode="HTML")
    else:
        bot.reply_to(message, "Не удалось найти задачу. Попробуй /update.")

@bot.message_handler(commands=['add_cf'])
def cmd_add_cf(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Пиши: /add_cf [ник]")
    handle = parts[1]
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO users (user_id, handle, rating) VALUES (?, ?, 800)", (str(message.from_user.id), handle))
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ Ник {handle} привязан. Нажми /update.")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user: return bot.reply_to(message, "Сначала /add_cf")
    
    msg = bot.reply_to(message, "⏳ Синхронизация с CF...")
    res = update_user_tasks(user_id, user['handle'])
    
    if res == "success":
        bot.edit_message_text("✅ Готово! Долги собраны.", chat_id=msg.chat.id, message_id=msg.message_id)
    elif res == "no_contests":
        bot.edit_message_text("ℹ️ Ты еще не участвовал в официальных контестах. Буду давать задачи по роадмапу.", chat_id=msg.chat.id, message_id=msg.message_id)
    else:
        bot.edit_message_text("❌ Ошибка API или ника.", chat_id=msg.chat.id, message_id=msg.message_id)

@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
