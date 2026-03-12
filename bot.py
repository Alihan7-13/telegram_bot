import os
import time
import json
import requests
import telebot
import random
from threading import Thread
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "users_data.json"
CHECK_INTERVAL = 60

bot = telebot.TeleBot(TOKEN)
app = Flask('')

# --- Работа с данными (Теперь по User ID) ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# Глобальное состояние (словарь словарей)
users_state = load_data()

def get_user_config(user_id):
    user_id = str(user_id)
    if user_id not in users_state:
        users_state[user_id] = {"cf": [], "ac": []}
    return users_state[user_id]

# --- Команды бота ---

@bot.message_handler(commands=['start', 'status'])
def cmd_status(message):
    cfg = get_user_config(message.from_user.id)
    status_text = (
        "🟢 <b>Бот активен</b>\n\n"
        f"👤 Твои CF ники: <code>{', '.join(cfg['cf']) if cfg['cf'] else 'пусто'}</code>\n"
        f"👤 Твои AC ники: <code>{', '.join(cfg['ac']) if cfg['ac'] else 'пусто'}</code>\n"
        "📈 Режим: Индивидуальная БД включена"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")

@bot.message_handler(commands=['add'])
def cmd_add(message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            return bot.reply_to(message, "Формат: /add [cf/ac] [ник]")
        
        platform = parts[1].lower()
        handle = parts[2]
        user_id = str(message.from_user.id)

        if platform not in ["cf", "ac"]:
            return bot.reply_to(message, "Платформы только cf или ac")

        cfg = get_user_config(user_id)
        if handle in cfg[platform]:
            return bot.reply_to(message, "Уже добавлен в твой список.")

        # Проверка ника
        if platform == "cf":
            r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5).json()
            if r.get("status") != "OK":
                return bot.reply_to(message, "❌ Ник на CF не найден")

        cfg[platform].append(handle)
        save_data(users_state)
        bot.reply_to(message, f"✅ {handle} добавлен в твой список!")
    except Exception as e:
        bot.reply_to(message, f"Ошибка при добавлении: {e}")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    try:
        parts = message.text.split()
        user_id = str(message.from_user.id)
        cfg = get_user_config(user_id)

        if len(parts) < 2:
            if not cfg['cf']:
                return bot.reply_to(message, "Сначала добавь ник: /add cf твой_ник")
            handle = cfg['cf'][0]
        else:
            handle = parts[1]

        bot.send_chat_action(message.chat.id, 'typing')

        # 1. Данные пользователя
        user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}").json()
        user_status = requests.get(f"https://codeforces.com/api/user.status?handle={handle}").json()
        
        if user_info['status'] != "OK": return bot.reply_to(message, "Ошибка API")

        rating = user_info['result'][0].get('rating', 800)
        
        # Маппинг тегов (твой роадмап)
        if rating <= 999:
            target_tags, min_r, max_r = ["brute force", "sortings", "math"], 800, 1000
        elif rating <= 1199:
            target_tags, min_r, max_r = ["math", "binary search", "two pointers", "dp"], 1000, 1200
        elif rating <= 1399:
            target_tags, min_r, max_r = ["graphs", "dsu", "trees", "dp"], 1200, 1400
        elif rating <= 1599:
            target_tags, min_r, max_r = ["constructive algorithms", "shortest paths", "probabilities"], 1400, 1600
        else:
            target_tags, min_r, max_r = ["dp", "data structures", "games"], 1600, 2000

        # Анализ решенных задач и поиск слабого тега
        tag_stats = {tag: 0 for tag in target_tags}
        solved_ids = set()
        for sub in user_status['result']:
            if sub.get('verdict') == 'OK':
                p = sub['problem']
                solved_ids.add(f"{p.get('contestId')}{p.get('index')}")
                for tag in p.get('tags', []):
                    if tag in tag_stats: tag_stats[tag] += 1
        
        weak_tag = min(tag_stats, key=tag_stats.get)

        # === НОВАЯ ЛОГИКА: Поиск в последних 5 контестах ===
        pool = []
        try:
            contests = requests.get("https://codeforces.com/api/contest.list?gym=false").json()['result']
            recent_ids = [c['id'] for c in contests if c['phase'] == 'FINISHED'][:5]
            
            for c_id in recent_ids:
                c_data = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1").json()
                if c_data['status'] == "OK":
                    for p in c_data['result']['problems']:
                        p_id = f"{p.get('contestId')}{p.get('index')}"
                        p_rating = p.get('rating', 0)
                        if p_id not in solved_ids and p_rating and (min_r <= p_rating <= max_r):
                            if weak_tag in p.get('tags', []):
                                pool.append(p)
        except: pass

        source_info = "Последние контесты"
        
        # Если в новых контестах не нашли, ищем по всей базе (старая логика)
        if not pool:
            source_info = "Общий архив"
            all_probs = requests.get("https://codeforces.com/api/problemset.problems").json()
            for p in all_probs['result']['problems']:
                p_id = f"{p.get('contestId')}{p.get('index')}"
                p_rating = p.get('rating', 0)
                if p_id not in solved_ids and p_rating and (min_r <= p_rating <= max_r):
                    if weak_tag in p.get('tags', []):
                        pool.append(p)

        if not pool:
            return bot.reply_to(message, f"Нет задач по тегу {weak_tag}. Попробуй позже.")

        p = random.choice(pool)
        link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
        
        bot.reply_to(message, (
            f"🎯 <b>Suggest для {handle}</b>\n"
            f"Слабый тег: <code>#{weak_tag}</code>\n"
            f"Источник: {source_info}\n\n"
            f"Задача: <b>{p['name']}</b> ({p.get('rating')})\n"
            f"🔗 <a href='{link}'>Перейти к задаче</a>"
        ), parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        bot.reply_to(message, "Ошибка в логике suggest.")

# --- Очистка и запуск (мониторинг остается общим, но берет всех из базы) ---
def check_updates():
    # Чтобы не нагружать API, здесь можно оставить упрощенную проверку
    # или переделать под всех пользователей в users_state
    pass

@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
    print("Бот запущен...")
    bot.infinity_polling()
