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
CHAT_ID = 6883445011 # ID для уведомлений о мониторинге
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
    return {
        "cf_users": {}, # {user_id: {"handle": "...", "rating": 800, "debts": []}}
        "monitored_cf": ["whyy", "NullPase"], 
        "monitored_ac": ["isa934578", "NullPhase"]
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

state = load_data()
# Для мониторинга новых решений
last_check = {h: int(time.time()) for h in state["monitored_cf"] + state["monitored_ac"]}

# --- Логика Codeforces Долгов ---
def sync_cf_debts(user_id):
    u_data = state["cf_users"].get(str(user_id))
    if not u_data: return "no_handle"
    
    handle = u_data["handle"]
    try:
        # 1. Получаем рейтинг
        user_info = requests.get(f"https://codeforces.com/api/user.info?handles={handle}", timeout=10).json()
        if user_info['status'] == 'OK':
            u_data["rating"] = user_info['result'][0].get('rating', 800)
        
        # 2. Получаем последние 5 контестов пользователя
        hist = requests.get(f"https://codeforces.com/api/user.rating?handle={handle}", timeout=10).json()
        if hist['status'] != 'OK': return "api_error"
        
        my_contests = [c['contestId'] for c in hist['result'][-5:]]
        
        # 3. Список уже решенных задач
        status = requests.get(f"https://codeforces.com/api/user.status?handle={handle}", timeout=10).json()
        solved = {f"{s['problem']['contestId']}{s['problem']['index']}" for s in status['result'] if s.get('verdict') == 'OK'}
        
        # 4. Собираем нерешенные задачи из этих контестов
        new_debts = []
        for c_id in my_contests:
            standings = requests.get(f"https://codeforces.com/api/contest.standings?contestId={c_id}&from=1&count=1").json()
            if standings['status'] == 'OK':
                for p in standings['result']['problems']:
                    p_id = f"{p['contestId']}{p['index']}"
                    p_rating = p.get('rating', 0)
                    # Если не решена и подходит по сложности (-100... +400)
                    if p_id not in solved and p_rating and (u_data["rating"] - 100 <= p_rating <= u_data["rating"] + 400):
                        new_debts.append({
                            "id": p_id,
                            "name": p['name'],
                            "rating": p_rating,
                            "link": f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"
                        })
            time.sleep(0.5)
            
        u_data["debts"] = new_debts
        save_data(state)
        return "success"
    except Exception as e:
        print(f"Sync error: {e}")
        return "fatal_error"

# --- Команды бота ---

@bot.message_handler(commands=['add_cf'])
def cmd_add_cf(message):
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Используй: /add_cf твой_ник")
    
    handle = parts[1]
    user_id = str(message.from_user.id)
    
    state["cf_users"][user_id] = {"handle": handle, "rating": 800, "debts": []}
    save_data(state)
    bot.reply_to(message, f"✅ Ник {handle} привязан! Нажми /update, чтобы собрать долги.")


@bot.message_handler(commands=['update'])
def cmd_update(message):
    uid = str(message.from_user.id)
    if uid not in state["cf_users"]: return bot.reply_to(message, "Сначала /add_cf")
    
    msg = bot.reply_to(message, "🔍 Проверяю твои последние 5 контестов...")
    res = sync_cf_debts(uid)
    
    if res == "success":
        count = len(state["cf_users"][uid]["debts"])
        bot.edit_message_text(f"✅ Готово! Найдено долгов: {count}", msg.chat.id, msg.message_id)
    else:
        bot.edit_message_text(f"❌ Ошибка при обновлении. Попробуй позже.", msg.chat.id, msg.message_id)

@bot.message_handler(commands=['suggest'])
def cmd_suggest(message):
    uid = str(message.from_user.id)
    if uid not in state["cf_users"]: return bot.reply_to(message, "Сначала /add_cf")
    
    u_data = state["cf_users"][uid]
    
    # 1. Сначала даем задачу из долгов (контестов)
    if u_data["debts"]:
        task = random.choice(u_data["debts"])
        response = (
            f"🚩 <b>Долг из контеста:</b>\n"
            f"Задача: {task['name']} ({task['rating']})\n"
            f"🔗 {task['link']}"
        )
        return bot.reply_to(message, response, parse_mode="HTML")
    
    # 2. Если долгов нет - старая логика роадмапа (упрощенно для примера)
    bot.reply_to(message, "💡 Долгов нет! Ищу задачу по общему роадмапу...")
    # Тут можно вызвать вашу функцию get_roadmap_problem из первого кода

@bot.message_handler(commands=['status'])
def cmd_status(message):
    uid = str(message.from_user.id)
    if uid in state["cf_users"]:
        u = state["cf_users"][uid]
        text = (f"👤 <b>Ник:</b> {u['handle']}\n"
                f"📈 <b>Рейтинг:</b> {u['rating']}\n"
                f"📚 <b>Долгов:</b> {len(u['debts'])}")
    else:
        text = "Вы не зарегистрированы. Используйте /add_cf"
    bot.reply_to(message, text, parse_mode="HTML")

# --- Мониторинг (из вашего 1-го кода) ---
def send_msg(text):
    try: bot.send_message(CHAT_ID, text, parse_mode="HTML")
    except: pass

def check_updates():
    while True:
        # Мониторинг CF (короткий список для уведомлений)
        for h in state["monitored_cf"]:
            try:
                r = requests.get(f"https://codeforces.com/api/user.status?handle={h}&from=1&count=5", timeout=10).json()
                if r.get("status") == "OK":
                    for sub in reversed(r["result"]):
                        tm = sub.get("creationTimeSeconds", 0)
                        if tm > last_check.get(h, 0):
                            res = sub.get("verdict", "TESTING")
                            if res == "TESTING": continue
                            p = sub["problem"]
                            emoji = "✅" if res == "OK" else "❌"
                            send_msg(f"{emoji} <b>{h}</b> (CF): {p['name']} | {res}")
                            last_check[h] = tm
            except: pass
        time.sleep(CHECK_INTERVAL)

# --- Flask & Run ---
@app.route('/')
def ping(): return "OK", 200

if __name__ == "__main__":
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    Thread(target=check_updates, daemon=True).start()
    print("Бот запущен...")
    bot.infinity_polling()
