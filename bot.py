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
        except:
            pass
    return {}  # пустой словарь для новых пользователей

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

state = load_data()
last_check = {}  # метки времени для каждого ника

# --- Вспомогательные функции ---
def send_msg(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# --- Команды бота ---
@bot.message_handler(commands=['start', 'status'])
def cmd_status(message):
    chat_id = str(message.chat.id)
    user_data = state.get(chat_id, {"cf": [], "ac": []})
    status_text = (
        "🟢 <b>Бот активен</b>\n\n"
        f"👥 CF: <code>{', '.join(user_data['cf'])}</code>\n"
        f"👥 AC: <code>{', '.join(user_data['ac'])}</code>\n"
        f"📈 Интервал: {CHECK_INTERVAL} сек"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")

@bot.message_handler(commands=['add'])
def cmd_add(message):
    chat_id = str(message.chat.id)
    if chat_id not in state:
        state[chat_id] = {"cf": [], "ac": []}

    try:
        _, platform, handle = message.text.split()
        platform = platform.lower()
        if platform not in ["cf", "ac"]:
            return bot.reply_to(message, "Используй: /add [cf/ac] [ник]")

        if handle in state[chat_id][platform]:
            return bot.reply_to(message, "Уже отслеживаю.")

        # Проверка существования ника на CF
        if platform == "cf":
            r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=5)
            if r.json().get("status") != "OK":
                return bot.reply_to(message, "❌ Ник не найден на CF")

        state[chat_id][platform].append(handle)
        last_check[handle] = int(time.time())
        save_data(state)
        bot.reply_to(message, f"✅ {handle} добавлен!")
    except:
        bot.reply_to(message, "Ошибка. Формат: /add cf|ac ник")

# --- Остальные команды suggest, suggest_ac, audit ---
# их код полностью оставлен как у тебя
# Внутри них надо использовать chat_id, если хочешь ограничить ники конкретным пользователем

# --- Логика мониторинга ---
def check_updates():
    while True:
        for chat_id, user_data in state.items():
            # --- Codeforces ---
            for h in user_data["cf"]:
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
                                send_msg(chat_id, f"{emoji} <b>{h}</b> (CF): {p['name']} [{p.get('rating','?')}] | {res}")
                                last_check[h] = tm
                except Exception as e:
                    print(f"CF Error {h}: {e}")

            # --- AtCoder ---
            for h in user_data["ac"]:
                try:
                    r = requests.get(f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={h}&from_second={last_check.get(h, 0)}", timeout=10).json()
                    for sub in sorted(r, key=lambda x: x.get("epoch_second", 0)):
                        tm = sub.get("epoch_second", 0)
                        if tm > last_check.get(h, 0):
                            res = sub.get("result", "?")
                            emoji = "✅" if res == "AC" else "❌"
                            send_msg(chat_id, f"{emoji} <b>{h}</b> (AtCoder): {sub.get('problem_id')} | {res}")
                            last_check[h] = tm
                except Exception as e:
                    print(f"AC Error {h}: {e}")
                time.sleep(1)  # уважение к API AtCoder

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
