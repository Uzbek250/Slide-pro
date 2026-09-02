# Contabo VPS ga joylash (8GB RAM)

Bu yo'riqnoma Slide backend + Telegram botni Contabo VPS (Ubuntu 22.04/24.04,
8GB RAM) ga Docker orqali joylash uchun.

## 1. Serverda Docker o'rnatish

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
# foydalanuvchini docker guruhiga qo'shish (sudo'siz ishlash uchun)
sudo usermod -aG docker $USER
# qayta kirish (yoki: newgrp docker)
```

## 2. Loyihani ko'chirish

```bash
mkdir -p ~/slide && cd ~/slide
git clone https://github.com/Uzbek250/Slide.git .
cp .env.example .env
nano .env    # GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, INTERNAL_TOKEN — majburiy
```

> .env ichidagi API_BASE qiymati kompose ichida avtomatik
> http://backend:8000 ga o'rnatiladi — uni o'zgartirmang.

## 3. Ishga tushirish

```bash
docker compose up -d --build
docker compose ps                 # ikkala servis ham running bo'lishi kerak
docker compose logs -f backend    # birinchi render sinovi
```

Sog'liqni tekshirish:

```bash
curl http://SERVER_IP:8003/health
# {"status":"ok","queued":0,"running":0,"max_concurrent":3,...}
```

Telegram'da botga mavzu yozib sinang. Web UI: http://SERVER_IP:8003/

## 4. Xavfsizlik (asosiy)

- `ufw` yoqilsa: `sudo ufw allow 8003/tcp` (va SSH 22) — yoki portni
  tashqariga ochmasdan, faqat bot ishlatsangiz `sudo ufw deny 8003`
  qiling (bot backend'ga konteyner tarmog'i orqali kiradi, tashqi port
  shart emas).
- `INTERNAL_TOKEN` ni uzoq tasodifiy satr qilib qo'ying:
  `openssl rand -hex 24`
- Docker qayta ishga tushganda avtomatik ishga tushadi (`restart:
  unless-stopped`).

## 5. Yangilash (yangi commit chiqsa)

```bash
cd ~/slide
git pull
docker compose up -d --build
```

Eslatma: git push qilish uchun GitHub token kerak bo'ladi (serverdan).
  Qanday qilish: https://docs.github.com — "personal access token" yarating,
  `git remote set-url origin https://TOKEN@github.com/Uzbek250/Slide.git`

## 6. 8GB RAM chegaralari (hozirgi default'lar)

| Sozlama                  | Qiymat | Izoh |
|--------------------------|--------|------|
| MAX_CONCURRENT_JOBS      | 3      | 3×Chromium ~1.5GB — 8GB uchun xavfsiz |
| MAX_QUEUE                | 12     | ortiqcha so'rov 503 oladi |
| RATE_PER_MINUTE          | 2      | bitta foydalanuvchi daqiqada 2 ta |
| RATE_GLOBAL_PER_MINUTE   | 8      | 3 parallel ish bilan real ~5-6 ta/daq |
| RATE_GLOBAL_PER_DAY      | 600    | Gemini free ~1500 RPD ning xavfsiz qismi |

Ko'proq trafik kutilsa: `.env` da `MAX_CONCURRENT_JOBS=4`,
`RATE_GLOBAL_PER_MINUTE=5` gacha ko'tarish mumkin (8GB yetadi), lekin
Gemini kvotasi va kunlik byudjetni hisobga oling.

## 7. Katta trafikga o'tish (keyingi bosqich)

Hozirgi navbat **xotirada** — bitta backend nusxasi uchun mo'ljallangan.
Foydalanuvchilar ko'payib, bir nechta instansiya/worker kerak bo'lsa:

1. Redis o'rnatish (docker compose'ga `redis` servis qo'shish)
2. `jobs.py` va `limits.py` ni Redis asosiga ko'chirish (deque → Redis
   list, hisoblagichlar → INCR/EXPIRE)
3. Backend'ni 2+ nusxada ko'tarish + yuk taqsimlash (nginx yoki
   Traefik), sticky session shart emas — job_id orqali holat olinadi

Bu o'zgarishlarsiz backend faqat bitta nusxada ishlaydi — shunday ham
~3 taqdimot/daqiqa o'tkazuvchanlik bilan kichik auditoriya uchun yetarli.
