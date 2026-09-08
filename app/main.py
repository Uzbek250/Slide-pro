"""
FastAPI backend: veb-forma / Telegram bot orqali mavzu qabul qiladi,
Gemini + Playwright (vektor PDF) yordamida taqdimot generatsiya qiladi.

Real foydalanuvchilar uchun mo'ljallangan versiya:
  - so'rov darhol NAVBATGA tushadi (202) — HTTP ulanish uzoq ushlab
    turilmaydi (proxy timeout bo'lmaydi)
  - MAX_CONCURRENT_JOBS ta ish bir vaqtda bajariladi, qolgani navbatda
  - REAL progress: /status/{job_id} → pct + bosqich (llm/render/merge) +
    qaysi slayd render bo'lyapti — bot va veb-UI progress bar ko'rsatadi
  - foydalanuvchi ishni bekor qila oladi: POST /jobs/{job_id}/cancel
  - daqiqalik/kunlik limit foydalanuvchi bo'yicha + butun servis uchun
    umumiy limit (Gemini kvotasi + Chromium RAM himoyasi)
  - server xatosi bo'lsa foydalanuvchi limiti qaytariladi (refund)
  - tayyor fayllar TTL o'tgach avtomatik o'chiriladi

Diqqat: holat xotirada saqlanadi — uvicorn WORKERS=1 bo'lishi shart
(web.sh da shunday). Bir nechta worker/instansiya uchun jobs Redis'ga
ko'chirilishi kerak.
"""
import logging
import os
import re

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core import jobs
from app.core.limits import limiter
from app.core import referrals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("api")

app = FastAPI(title="Slide Bot API")

# Ichki mijozlar (Telegram bot) o'z foydalanuvchi identifikatorini bera oladi —
# aks holda hamma bot foydalanuvchilari bitta IP sifatida hisoblanardi.
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")

# Nechta reverse proxy qatlami orqali so'rov o'tishi (0 = proxy yo'q, to'g'ridan
# to'g'ri ulanish — X-Forwarded-For headeriga umuman ishonilmaydi, chunki uni
# mijoz o'zi yuborishi mumkin). nginx kabi bitta proxy ortida bo'lsa — 1.
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0") or "0")

_MEDIA_TYPES = {
    "pdf": "application/pdf",
}


def _slugify_filename(topic: str) -> str:
    """
    Mavzu matnidan xavfsiz fayl nomi yasaydi: papka ajratuvchilari, kavychalar
    va boshqa maxsus belgilarni olib tashlaydi, bo'sh joylarni pastki chiziqqa
    almashtiradi. Lotin va kiril harflari, raqamlar saqlanadi.
    """
    cleaned = re.sub(r"[^\w\s-]", "", topic, flags=re.UNICODE).strip()
    cleaned = re.sub(r"[\s]+", "_", cleaned)
    cleaned = cleaned[:80]
    return cleaned or "prezentatsiya"


def _client_key(request: Request) -> str:
    """
    Rate limiting kaliti. Ichki token bilan kelgan so'rov o'z identifikatorini
    belgilashi mumkin (bot foydalanuvchilari), aks holda IP ishlatiladi.

    XAVFSIZLIK: X-Forwarded-For — bu oddiy HTTP header, uni istalgan mijoz
    o'zi qalbakilashtirib yubora oladi (masalan har so'rovda tasodifiy IP
    yozib, rate limitni butunlay chetlab o'tish mumkin edi). Shu sababli bu
    headerga FAQAT TRUSTED_PROXY_COUNT muhit o'zgaruvchisi ochiq belgilangan
    bo'lsagina ishonamiz (ya'ni reverse proxy — masalan nginx/Cloudflare —
    borligi va nechta proxy qatlami borligi ma'lum bo'lsa). Aks holda har
    doim ulanishning haqiqiy socket IP manzili (request.client.host)
    ishlatiladi — buni mijoz soxtalashtira olmaydi.
    """
    if INTERNAL_TOKEN:
        token = request.headers.get("x-internal-token", "")
        client_id = request.headers.get("x-client-id", "")
        if token and token == INTERNAL_TOKEN and client_id:
            return client_id[:64]

    if TRUSTED_PROXY_COUNT > 0:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # Zanjirning oxiridan TRUSTED_PROXY_COUNT-chi qiymatni olamiz —
            # proxy(lar) qo'shgan qiymatlarga ishonamiz, lekin mijoz o'zi
            # header boshiga qo'shib yuborgan soxta qiymatlarga emas.
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if len(parts) >= TRUSTED_PROXY_COUNT:
                return "ip:" + parts[-TRUSTED_PROXY_COUNT][:45]

    return "ip:" + (request.client.host if request.client else "unknown")


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=300)
    slide_count: int = Field(..., ge=3, le=20)
    theme: str = Field("minimal", pattern="^(minimal|corporate|warm|forest)$")


class ReferralRequest(BaseModel):
    new_user_id: int
    referrer_id: int


def _require_internal(request: Request) -> None:
    """
    Referal endpoint'lari faqat ICHKI mijoz (Telegram bot) tomonidan
    chaqirilishi kerak — chunki bu yerda foydalanuvchi Telegram user_id'sini
    o'zi bemalol istagan qiymat qilib yuborishi mumkin bo'lsa, bonus tizimini
    suiiste'mol qilish oson bo'lardi. Shu sababli INTERNAL_TOKEN majburiy.
    """
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Referal tizimi sozlanmagan (INTERNAL_TOKEN yo'q).",
        )
    token = request.headers.get("x-internal-token", "")
    if token != INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")


@app.get("/")
def root():
    """Veb-UI yo'q — ilova faqat Telegram bot orqali ishlaydi."""
    return JSONResponse(
        {
            "service": "Slide Bot API",
            "message": "Bu backend Telegram bot (@vbnderbot) uchun ishlaydi",
            "endpoints": ["/health", "/generate", "/status/{job_id}", "/download/{job_id}"],
        }
    )


@app.get("/health")
def health():
    return {"status": "ok", **jobs.manager.stats()}


def _referral_bonus_for_request(request: Request) -> int:
    """
    Agar so'rov ichki mijoz (bot) tomonidan kelayotgan bo'lsa va x-user-id
    header bilan Telegram user_id berilgan bo'lsa — shu foydalanuvchining
    referal bonusini qaytaradi. Aks holda 0 (bonus yo'q, masalan veb orqali
    IP asosida kelgan so'rovlar uchun — ular hali botga /start bosmagan).
    """
    if not INTERNAL_TOKEN:
        return 0
    token = request.headers.get("x-internal-token", "")
    if token != INTERNAL_TOKEN:
        return 0
    raw_uid = request.headers.get("x-user-id", "")
    try:
        uid = int(raw_uid)
    except (TypeError, ValueError):
        return 0
    return referrals.store.bonus_for(uid)


@app.get("/limits")
def limits(request: Request):
    bonus = _referral_bonus_for_request(request)
    return {
        "limits": {
            "per_minute": limiter.per_minute,
            "per_hour": limiter.per_hour,
            "per_day": limiter.per_day,
            "global_per_minute": limiter.global_per_minute,
            "global_per_day": limiter.global_per_day,
        },
        "referral_bonus": bonus,
        "remaining": limiter.remaining(_client_key(request), extra_daily=bonus),
        "queue": jobs.manager.stats(),
    }


@app.post("/referrals/register")
def register_referral(req: ReferralRequest, request: Request):
    """
    Yangi foydalanuvchi referal havolasi orqali botga kirganda bot bu
    endpoint'ni chaqiradi. Faqat ichki (bot) so'rovlar qabul qilinadi.
    """
    _require_internal(request)
    granted = referrals.store.register_referral(
        new_user_id=req.new_user_id, referrer_id=req.referrer_id
    )
    return {
        "granted": granted,
        "referrer_bonus": referrals.store.bonus_for(req.referrer_id),
        "referrer_invite_count": referrals.store.invite_count(req.referrer_id),
    }


@app.get("/referrals/{user_id}")
def referral_status(user_id: int, request: Request):
    """Shu foydalanuvchining joriy referal statistikasi (bot /referal uchun)."""
    _require_internal(request)
    return {
        "invite_count": referrals.store.invite_count(user_id),
        "bonus": referrals.store.bonus_for(user_id),
        "bonus_max": referrals.BONUS_MAX,
        "bonus_per_invite": referrals.BONUS_PER_INVITE,
    }


@app.post("/generate", status_code=202)
def generate(req: GenerateRequest, request: Request, response: Response):
    """
    So'rovni navbatga qo'yadi va darhol job_id qaytaradi (202 Accepted).
    Holatni /status/{job_id} orqali kuzatib boring.
    """
    key = _client_key(request)
    bonus = _referral_bonus_for_request(request)

    allowed, reason, retry_after = limiter.check(key, extra_daily=bonus)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=reason,
            headers={"Retry-After": str(min(retry_after, 86400))},
        )

    display_name = f"{_slugify_filename(req.topic)}.pdf"

    try:
        job = jobs.manager.submit(
            topic=req.topic,
            slide_count=req.slide_count,
            theme=req.theme,
            owner=key,
            display_name=display_name,
        )
    except jobs.QueueFull as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "60"})

    limiter.record(key)
    _job, position = jobs.manager.get(job.job_id)
    payload = job.public(position)
    payload["filename"] = display_name
    payload["remaining"] = limiter.remaining(key, extra_daily=bonus)
    response.headers["Location"] = f"/status/{job.job_id}"
    logger.info(
        "Yangi job %s (pdf) — %s slayd, theme=%s, owner=%s",
        job.job_id[:8], req.slide_count, req.theme, key,
    )
    return payload


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    """Navbatdagi/bajarilayotgan ishni bekor qiladi."""
    job = jobs.manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bunday so'rov topilmadi")
    return {"job_id": job_id, "status": job.status}


@app.get("/status/{job_id}")
def status(job_id: str):
    job, position = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bunday so'rov topilmadi (yoki muddati o'tgan)")
    return job.public(position)


@app.get("/download/{job_id}")
def download(job_id: str):
    job, _pos = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Fayl topilmadi (muddati o'tgan bo'lishi mumkin)")
    if job.status == "cancelled":
        raise HTTPException(status_code=410, detail="Ish bekor qilingan")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Fayl hali tayyor emas (holat: {job.status})")
    if not os.path.exists(job.path):
        raise HTTPException(status_code=404, detail="Fayl diskda topilmadi (tozalangan)")
    return FileResponse(
        job.path,
        media_type=_MEDIA_TYPES.get(job.output_format, "application/octet-stream"),
        filename=job.filename,
    )
