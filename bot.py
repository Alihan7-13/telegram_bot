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
        handle = message.text.split()[1]
        # Берем инфо о юзере, чтобы узнать его рейтинг
        r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}").json()
        rating = r['result'][0].get('rating', 1200)
        
        # Ищем задачи этого рейтинга
        r_probs = requests.get(f"https://codeforces.com/api/problemset.problems?rating={rating}").json()
        if r_probs['status'] == "OK":
            import random
            p = random.choice(r_probs['result']['problems'])
            link = f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
            bot.reply_to(message, f"📚 Советую решить (rating {rating}):\n<b>{p['name']}</b>\n{link}", parse_mode="HTML")
    except:
        bot.reply_to(message, "Используй: /suggest [ник_на_cf]")

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
