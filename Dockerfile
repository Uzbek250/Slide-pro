# Microsoft'ning rasmiy Playwright image'i — Chromium va barcha system
# kutubxonalari (libnss3, libgbm va h.k.) allaqachon o'rnatilgan holda keladi.
# Bu Render'da "missing shared libraries" xatoligining oldini oladi.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

# Faqat requirements.txt ni oldin ko'chirish — Docker layer cache samarali
# ishlashi uchun (kod o'zgarsa ham, dependency qayta o'rnatilmaydi)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# XAVFSIZLIK: konteyner ROOT sifatida emas, alohida yaratilgan foydalanuvchi
# nomidan ishga tushadi. Chromium konteyner ichida --no-sandbox bilan
# ishlashga majbur (sandbox odatda konteynerda ishlamaydi), shuning uchun
# uning o'z ichki himoyasi kamaygan — buni non-root user bilan qoplaymiz:
# agar Chromium yoki ilovada zaiflik topilsa, tajovuzkor baribir konteyner
# ichida root huquqiga ega bo'lolmaydi. /ms-playwright papkasi (brauzer
# binarylari) va /app (ilova kodi, /tmp chiqish papkasi) shu userga
# tegishli qilib beriladi.
RUN groupadd --system app && useradd --system --create-home --gid app app \
    && mkdir -p /tmp/slide_outputs \
    && chown -R app:app /app /ms-playwright /tmp/slide_outputs
USER app

EXPOSE 8000

# --workers 1: xotira cheklangan free/hobby tarifda bir nechta worker
# Chromium bilan birga xotirani tezda tugatadi. Trafik ko'paysa,
# workers sonini oshirish o'rniga instansiyani vertikal kattalashtirish
# (yoki queue tizimi) to'g'riroq yechim.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
