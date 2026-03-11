import os
import time
import json
import requests
from threading import Thread
from flask import Flask
import telebot

# ====== НАСТРОЙКИ ======
TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = 6883445011  # Твой ID
DATA_FILE = "data.json"

bot = telebot.TeleBot(TOKEN)
app = Flask('')

# --- Работа с базой (файлом) ---
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"cf": [], "ac": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# Инициализируем списки
handles = load_data()
last_cf_time = {h: int(time.time()) for h in handles["cf"]}
last_ac_time = {h: int(time.time()) for h in handles["ac"]}

# --- Flask сервер для Render ---
@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- Команды бота ---
@bot.message_handler(commands=['start', 'status'])
def send_status(message):
    msg = (f"🤖 <b>Бот работает!</b>\n"
           f"Отслеживаю CF: <code>{', '.join(handles['cf']) if handles['cf'] else 'пусто'}</code>\n"
           f"Отслеживаю AC: <code>{', '.join(handles['ac']) if handles['ac'] else 'пусто'}</code>")
    bot.send_message(message.chat.id, msg, parse_mode="HTML")

@bot.message_handler(commands=['add'])
def add_handle(message):
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Используй: /add [cf/ac] [ник]")
        return
    
    platform, handle = args[1].lower(), args[2]
    if platform not in ["cf", "ac"]:
        bot.reply_to(message, "Платформа должна быть 'cf' или 'ac'")
        return

    # Проверка ника на CF
    if platform == "cf":
        r = requests.get(f"https://codeforces.com/api/user.info?handles={handle}")
        if r.json().get("status") != "OK":
            bot.reply_to(message, "❌ Такого ника на Codeforces нет!")
            return

    if handle not in handles[platform]:
        handles[platform].append(handle)
        save_data(handles)
        bot.reply_to(message, f"✅ Ник {handle} добавлен в список {platform}!")
    else:
        bot.reply_to(message, "Этот ник уже есть в списке.")

@bot.message_handler(commands=['last'])
def last_subs(message):
    args = message.text.split()
    if len(args) < 2: return
    h = args[1]
    r = requests.get(f"https://codeforces.com/api/user.status?handle={h}&from=1&count=5")
    data = r.json()
    if data["status"] == "OK":
        res = [f"• {s['problem']['name']} -> {s.get('verdict', '?')}" for s in data["result"]]
        bot.reply_to(message, f"Последние 5 сабмитов {h}:\n" + "\n".join(res))

# --- Фоновая проверка сабмитов (Твой старый цикл) ---
def monitoring_loop():
    while True:
        # Тут твоя логика check_codeforces() и check_atcoder()
        # Используй bot.send_message(CHAT_ID, text) для уведомлений
        time.sleep(60)

# --- Запуск ---
if __name__ == "__main__":
    # 1. Запуск Flask
    Thread(target=run_web_server).start()
    # 2. Запуск мониторинга
    Thread(target=monitoring_loop).start()
    # 3. Запуск команд (Polling)
    print("Бот запущен...")
    bot.infinity_polling()
