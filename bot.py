import os
import time
import sqlite3
import requests
import telebot
from threading import Thread
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН")
bot = telebot.TeleBot(TOKEN)
app = Flask('')

# ====== БАЗА ДАННЫХ ======
def get_db_connection():
    # SQLite файл cf_bot.db на Render будет удаляться при рестарте!
    conn = sqlite3.connect('cf_bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Твой личный CF ник (для /suggest)
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, handle TEXT)')
    # Список друзей, за которыми следишь ТЫ (индивидуально)
    c.execute('CREATE TABLE IF NOT EXISTS following (user_id TEXT, target_handle TEXT, UNIQUE(user_id, target_handle))')
    # Задачи для дорешивания (индивидуально)
    c.execute('''CREATE TABLE IF NOT EXISTS missed_tasks 
                 (user_id TEXT, contest_id INTEGER, index_str TEXT, name TEXT, rating INTEGER, tags TEXT, 
                  UNIQUE(user_id, contest_id, index_str))''')
    # Техническая таблица для мониторинга (последняя решенная задача друга)
    c.execute('CREATE TABLE IF NOT EXISTS last_solved (handle TEXT PRIMARY KEY, last_id TEXT)')
    conn.commit()
    conn.close()

init_db()

# ====== ЛОГИКА МОНИТОРИНГА (УВЕДОМЛЕНИЯ О ДРУЗЬЯХ) ======
def monitoring_worker():
    print("Мониторинг запущен...")
    while True:
        try:
            conn = get_db_connection()
            # Находим все уникальные ники, за которыми хоть кто-то следит
            distinct_targets = conn.execute("SELECT DISTINCT target_handle FROM following").fetchall()
            
            for row in distinct_targets:
                target = row['target_handle']
                time.sleep(2) # Защита от бана API
                
                resp = requests.get(f"https://codeforces.com/api/user.status?handle={target}&from=1&count=5", timeout=10).json()
                if resp.get('status') == 'OK' and resp['result']:
                    last_sub = resp['result'][0]
                    if last_sub.get('verdict') == 'OK':
                        sub_id = f"{last_sub['contestId']}{last_sub['problem']['index']}"
                        
                        # Проверяем, не уведомляли ли мы об этой задаче раньше
                        db_last = conn.execute("SELECT last_id FROM last_solved WHERE handle = ?", (target,)).fetchone()
                        
                        if not db_last or db_last['last_id'] != sub_id:
                            # Находим всех юзеров, которые следят именно за этим человеком
                            subscribers = conn.execute("SELECT user_id FROM following WHERE target_handle = ?", (target,)).fetchall()
                            
                            for s in subscribers:
                                try:
                                    msg = f"🔔 <b>{target}</b> решил задачу!\n📝 {last_sub['problem']['name']} (Рейтинг: {last_sub['problem'].get('rating', '???')})"
                                    bot.send_message(s['user_id'], msg, parse_mode="HTML")
                                except: pass
                            
                            conn.execute("INSERT OR REPLACE INTO last_solved (handle, last_id) VALUES (?, ?)", (target, sub_id))
                            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Ошибка в мониторинге: {e}")
        time.sleep(60) # Проверка раз в минуту

# ====== ЛОГИКА ЗАДАЧ (UPSOLVING) ======
def update_user_tasks(user_id, handle):
    try:
        user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if user_info['status'] != 'OK': return False
        rating = user_info['result'][0].get('rating', 800)
        
        # Задачи твоего уровня и чуть выше (2 ранга вверх)
        min_r, max_r = max(800, rating - 100), rating + 400

        status = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=5).json()
        solved = {f"{s['problem']['contestId']}{s['problem']['index']}" for s in status['result'] if s.get('verdict') == 'OK'}

        contests = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=5).json()
        if contests['status'] != 'OK': return False
        
        last_5 = [c['contestId'] for c in contests['result'][-5:]]
        conn = get_db_connection()
        
        for c_id in last_5:
            time.sleep(0.5)
            st = requests.get(f"https://codeforces.get/api/contest.standings?contestId={c_id}&from=1&count=1").json()
            if st['status'] == 'OK':
                for p in st['result']['problems']:
                    p_id = f"{p['contestId']}{p['index']}"
                    p_r = p.get('rating', 0)
                    if p_id not in solved and p_r and (min_r <= p_r <= max_r):
                        conn.execute("INSERT OR IGNORE INTO missed_tasks VALUES (?,?,?,?,?,?)",
                                     (user_id, p['contestId'], p['index'], p['name'], p_r, ",".join(p.get('tags', []))))
        conn.commit()
        conn.close()
        return True
    except: return False

# ====== КОМАНДЫ ======
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    help_text = (
        "🚀 <b>CF Personal Bot</b>\n\n"
        "<b>Твоё развитие:</b>\n"
        "/add_cf [ник] — привязать свой аккаунт\n"
        "/update — собрать задачи из твоих последних контестов\n"
        "/suggest — получить случайную задачу для дорешивания\n\n"
        "<b>Слежка за друзьями:</b>\n"
        "/follow [ник] — следить за решениями друга\n"
        "/unfollow [ник] — перестать следить\n"
        "/status — твой профиль и список друзей"
    )
    bot.reply_to(message, help_text, parse_mode="HTML")

@bot.message_handler(commands=['follow'])
def cmd_follow(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Пиши: /follow [ник]")
    
    target = parts[1]
    user_id = str(message.from_user.id)
    
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO following (user_id, target_handle) VALUES (?, ?)", (user_id, target))
        conn.commit()
        bot.reply_to(message, f"✅ Теперь ты следишь за <b>{target}</b>. Я напишу, когда он сдаст задачу!", parse_mode="HTML")
    except:
        bot.reply_to(message, "Ты уже следишь за ним.")
    finally: conn.close()

@bot.message_handler(commands=['status'])
def cmd_status(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (user_id,)).fetchone()
    follows = conn.execute("SELECT target_handle FROM following WHERE user_id = ?", (user_id,)).fetchall()
    tasks_count = conn.execute("SELECT COUNT(*) FROM missed_tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
    conn.close()

    text = f"👤 <b>Твой ник:</b> {user['handle'] if user else 'не привязан'}\n"
    text += f"📚 Задач к дорешиванию: <b>{tasks_count}</b>\n\n"
    text += "👥 <b>Ты следишь за:</b>\n"
    if follows:
        for f in follows: text += f"• <code>{f['target_handle']}</code>\n"
    else: text += "Список пуст."
    
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['add_cf'])
def cmd_add_cf(message):
    handle = message.text.split()[1] if len(message.text.split()) > 1 else None
    if not handle: return bot.reply_to(message, "Укажи ник!")
    
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO users (user_id, handle) VALUES (?, ?)", (str(message.from_user.id), handle))
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ Твой ник {handle} сохранен. Используй /update.")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user: return bot.reply_to(message, "Сначала /add_cf")
    
    bot.reply_to(message, "⏳ Анализирую... Это займет около 10 секунд.")
    if update_user_tasks(user_id, user['handle']):
        bot.reply_to(message, "✅ База задач обновлена!")
    else: bot.reply_to(message, "❌ Ошибка API.")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    task = conn.execute("SELECT * FROM missed_tasks WHERE user_id = ? ORDER BY RANDOM() LIMIT 1", (user_id,)).fetchone()
    if not task:
        conn.close()
        return bot.reply_to(message, "Задач нет. Нажми /update.")
    
    conn.execute("DELETE FROM missed_tasks WHERE user_id = ? AND contest_id = ? AND index_str = ?", 
                 (user_id, task['contest_id'], task['index_str']))
    conn.commit()
    conn.close()
    
    link = f"https://codeforces.com/contest/{task['contest_id']}/problem/{task['index_str']}"
    bot.reply_to(message, f"🎯 <b>Задача: {task['name']}</b> ({task['rating']})\nТеги: {task['tags']}\n{link}", parse_mode="HTML")

# ====== ЗАПУСК ======
@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    # Поток для мониторинга
    Thread(target=monitoring_worker, daemon=True).start()
    # Поток для Flask (Render)
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    
    print("Бот в эфире...")
    bot.infinity_polling()
