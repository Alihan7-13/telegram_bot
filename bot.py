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

# ====== РОАДМАП ТЕГИ (ПО ТВОЕМУ СПИСКУ) ======
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

# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======

def get_roadmap_tags(rating):
    """Возвращает список тегов для текущего уровня рейтинга из роадмапа"""
    for level in ROADMAP:
        if level["min"] <= rating <= level["max"]:
            return level["tags"]
    return ["implementation", "greedy"] # дефолт

def get_roadmap_problem(rating):
    """Ищет случайную задачу в общем Problemset CF по тегам роадмапа"""
    tags = get_roadmap_tags(rating)
    tag_str = ";".join(tags)
    try:
        url = f"https://codeforces.com/api/problemset.problems?tags={random.choice(tags)}"
        resp = requests.get(url, timeout=10).json()
        if resp['status'] == 'OK':
            # Фильтруем задачи по рейтингу (твой + 100-200)
            valid_problems = [
                p for p in resp['result']['problems'] 
                if p.get('rating') and rating <= p['rating'] <= rating + 200
            ]
            if valid_problems:
                return random.choice(valid_problems)
    except: return None
    return None

# ====== ЛОГИКА МОНИТОРИНГА (УВЕДОМЛЕНИЯ) ======
def monitoring_worker():
    while True:
        try:
            conn = get_db_connection()
            distinct_targets = conn.execute("SELECT DISTINCT target_handle FROM following").fetchall()
            for row in distinct_targets:
                target = row['target_handle']
                time.sleep(2)
                resp = requests.get(f"https://codeforces.com/api/user.status?handle={target}&from=1&count=5", timeout=10).json()
                if resp.get('status') == 'OK' and resp['result']:
                    last_sub = resp['result'][0]
                    if last_sub.get('verdict') == 'OK':
                        sub_id = f"{last_sub['contestId']}{last_sub['problem']['index']}"
                        db_last = conn.execute("SELECT last_id FROM last_solved WHERE handle = ?", (target,)).fetchone()
                        if not db_last or db_last['last_id'] != sub_id:
                            subscribers = conn.execute("SELECT user_id FROM following WHERE target_handle = ?", (target,)).fetchall()
                            for s in subscribers:
                                try:
                                    bot.send_message(s['user_id'], f"🔔 <b>{target}</b> решил: {last_sub['problem']['name']}", parse_mode="HTML")
                                except: pass
                            conn.execute("INSERT OR REPLACE INTO last_solved VALUES (?, ?)", (target, sub_id))
                            conn.commit()
            conn.close()
        except: pass
        time.sleep(60)

# ====== ЛОГИКА ОБНОВЛЕНИЯ ЗАДАЧ (UPSOLVING) ======
def update_user_tasks(user_id, handle):
    try:
        user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if user_info['status'] != 'OK': return False
        rating = user_info['result'][0].get('rating', 800)
        
        # Обновляем рейтинг в базе
        conn = get_db_connection()
        conn.execute("UPDATE users SET rating = ? WHERE user_id = ?", (rating, user_id))
        
        # Получаем контесты, в которых юзер реально участвовал
        contests_resp = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=5).json()
        if contests_resp['status'] != 'OK': return False
        
        last_5_participated = [c['contestId'] for c in contests_resp['result'][-5:]]
        
        # Получаем решенные задачи
        status = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=5).json()
        solved = {f"{s['problem']['contestId']}{s['problem']['index']}" for s in status['result'] if s.get('verdict') == 'OK'}

        for c_id in last_5_participated:
            time.sleep(0.5)
            st = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1").json()
            if st['status'] == 'OK':
                for p in st['result']['problems']:
                    p_id = f"{p['contestId']}{p['index']}"
                    p_r = p.get('rating', 0)
                    # Фильтр: не решена и подходит по сложности
                    if p_id not in solved and p_r and (rating - 100 <= p_r <= rating + 400):
                        conn.execute("INSERT OR IGNORE INTO missed_tasks VALUES (?,?,?,?,?,?)",
                                     (user_id, p['contestId'], p['index'], p['name'], p_r, ",".join(p.get('tags', []))))
        conn.commit()
        conn.close()
        return True
    except: return False

# ====== КОМАНДЫ ======

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    
    # 1. Пробуем взять задачу из "долгов" (нерешенные с контестов)
    task = conn.execute("SELECT * FROM missed_tasks WHERE user_id = ? ORDER BY rating ASC LIMIT 1", (user_id,)).fetchone()
    
    if task:
        # Если есть долг — отдаем его и удаляем
        conn.execute("DELETE FROM missed_tasks WHERE user_id = ? AND contest_id = ? AND index_str = ?", 
                     (user_id, task['contest_id'], task['index_str']))
        conn.commit()
        conn.close()
        
        link = f"https://codeforces.com/contest/{task['contest_id']}/problem/{task['index_str']}"
        return bot.reply_to(message, f"🚩 <b>Дорешивание (из твоего контеста):</b>\n{task['name']} ({task['rating']})\nТеги: {task['tags']}\n{link}", parse_mode="HTML")
    
    # 2. Если долгов нет — работаем по Роадмапу
    user = conn.execute("SELECT rating FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    current_rating = user['rating'] if user else 800
    bot.send_message(message.chat.id, "💡 Все хвосты закрыты! Ищу задачу по роадмапу для твоего уровня...")
    
    roadmap_p = get_roadmap_problem(current_rating)
    if roadmap_p:
        link = f"https://codeforces.com/problemset/problem/{roadmap_p['contestId']}/{roadmap_p['index']}"
        bot.reply_to(message, f"🚀 <b>Новая тема по Роадмапу:</b>\n{roadmap_p['name']} ({roadmap_p.get('rating')})\nТеги: {', '.join(roadmap_p['tags'])}\n{link}", parse_mode="HTML")
    else:
        bot.reply_to(message, "Не смог найти подходящую задачу в Problemset. Попробуй позже.")

# Остальные команды (add_cf, update, follow, status) остаются такими же...
# [Вставь сюда команды из предыдущего сообщения]

@bot.message_handler(commands=['add_cf'])
def cmd_add_cf(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Укажи ник!")
    handle = parts[1]
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO users (user_id, handle) VALUES (?, ?)", (str(message.from_user.id), handle))
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ Ник {handle} сохранен.")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user: return bot.reply_to(message, "Сначала /add_cf")
    bot.reply_to(message, "⏳ Анализирую твои контесты...")
    if update_user_tasks(user_id, user['handle']):
        bot.reply_to(message, "✅ Список задач для дорешивания обновлен!")
    else: bot.reply_to(message, "❌ Ошибка. Возможно, ты не участвовал в контестах.")

@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    Thread(target=monitoring_worker, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
