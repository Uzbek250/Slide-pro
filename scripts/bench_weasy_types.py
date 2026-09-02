#!/usr/bin/env python3
"""10 xil shablonli tipik deckni yagona-hujjat rejimda vaqtlash."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.pdf_renderer import render_deck_to_pdf

DECK = {
    "title": "Vaqt sinovi",
    "theme": "minimal",
    "slides": [
        {"type": "title", "heading": "Raqamli transformatsiya 2026", "subheading": "Barcha sohalarda innovatsion yondashuv", "tags": ["strategiya", "IT"]},
        {"type": "bullets", "heading": "Asosiy yo'nalishlar", "bullets": [
            "Raqamli infratuzilmani rivojlantirish davlat xizmatlari sifatini oshiradi",
            "Kadrlar salohiyatini oshirish orqali IT sohasida bandlikni kengaytirish",
            "Xususiy sektorni qo'llab-quvvatlash iqtisodiy o'sishni tezlashtiradi",
            "Innovatsion startaplar uchun qulay muhit yaratish ustuvor vazifadir"]},
        {"type": "two_column", "heading": "Imkoniyat va xavflar",
         "left_title": "Imkoniyatlar", "left_points": ["Tezkor o'sish", "Global bozor", "Innovatsiya"],
         "right_title": "Xavflar", "right_points": ["Raqobat", "Qonunchilik", "Texnologiya"]},
        {"type": "timeline", "heading": "Amalga oshirish bosqichlari", "steps": [
            {"title": "Tahlil", "detail": "Bozor va mijozlar ehtiyojini chuqur o'rganish, ma'lumotlarni yig'ish"},
            {"title": "Prototip", "detail": "Minimal mahsulot versiyasini yaratish va foydalanuvchilar bilan sinash"},
            {"title": "Ishga tushirish", "detail": "To'liq mahsulotni bozorga chiqarish va marketing kampaniyasi"},
            {"title": "Masshtablash", "detail": "Xalqaro bozorlarga chiqish va operatsion samaradorlikni oshirish"}]},
        {"type": "icon_grid", "heading": "To'rt asosiy ustun", "items": [
            {"icon": "⚡", "title": "Tezlik", "detail": "Jarayonlarni optimallashtirish orqali vaqt tejaymiz"},
            {"icon": "🛡️", "title": "Xavfsizlik", "detail": "Ma'lumotlar shifrlangan holda saqlanadi"},
            {"icon": "🎯", "title": "Aniqlik", "detail": "Natijalarni o'lchash tizimi mavjud"},
            {"icon": "🌱", "title": "Barqarorlik", "detail": "Uzoq muddatli rivojlanish strategiyasi"}]},
        {"type": "stats_grid", "heading": "Raqamlarda", "stats": [
            {"value": "2.5M", "label": "faol foydalanuvchilar"}, {"value": "98%", "label": "qoniqish"},
            {"value": "145", "label": "mamlakat"}, {"value": "12", "label": "yil tajriba"}]},
        {"type": "big_stat", "heading": "Bozor hajmi 2030", "stat": "1.8T$", "stat_label": "prognoz",
         "context": "Yillik o'sish 37% ni tashkil etadi va tendensiya davom etishi kutilmoqda."},
        {"type": "quote", "quote_text": "Innovatsiya — bu o'zgarish", "quote_author": "Anonim",
         "context": "Har bir sohada yangi texnologiyalar qo'llanilmoqda."},
        {"type": "bar_chart", "heading": "Daromad o'sishi", "bars": [
            {"label": "2022", "value": 40}, {"label": "2023", "value": 62},
            {"label": "2024", "value": 88}, {"label": "2025", "value": 120}]},
        {"type": "table", "heading": "Taqqoslash", "columns": ["Xususiyat", "Standart", "Premium"],
         "rows": [["Yuklab olish", "10 GB", "Cheksiz"], ["Yordam", "Email", "24/7"], ["Narx", "Bepul", "49$"]]},
        {"type": "closing", "heading": "Rahmat!", "subheading": "Savollaringiz bo'lsa, marhamat"},
    ],
}

t0 = time.perf_counter()
render_deck_to_pdf(DECK, "/tmp/weasy_10types.pdf")
print(f"10 xil shablon: {time.perf_counter()-t0:.1f} s")
