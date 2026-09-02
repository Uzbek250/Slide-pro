"""
WeasyPrint PILOT: Chromium'siz 11 shablonni PDF'ga render qilish sinovi.

Har bir slayd alohida 1920x1080 sahifa sifatida renderlanadi (shablonlar
to'liq HTML hujjat bo'lgani uchun), so'ng bitta PDF'ga birlashtiriladi.

Ishga tushirish:
    .venv/bin/python scripts/weasy_pilot.py [theme] [out.pdf]
"""
import io
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.renderer import render_slide_html  # noqa: E402

THEME = sys.argv[1] if len(sys.argv) > 1 else "minimal"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/weasy_pilot.pdf"
FONT_CSS = os.environ.get(
    "FONT_CSS_URL",
    "app/static/fonts/fonts.css",
)  # lokal shriftlar; bo'sh bo'lsa Google Fonts

# 11 layout turi — har biri uchun minimal namunaviy slayd
SAMPLE_SLIDES = [
    {"type": "title", "heading": "WeasyPrint sinovi", "subheading": "Chromium'siz vektor PDF render", "tags": ["test", "pilot"]},
    {"type": "bullets", "heading": "Asosiy yo'nalishlar", "bullets": [
        "Birinchi yo'nalish — bu juda muhim va keng tahlil qilinadigan masala hisoblanadi",
        "Ikkinchi yo'nalish — moliyaviy barqarorlik va uzoq muddatli rejalashtirishni o'z ichiga oladi",
        "Uchinchi yo'nalish — texnologik yangilanishlar orqali raqobatbardoshlikni oshirishga qaratilgan",
    ]},
    {"type": "two_column", "heading": "Imkoniyat va xavflar",
     "left_title": "Imkoniyatlar", "left_points": ["Tezkor o'sish", "Global bozor", "Innovatsiya"],
     "right_title": "Xavflar", "right_points": ["Raqobat keskinlashuvi", "Qonunchilik o'zgarishlari", "Texnologik xavflar"]},
    {"type": "timeline", "heading": "Bosqichma-bosqich reja",
     "steps": [
         {"title": "Tahlil", "text": "Bozor va mijozlar ehtiyojini chuqur o'rganish"},
         {"title": "Prototip", "text": "Minimal mahsulot versiyasini yaratish va sinash"},
         {"title": "Ishga tushirish", "text": "To'liq mahsulotni bozorga chiqarish"},
         {"title": "Masshtablash", "text": "Xalqaro bozorlarga chiqish strategiyasi"},
     ]},
    {"type": "icon_grid", "heading": "To'rt asosiy ustun",
     "items": [
         {"icon": "⚡", "title": "Tezlik", "text": "Har bir jarayonni optimallashtirish orqali vaqtni tejaymiz"},
         {"icon": "🛡️", "title": "Xavfsizlik", "text": "Ma'lumotlar shifrlangan holda saqlanadi va himoya qilinadi"},
         {"icon": "🎯", "title": "Aniqlik", "text": "Natijalarni o'lchash va tahlil qilish tizimi mavjud"},
         {"icon": "🌱", "title": "Barqarorlik", "text": "Uzoq muddatli rivojlanishga qaratilgan strategiya"},
     ]},
    {"type": "stats_grid", "heading": "Raqamlarda", "stats": [
        {"value": "2.5M", "label": "faol foydalanuvchilar"},
        {"value": "98%", "label": "mijozlar qoniqishi"},
        {"value": "145", "label": "mamlakatda xizmat"},
        {"value": "12", "label": "yillik tajriba"},
    ]},
    {"type": "big_stat", "heading": "Bozor hajmi 2030 yilga borib",
     "stat": "1.8T$", "stat_label": "global AI bozori prognozi",
     "context": "Yillik o'sish sur'ati 37% ni tashkil etadi va bu tendensiya davom etishi kutilmoqda."},
    {"type": "quote", "quote_text": "Sun'iy intellekt — bu yangi elektr energiyasi",
     "quote_author": "Andrew Ng",
     "context": "AI butun sanoat tarmoqlarini tubdan o'zgartirish potensialiga ega."},
    {"type": "bar_chart", "heading": "Yillik daromad o'sishi",
     "bars": [{"label": "2022", "value": 40}, {"label": "2023", "value": 62}, {"label": "2024", "value": 88}, {"label": "2025", "value": 120}]},
    {"type": "table", "heading": "Xizmatlar taqqoslash",
     "columns": ["Xususiyat", "Standart", "Premium"],
     "rows": [["Yuklab olish", "10 GB", "Cheksiz"], ["Yordam", "Email", "24/7 chat"], ["Narx", "Bepul", "49$/oy"]]},
    {"type": "closing", "heading": "Rahmat!", "subheading": "Savollaringiz bo'lsa, marhamat"},
]

PAGE_CSS = """
<style>
  @page { size: 1920px 1080px; margin: 0; }
  html, body { margin: 0 !important; padding: 0 !important; }
</style>
"""


def main() -> int:
    from weasyprint import HTML

    logging.basicConfig(level=logging.WARNING)
    # WeasyPrint nima qo'llab-quvvatlamaganini ko'rish uchun shovqinli
    # bo'lmagan darajada loglaymiz
    wp_logger = logging.getLogger("weasyprint")
    wp_logger.setLevel(logging.INFO)

    # 1) Har bir slaydni alohida render qilamiz
    pdf_blobs: list[bytes] = []
    problems: list[str] = []
    for i, slide in enumerate(SAMPLE_SLIDES, 1):
        t0 = time.perf_counter()
        html = render_slide_html(slide, THEME, page_num=i, font_css_url=FONT_CSS)
        html = html.replace("</head>", PAGE_CSS + "</head>", 1)
        try:
            doc = HTML(string=html, base_url=".").render()
            if len(doc.pages) != 1:
                problems.append(f"#{i} {slide['type']}: {len(doc.pages)} sahifa (1 kutilgan!)")
            data = doc.write_pdf()
            pdf_blobs.append(data)
            print(f"#{i:2d} {slide['type']:12s} → {len(doc.pages)} sahifa, "
                  f"{len(data)/1e3:.0f} KB, {time.perf_counter()-t0:.2f} s")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"#{i} {slide['type']}: XATO {exc}")
            print(f"#{i:2d} {slide['type']:12s} → XATO: {exc}")

    # 2) Birlashtiramiz
    if pdf_blobs:
        from pypdf import PdfWriter
        writer = PdfWriter()
        for blob in pdf_blobs:
            writer.append(io.BytesIO(blob))
        with open(OUT, "wb") as f:
            writer.write(f)
        print(f"\nBirlashtirilgan PDF: {OUT} ({len(pdf_blobs)} sahifa, "
              f"{os.path.getsize(OUT)/1e6:.2f} MB)")

    print(f"\nNatija: {len(SAMPLE_SLIDES) - len(problems)}/{len(SAMPLE_SLIDES)} shablon OK")
    if problems:
        print("Muammolar:")
        for p in problems:
            print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
