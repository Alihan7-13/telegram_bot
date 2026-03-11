import os
from threading import Thread
from flask import Flask

# Создаем микро-сервер для Render
app = Flask('')

@app.route('/')
def home():
    return "I am alive"

def run_web_server():
    # Render сам подставит нужный порт в переменную PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Запускаем сервер в отдельном потоке, чтобы он не мешал основному циклу бота
Thread(target=run_web_server).start()
import requests
import time

# ====== НАСТРОЙКИ ======
# СРОЧНО ПОМЕНЯЙ ТОКЕН НА НОВЫЙ ИЗ BOTFATHER

CHAT_ID = 6883445011
TOKEN = os.environ.get("BOT_TOKEN")
CF_HANDLES = ["whyy", "NullPase"]
AC_HANDLES = ["isa934578", "NullPhase"]

CHECK_INTERVAL = 60

# Инициализируем словари ТЕКУЩИМ временем. 
# Бот будет присылать только новые сабмиты, сделанные после его запуска.
current_time = int(time.time())
last_cf_time = {h: current_time for h in CF_HANDLES}
last_ac_time = {h: current_time for h in AC_HANDLES}

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    # Меняем на HTML. Это спасет от падений из-за спецсимволов и нижних подчеркиваний
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

def check_codeforces():
    for h in CF_HANDLES:
        try:
            r = requests.get(f"https://codeforces.com/api/user.status?handle={h}&count=10", timeout=10)
            data = r.json()
        except:
            continue

        if data.get("status") != "OK":
            continue

        for sub in reversed(data["result"]):
            t = sub.get("creationTimeSeconds", 0)
            if t <= last_cf_time[h]:
                continue

            res = sub.get("verdict", "?")
            
            # Экранируем скобки, чтобы HTML разметка телеграма не сломалась
            name = sub["problem"].get("name", "?").replace("<", "&lt;").replace(">", "&gt;")
            rating = sub["problem"].get("rating", "?")

            emoji = ("✅" if res == "OK" else
                     "❌" if "WRONG_ANSWER" in res else
                     "⏱" if "TIME_LIMIT_EXCEEDED" in res else
                     "💥" if "RUNTIME_ERROR" in res else
                     "⚠️")

            send(f"{emoji} <b>{h}</b> on CF: {name} | {res} | rating {rating}")
            last_cf_time[h] = t

def check_atcoder():
    for h in AC_HANDLES:
        try:
            # Обязательно используем from_second, чтобы скачивать только новые данные
            r = requests.get(
                f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={h}&from_second={last_ac_time[h]}",
                timeout=10
            )
            data = r.json()
        except:
            continue

        if not isinstance(data, list):
            continue

        # Сортируем сабмиты по времени по возрастанию
        for sub in sorted(data, key=lambda x: x.get("epoch_second", 0)):
            t = sub.get("epoch_second", 0)
            if t <= last_ac_time[h]:
                continue

            pid = sub.get("problem_id", "?")
            result = sub.get("result", "?")

            emoji = ("✅" if result == "AC" else
                     "❌" if "WA" in result else
                     "⏱" if "TLE" in result else
                     "💥" if "RE" in result else
                     "📋" if "CE" in result else "⚠️")

            # Убрал difficulty, так как API его здесь не предоставляет
            send(f"{emoji} <b>{h}</b> on AtCoder: {pid} | {result}")
            last_ac_time[h] = t

        # Обязательная задержка по правилам Kenkoooo API
        time.sleep(1)

send("🤖 Бот запущен, отслеживаю новые сабмиты...")

while True:
    check_codeforces()
    check_atcoder()
    time.sleep(CHECK_INTERVAL)
