import os
import time
import json
import requests
import telebot
from threading import Thread
from flask import Flask

# ====== КОНФИГУРАЦИЯ ======
TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = 6883445011
DATA_FILE = "data.json"
CHECK_INTERVAL = 60

bot = telebot.TeleBot(TOKEN)
app = Flask('')

# --- Работа с данными ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {"cf": ["whyy", "NullPase"], "ac": ["isa934578", "NullPhase"]}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

state = load_data()
# Инициализируем метки времени текущим моментом
last_check = {h: int(time.time()) for h in state["cf"] + state["ac"]}

# --- Вспомогательные функции ---
def send_msg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# --- Команды бота ---
@bot.message_handler(commands=['start', 'status'])
def cmd_status(message):
    status_text = (
        "🟢 <b>Бот активен</b>\n\n"
        f"👥 CF: <code>{', '.join(state['cf'])}</code>\n"
        f"👥 AC: <code>{', '.join(state['ac'])}</code>\n"
        "📈 Интервал: 60 сек"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")

@bot.message_handler(commands=['add'])
def cmd_add(message):
    try:
        _, platform, handle = message.text.split()
        platform = platform.lower()
        if platform not in state:
            return bot.reply_to(message, "Используй: /add [cf/ac] [ник]")
        
        if handle in state[platform]:
            return bot.reply_to(message, "Уже отслеживаю.")

        # Простая проверка существования ника на CF
        if platform == "cf":
            r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5)
            if r.json().get("status") != "OK":
                return bot.reply_to(message, "❌ Ник не найден на CF")

        state[platform].append(handle)
        last_check[handle] = int(time.time())
        save_data(state)
        bot.reply_to(message, f"✅ {handle} добавлен!")
    except:
        bot.reply_to(message, "Ошибка. Формат: /add cf ник")

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            return bot.reply_to(message, "Пиши: /suggest ник")
        
        handle = parts[1]
        bot.send_chat_action(message.chat.id, 'typing')

        # 1. Запросы к API (с таймаутами на случай лагов CF)
        try:
            user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=10).json()
            user_status = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=10).json()
        except:
            return bot.reply_to(message, "Codeforces API долго думает. Попробуй через минуту.")

        if user_info.get("status") != "OK" or user_status.get("status") != "OK":
            return bot.reply_to(message, "Не удалось получить данные профиля.")

        rating = user_info['result'][0].get('rating', 800)
        
        # 2. Строгий маппинг тегов по твоему Google Doc роадмапу
        if rating <= 999:
            target_tags = ["brute force", "sortings", "strings", "math", "number theory", "binary search", "two pointers", "bitmasks"]
            min_r, max_r = 800, 1000
        elif rating <= 1199:
            target_tags = ["brute force", "sortings", "strings", "math", "number theory", "binary search", "two pointers", "bitmasks", "dp", "combinatorics"]
            min_r, max_r = 1000, 1200
        elif rating <= 1399:
            target_tags = ["math", "number theory", "binary search", "two pointers", "combinatorics", "dp", "bitmasks", "graphs", "trees", "dsu"]
            min_r, max_r = 1200, 1400
        elif rating <= 1599:
            target_tags = ["math", "number theory", "binary search", "two pointers", "combinatorics", "dp", "graphs", "trees", "constructive algorithms", "probabilities", "dsu", "shortest paths", "games"]
            min_r, max_r = 1400, 1600
        else: # 1600-1899+
            target_tags = ["dp", "graphs", "trees", "math", "number theory", "combinatorics", "probabilities", "bitmasks", "data structures", "games", "constructive algorithms"]
            min_r, max_r = 1600, 1900

        # 3. Анализ слабых мест (считаем только теги текущего уровня)
        tag_stats = {tag: 0 for tag in target_tags}
        solved_ids = set()
        
        for sub in user_status['result']:
            if sub.get('verdict') == 'OK':
                p = sub['problem']
                p_id = f"{p.get('contestId')}{p.get('index')}"
                solved_ids.add(p_id)
                for tag in p.get('tags', []):
                    # Если решенная задача имеет тег из нашего таргета - плюсуем
                    if tag in tag_stats:
                        tag_stats[tag] += 1

        # Ищем тег с минимальным количеством решений
        weak_tag = min(tag_stats, key=tag_stats.get)
        count_solved = tag_stats[weak_tag]

        # 4. Поиск подходящей задачи
        try:
            all_probs = requests.get("https://codeforces.com/api/problemset.problems", timeout=15).json()
        except:
            return bot.reply_to(message, "Не смог загрузить банк задач CF (таймаут).")

        if all_probs.get("status") != "OK":
            return bot.reply_to(message, "Ошибка API банка задач.")

        import random
        pool = []
        
        # Строгий поиск: нужный тег + нужный рейтинг + не решена
        for p in all_probs['result']['problems']:
            p_id = f"{p.get('contestId')}{p.get('index')}"
            p_rating = p.get('rating', 0)
            
            if not p_rating: continue # Пропускаем задачи без рейтинга

            if min_r <= p_rating <= max_r and p_id not in solved_ids:
                if weak_tag in p.get('tags', []):
                    pool.append(p)

        # Если в строгом диапазоне задач не осталось, чуть расширяем окно поиска
        if not pool:
            for p in all_probs['result']['problems']:
                p_id = f"{p.get('contestId')}{p.get('index')}"
                p_rating = p.get('rating', 0)
                if not p_rating: continue
                if (min_r - 100) <= p_rating <= (max_r + 200) and p_id not in solved_ids:
                    if weak_tag in p.get('tags', []):
                        pool.append(p)

        if not pool:
            return bot.reply_to(message, f"Не нашел нерешенных задач по тегу {weak_tag}. Выбирай другой ник.")

        p = random.choice(pool)
        link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
        
        response = (
            f"🧠 <b>Аудит по роадмапу для {handle}</b>\n"
            f"Твой рейтинг: <code>{rating}</code>\n"
            f"Самое слабое звено уровня: <b>#{weak_tag}</b> (решено: {count_solved})\n\n"
            f"🎯 <b>Цель для тренировки:</b>\n"
            f"Задача: <b>{p['name']}</b>\n"
            f"Сложность: <code>{p.get('rating')}</code>\n"
            f"Теги: {', '.join(p['tags'])}\n\n"
            f"🔗 {link}"
        )
        bot.reply_to(message, response, parse_mode="HTML")

    except Exception as e:
        print(f"Code Error: {e}")
        bot.reply_to(message, "Внутренняя ошибка бота. Глянь логи Render.")
# --- Логика мониторинга ---
def check_updates():
    while True:
        # --- Codeforces ---
        for h in state["cf"]:
            try:
                r = requests.get(f"https://codeforces.com/api/user.status?handle={h}&from=1&count=10", timeout=10).json()
                if r.get("status") == "OK":
                    for sub in reversed(r["result"]):
                        tm = sub.get("creationTimeSeconds", 0)
                        if tm > last_check.get(h, 0):
                            res = sub.get("verdict", "TESTING")
                            if res == "TESTING": continue
                            
                            p = sub["problem"]
                            emoji = "✅" if res == "OK" else "❌"
                            send_msg(f"{emoji} <b>{h}</b> (CF): {p['name']} [{p.get('rating','?')}] | {res}")
                            last_check[h] = tm
            except Exception as e: print(f"CF Error {h}: {e}")

        # --- AtCoder ---
        for h in state["ac"]:
            try:
                now_ts = int(time.time())
                r = requests.get(f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={h}&from_second={last_check.get(h, 0)}", timeout=10).json()
                for sub in sorted(r, key=lambda x: x.get("epoch_second", 0)):
                    tm = sub.get("epoch_second", 0)
                    if tm > last_check.get(h, 0):
                        res = sub.get("result", "?")
                        emoji = "✅" if res == "AC" else "❌"
                        send_msg(f"{emoji} <b>{h}</b> (AtCoder): {sub.get('problem_id')} | {res}")
                        last_check[h] = tm
            except Exception as e: print(f"AC Error {h}: {e}")
            time.sleep(1) # Уважение к API AtCoder

        time.sleep(CHECK_INTERVAL)

# --- Flask для Render ---
@app.route('/')
def ping(): return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    print("Запуск систем...")
    Thread(target=run_flask, daemon=True).start()
    Thread(target=check_updates, daemon=True).start()
    bot.infinity_polling()
