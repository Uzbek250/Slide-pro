#!/usr/bin/env python3
"""Bot komandalari va tavsifini o'rnatish (Telegram Bot API)."""
import os
import httpx

TOKEN = None
with open("/home/capitan/Slide/.env") as f:
    for line in f:
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            TOKEN = line.strip().split("=", 1)[1]
            break
assert TOKEN, "TELEGRAM_BOT_TOKEN topilmadi"

base = f"https://api.telegram.org/bot{TOKEN}"
commands = [
    {"command": "start", "description": "Botni ishga tushirish"},
    {"command": "sozlama", "description": "Slayd soni, dizayn"},
    {"command": "help", "description": "Yordam"},
]

r = httpx.post(f"{base}/setMyCommands", json={"commands": commands}, timeout=15)
print("setMyCommands:", r.status_code, r.json().get("ok"))

desc = ("Mavzuni yozing — tayyor PDF taqdimotni qaytaraman. AI matnni yozadi, "
        "dizayn tayyor shablonlarda. Progress bar va bekor qilish tugmasi bor.")
r = httpx.post(f"{base}/setMyDescription", json={"description": desc}, timeout=15)
print("setMyDescription:", r.status_code, r.json().get("ok"))

r = httpx.post(f"{base}/setMyShortDescription",
               json={"short_description": "Mavzu -> tayyor PPTX/PDF taqdimot"},
               timeout=15)
print("setMyShortDescription:", r.status_code, r.json().get("ok"))

r = httpx.get(f"{base}/getMyCommands", timeout=15)
print("Tasdiq:", r.json()["result"])
