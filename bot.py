import os
import time
import sqlite3
import requests
import telebot
import random
from threading import Thread
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА")
bot = telebot.TeleBot(TOKEN)
app = Flask('')

# ====== БАЗА ДАННЫХ ======
def get_db_connection():
    # check_same_thread=False нужен для работы с БД из разных потоков (веб-сервер и бот)
    conn = sqlite3.connect('cf_bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Таблица привязки Telegram ID к CF нику
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id TEXT PRIMARY KEY, handle TEXT)''')
    # Таблица нерешенных задач из контестов
    c.execute('''CREATE TABLE IF NOT EXISTS missed_tasks 
                 (user_id TEXT, contest_id INTEGER, index_str TEXT, 
                  name TEXT, rating INTEGER, tags TEXT, 
                  UNIQUE(user_id, contest_id, index_str))''')
    conn.commit()
    conn.close()

init_db()

# ====== ЛОГИКА CODEFORCES ======
def update_user_tasks(user_id, handle):
    """Парсит 5 последних контестов юзера и ищет нерешенные задачи его уровня"""
    try:
        # 1. Получаем инфу о пользователе (для рейтинга)
        user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if user_info['status'] != 'OK': return False
        current_rating = user_info['result'][0].get('rating', 800)
        
        # Ограничение по уровню: задачи не ниже текущего рейтинга и не выше чем на 400 пунктов (2 ранга)
        min_rating = max(800, current_rating - 100)
        max_rating = current_rating + 400

        # 2. Получаем все сабмиты (чтобы отсеять решенное)
        status_resp = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=5).json()
        solved_ids = set()
        if status_resp['status'] == 'OK':
            for sub in status_resp['result']:
                if sub.get('verdict') == 'OK':
                    solved_ids.add(f"{sub['problem']['contestId']}{sub['problem']['index']}")

        # 3. Получаем последние контесты пользователя
        rating_resp = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=5).json()
        if rating_resp['status'] != 'OK' or not rating_resp['result']: return False
        
        last_5_contests = [c['contestId'] for c in rating_resp['result'][-5:]]

        conn = get_db_connection()
        c = conn.cursor()
        
        # 4. Ищем задачи в этих контестах
        for c_id in last_5_contests:
            time.sleep(0.5)  # Защита от бана CF API
            standings = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1", timeout=5).json()
            if standings['status'] == 'OK':
                for p in standings['result']['problems']:
                    p_id = f"{p.get('contestId')}{p.get('index')}"
                    p_rating = p.get('rating', 0)
                    
                    # Проверяем, что задача не решена и подходит по рейтингу
                    if p_id not in solved_ids and p_rating and (min_rating <= p_rating <= max_rating):
                        tags_str = ",".join(p.get('tags', []))
                        c.execute('''INSERT OR IGNORE INTO missed_tasks 
                                     (user_id, contest_id, index_str, name, rating, tags) 
                                     VALUES (?, ?, ?, ?, ?, ?)''', 
                                  (user_id, p['contestId'], p['index'], p['name'], p_rating, tags_str))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка парсинга: {e}")
        return False

# ====== КОМАНДЫ БОТА ======
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message, "Привет. Используй /add_cf [твой_ник] для привязки аккаунта.\nЗатем используй /update для сбора базы задач и /suggest для получения задачи.")

@bot.message_handler(commands=['add_cf'])
def cmd_add_cf(message):
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Формат: /add_cf [ник]")
    
    handle = parts[1]
    user_id = str(message.from_user.id)
    
    # Проверка существования аккаунта
    try:
        r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
        if r.get('status') != 'OK':
            return bot.reply_to(message, "❌ Ник на CF не найден.")
    except:
        return bot.reply_to(message, "❌ Ошибка соединения с Codeforces.")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, handle) VALUES (?, ?)", (user_id, handle))
    conn.commit()
    conn.close()
    
    bot.reply_to(message, f"✅ Аккаунт {handle} привязан. Нажми /update чтобы собрать задачи из твоих контестов.")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    user = conn.execute("SELECT handle FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return bot.reply_to(message, "Сначала привяжи ник через /add_cf [ник]")
    
    handle = user['handle']
    bot.reply_to(message, f"⏳ Начинаю анализ твоих последних 5 контестов для {handle}. Это займет пару секунд...")
    
    if update_user_tasks(user_id, handle):
        conn = get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM missed_tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
        conn.close()
        bot.reply_to(message, f"✅ База обновлена. Доступно задач для дорешивания: {count}.")
    else:
        bot.reply_to(message, "❌ Ошибка при обновлении. Возможно, API Codeforces недоступен или ты не писал контесты.")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    user_id = str(message.from_user.id)
    conn = get_db_connection()
    
    # Берем случайную задачу пользователя
    task = conn.execute("SELECT * FROM missed_tasks WHERE user_id = ? ORDER BY RANDOM() LIMIT 1", (user_id,)).fetchone()
    
    if not task:
        conn.close()
        return bot.reply_to(message, "Твоя база задач пуста. Вызови /update чтобы собрать их.")
        
    contest_id = task['contest_id']
    index = task['index_str']
    
    # Удаляем выданную задачу из БД (чтобы не предлагать дважды)
    conn.execute("DELETE FROM missed_tasks WHERE user_id = ? AND contest_id = ? AND index_str = ?", 
                 (user_id, contest_id, index))
    conn.commit()
    conn.close()
    
    link = f"https://codeforces.com/contest/{contest_id}/problem/{index}"
    text = (
        f"🎯 <b>Задача для тебя:</b>\n\n"
        f"<b>{task['name']}</b>\n"
        f"Рейтинг: {task['rating']}\n"
        f"Теги: <code>{task['tags']}</code>\n\n"
        f"🔗 <a href='{link}'>Перейти к задаче</a>"
    )
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

# ====== ВЕБ-СЕРВЕР ======
@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    print("Бот запущен...")
    bot.infinity_polling()
