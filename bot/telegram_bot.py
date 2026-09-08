"""
Telegram bot: mavzu → tayyor PDF taqdimot.

Long-polling (getUpdates) asosida ishlaydi — webhook, ochiq port yoki HTTPS
sertifikat kerak emas. Bot backend'ning HTTP API'sini chaqiradi:

  POST /generate            → 202 {job_id, remaining} (navbatga tushadi)
  GET  /status/{job_id}     → holat + REAL progress (pct, stage, slayd i/n)
  GET  /limits              → foydalanuvchi limitlari (referal bonusi bilan)
  POST /jobs/{job_id}/cancel — ishni bekor qilish
  GET  /download/{job_id}   → tayyor PDF
  POST /referrals/register  → do'st referal havolasi orqali kirganda qayd etish
  GET  /referrals/{uid}     → foydalanuvchining referal statistikasi

UX: ish davomida xabar jonli progress bar bilan yangilanadi (navbat holati,
bosqich, foiz, qolgan vaqt), "❌ Bekor qilish" tugmasi bor. Tugagach
"🔄 Qayta yaratish" va "⚙️ Sozlamalar" tugmalari chiqadi. Barcha menyular
inline tugmalar orqali. Bot ikki tilda ishlaydi: o'zbek (lotin) va rus.

Ishga tushirish:
    export TELEGRAM_BOT_TOKEN=...            # @BotFather
    export API_BASE=http://127.0.0.1:8003    # backend manzili
    export INTERNAL_TOKEN=...                # per-user limit va referal uchun
    export BOT_USERNAME=...                  # referal havolasi uchun (masalan vbnderbot)
    python -m bot.telegram_bot
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import time
import traceback
from typing import Any

import httpx

logger = logging.getLogger("tgbot")


def _normalize_base(raw: str) -> str:
    """
    API_BASE ni normallashtiradi. Render'ning `property: hostport` qiymati
    sxemasiz ("slide-backend:8000") keladi — o'sha holatda http:// qo'shamiz,
    aks holda httpx "unsupported protocol" xatosi beradi.
    """
    base = (raw or "http://127.0.0.1:8003").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base


API_BASE = _normalize_base(os.environ.get("API_BASE", ""))
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
# Referal havolasi https://t.me/<username>?start=ref_<uid> ko'rinishida
# quriladi — shuning uchun bot o'z username'ini bilishi kerak. Bo'sh
# qoldirilsa, getMe orqali ishga tushishda avtomatik olinadi.
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")

# Telegram bot API orqali yuborish chegarasi
MAX_SEND_BYTES = 50 * 1024 * 1024

# Progress bar uzunligi (belgi)
BAR_LEN = 14

Lang = str  # "uz" | "ru"
DEFAULT_LANG: Lang = "uz"


# ============================================================================
# Ko'p tillilik: barcha foydalanuvchiga ko'rinadigan matnlar shu yerda,
# bir joyda. Yangi til qo'shish uchun shunchaki yangi kalit (masalan "en")
# qo'shish kifoya — kod boshqa hech qayerda o'zgarmaydi.
# ============================================================================

STAGE_LABELS: dict[Lang, dict[str, str]] = {
    "uz": {
        "llm": "🤖 Matn tayyorlanmoqda",
        "render": "🎨 Slaydlar chizilmoqda",
        "merge": "📦 PDF yig'ilmoqda",
    },
    "ru": {
        "llm": "🤖 Готовится текст",
        "render": "🎨 Отрисовка слайдов",
        "merge": "📦 Сборка PDF",
    },
}

THEMES: dict[Lang, dict[str, str]] = {
    "uz": {
        "minimal": "Minimal (qora urg'u)",
        "corporate": "Corporate (ko'k)",
        "warm": "Warm (to'q sariq)",
        "forest": "Forest (yashil)",
    },
    "ru": {
        "minimal": "Minimal (чёрный акцент)",
        "corporate": "Corporate (синий)",
        "warm": "Warm (оранжевый)",
        "forest": "Forest (зелёный)",
    },
}

SLIDE_CHOICES = [5, 8, 10, 15, 20]

MIN_TOPIC_LEN = 2
MAX_TOPIC_LEN = 300

# Referal sozlamalari matnda ko'rsatish uchun (haqiqiy hisob-kitob backendda,
# bu yerda faqat ko'rsatiladigan sonlar — .env bilan mos kelishi kerak).
REFERRAL_BONUS_PER_INVITE = int(os.environ.get("REFERRAL_BONUS_PER_INVITE", "2") or "2")


def _t(lang: Lang, uz: str, ru: str) -> str:
    return ru if lang == "ru" else uz


TEXTS: dict[str, dict[Lang, str]] = {
    "welcome_title": {
        "uz": "✨ <b>Slide — sun'iy intellektli taqdimot generatori</b>",
        "ru": "✨ <b>Slide — генератор презентаций на базе ИИ</b>",
    },
    "welcome_body": {
        "uz": (
            "Menga mavzuni yozib yuboring — bir necha soniyada chuqur, "
            "mazmunli va darsda yoki himoyada taqdim qilishga tayyor "
            "<b>PDF taqdimot</b> qaytaraman.\n\n"
            "📝 Masalan: <i>Sun'iy intellekt tibbiyotda</i>"
        ),
        "ru": (
            "Просто напишите тему — через несколько секунд пришлю "
            "содержательную <b>PDF-презентацию</b>, готовую для пары или "
            "защиты.\n\n"
            "📝 Например: <i>Искусственный интеллект в медицине</i>"
        ),
    },
    "help_title": {
        "uz": "🎯 <b>Slide — AI taqdimot generatori</b>",
        "ru": "🎯 <b>Slide — генератор презентаций на ИИ</b>",
    },
    "help_body": {
        "uz": (
            "Ishlatish juda oddiy: mavzuni yozasiz, men tayyor PDF "
            "taqdimotni yuboraman. Ish davomida progress bar ko'rinib "
            "turadi, istasangiz <b>❌ Bekor qilish</b> tugmasi bilan "
            "to'xtatishingiz mumkin."
        ),
        "ru": (
            "Всё просто: пишете тему — получаете готовую PDF-презентацию. "
            "Во время генерации виден прогресс-бар, в любой момент можно "
            "нажать <b>❌ Отменить</b>."
        ),
    },
    "help_commands": {
        "uz": (
            "Komandalar:\n"
            "  /start — asosiy menyu\n"
            "  /sozlama — slayd soni va dizayn\n"
            "  /referal — do'stlarni taklif qilib bonus oling\n"
            "  /til — tilni almashtirish\n"
            "  /help — bu yordam"
        ),
        "ru": (
            "Команды:\n"
            "  /start — главное меню\n"
            "  /sozlama — количество слайдов и дизайн\n"
            "  /referal — пригласить друзей и получить бонус\n"
            "  /til — сменить язык\n"
            "  /help — эта справка"
        ),
    },
    "current_settings": {
        "uz": "Hozirgi sozlamalar",
        "ru": "Текущие настройки",
    },
    "quota_line": {
        "uz": "📊 Bugun sizda: <b>{left}/{total} ta</b> taqdimot yaratish imkoniyati qoldi",
        "ru": "📊 Сегодня доступно: <b>{left}/{total}</b> генераций",
    },
    "quota_line_unlimited": {
        "uz": "📊 Bugungi limit: cheklanmagan",
        "ru": "📊 Лимит на сегодня: без ограничений",
    },
    "quota_hint_referral": {
        "uz": "💡 Do'st taklif qiling — har biri uchun +{bonus} ta qo'shimcha limit! /referal",
        "ru": "💡 Приглашайте друзей — за каждого +{bonus} к лимиту! /referal",
    },
    "settings_title": {
        "uz": "⚙️ <b>Sozlamalar</b>",
        "ru": "⚙️ <b>Настройки</b>",
    },
    "settings_change_hint": {
        "uz": "O'zgartirish uchun tugmani bosing:",
        "ru": "Нажмите кнопку, чтобы изменить:",
    },
    "topic_too_short": {
        "uz": "Mavzu juda qisqa. Kamida 2 belgi yozing.",
        "ru": "Тема слишком короткая. Введите хотя бы 2 символа.",
    },
    "topic_too_long": {
        "uz": "Mavzu juda uzun ({n} belgi). {max} belgidan oshmasin.",
        "ru": "Тема слишком длинная ({n} симв.). Не более {max} символов.",
    },
    "topic_only_text": {
        "uz": "Faqat matnli mavzu qabul qilaman. Masalan: Yashil energiya O'zbekistonda",
        "ru": "Принимаю только текстовую тему. Например: Зелёная энергетика в Узбекистане",
    },
    "busy": {
        "uz": (
            "⏳ Sizning oldingi so'rovingiz hali bajarilmoqda. "
            "Tayyor bo'lishini kuting yoki bekor qiling."
        ),
        "ru": (
            "⏳ Ваш предыдущий запрос ещё выполняется. "
            "Дождитесь завершения или отмените его."
        ),
    },
    "queue_position": {
        "uz": "⏳ Navbatda: oldingizda {pos} ta so'rov\nTaxminiy kutish: {eta}",
        "ru": "⏳ В очереди: перед вами {pos} запрос(ов)\nОжидание: ~{eta}",
    },
    "queue_waiting": {
        "uz": "⏳ Navbatda… Taxminiy kutish: {eta}",
        "ru": "⏳ В очереди… Ожидание: ~{eta}",
    },
    "eta_remaining": {
        "uz": "\n⏱ Qolgan vaqt: ~{eta}",
        "ru": "\n⏱ Осталось: ~{eta}",
    },
    "finalizing": {
        "uz": "⚙️ Yakunlanmoqda…",
        "ru": "⚙️ Завершается…",
    },
    "sending_file": {
        "uz": "📤 Fayl yuborilmoqda…",
        "ru": "📤 Отправка файла…",
    },
    "cancelled": {
        "uz": "❌ Bekor qilindi.",
        "ru": "❌ Отменено.",
    },
    "took_too_long": {
        "uz": "⌛️ Juda uzoq davom etdi. Keyinroq qayta urinib ko'ring.",
        "ru": "⌛️ Это заняло слишком много времени. Попробуйте позже.",
    },
    "file_not_sent": {
        "uz": "❌ Faylni yuborib bo'lmadi. Qayta urinib ko'ring.",
        "ru": "❌ Не удалось отправить файл. Попробуйте ещё раз.",
    },
    "file_too_big": {
        "uz": (
            "❌ Fayl juda katta ({mb:.1f} MB). "
            "Slayd sonini kamaytirib ko'ring."
        ),
        "ru": (
            "❌ Файл слишком большой ({mb:.1f} МБ). "
            "Попробуйте уменьшить число слайдов."
        ),
    },
    "file_download_failed": {
        "uz": "❌ Fayl yuklanmadi: {err}",
        "ru": "❌ Не удалось загрузить файл: {err}",
    },
    "done_caption": {
        "uz": "✅ Tayyor — {slides} slayd, {took} s",
        "ru": "✅ Готово — {slides} слайдов, {took} с",
    },
    "done_ready": {
        "uz": "✅ <b>Tayyor</b> ({took} s, {size})",
        "ru": "✅ <b>Готово</b> ({took} с, {size})",
    },
    "error_prefix": {
        "uz": "❌ Xatolik: {err}",
        "ru": "❌ Ошибка: {err}",
    },
    "not_accepted": {
        "uz": "❌ So'rov qabul qilinmadi: {detail}",
        "ru": "❌ Запрос не принят: {detail}",
    },
    "limit_reached": {
        "uz": "🚫 {detail}",
        "ru": "🚫 {detail}",
    },
    "server_busy": {
        "uz": "🕒 {detail}",
        "ru": "🕒 {detail}",
    },
    "unknown_command": {
        "uz": "Noma'lum komanda. /help — yordam.",
        "ru": "Неизвестная команда. /help — справка.",
    },
    "cancelling": {
        "uz": "⏹ Bekor qilinyapti…",
        "ru": "⏹ Отменяется…",
    },
    "cancel_failed": {
        "uz": "❌ Ishnni bekor qilib bo'lmadi: {detail}",
        "ru": "❌ Не удалось отменить запрос: {detail}",
    },
    "topic_not_found": {
        "uz": "Mavzu topilmadi — yangi mavzu yozing",
        "ru": "Тема не найдена — введите новую тему",
    },
    "regenerating": {
        "uz": "🔄 Qayta yaratilmoqda…",
        "ru": "🔄 Пересоздаётся…",
    },
    "referral_title": {
        "uz": "🎁 <b>Do'stlaringizni taklif qiling</b>",
        "ru": "🎁 <b>Приглашайте друзей</b>",
    },
    "referral_body": {
        "uz": (
            "Har bir do'stingiz sizning havolangiz orqali botga birinchi "
            "marta kirsa — kunlik limitingizga <b>+{bonus} ta</b> "
            "qo'shimcha imkoniyat qo'shiladi!\n\n"
            "🔗 Sizning havolangiz:\n<code>{link}</code>\n\n"
            "👥 Hozirgacha taklif qilganlar: <b>{count} kishi</b>\n"
            "🎯 Joriy bonus: <b>+{current_bonus} ta/kun</b>"
        ),
        "ru": (
            "Когда друг впервые заходит в бота по вашей ссылке — вы "
            "получаете <b>+{bonus}</b> к дневному лимиту!\n\n"
            "🔗 Ваша ссылка:\n<code>{link}</code>\n\n"
            "👥 Уже приглашено: <b>{count}</b>\n"
            "🎯 Текущий бонус: <b>+{current_bonus}/день</b>"
        ),
    },
    "referral_share_button": {
        "uz": "📤 Do'stlarga yuborish",
        "ru": "📤 Отправить другу",
    },
    "referral_share_text": {
        "uz": (
            "🎯 Men Slide botidan foydalanyapman — mavzuni yozib yuborsang, "
            "bir necha soniyada tayyor, chiroyli PDF taqdimot qaytaradi! "
            "Universitet uchun juda qulay.\n\n"
            "Sinab ko'r: {link}"
        ),
        "ru": (
            "🎯 Пользуюсь ботом Slide — пишешь тему, а он за секунды "
            "присылает готовую красивую PDF-презентацию! Очень удобно для "
            "учёбы.\n\n"
            "Попробуй: {link}"
        ),
    },
    "referral_welcome_bonus": {
        "uz": "🎉 Do'stingiz havolasi orqali kirdingiz — u kunlik limitiga bonus oldi. Xush kelibsiz!",
        "ru": "🎉 Вы пришли по ссылке друга — он получил бонус к лимиту. Добро пожаловать!",
    },
    "lang_choose": {
        "uz": "🌐 Tilni tanlang:",
        "ru": "🌐 Выберите язык:",
    },
    "lang_set": {
        "uz": "✅ Til o'zbek tiliga o'zgartirildi.",
        "ru": "✅ Язык изменён на русский.",
    },
}


def L(key: str, lang: Lang, **kwargs: object) -> str:
    """TEXTS lug'atidan tarjima olib, mavjud bo'lsa .format() qiladi."""
    template = TEXTS[key].get(lang) or TEXTS[key][DEFAULT_LANG]
    return template.format(**kwargs) if kwargs else template


def _bar(pct: int) -> str:
    """██████░░░░ ko'rinishidagi progress bar."""
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 100 * BAR_LEN)
    return "▓" * filled + "░" * (BAR_LEN - filled)


def _fmt_size(n: int) -> str:
    """Fayl hajmini chiroyli ko'rsatadi: 47 KB / 1.2 MB."""
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1e6:.1f} MB"


def _fmt_secs(secs: int, lang: Lang) -> str:
    """Sekundlarni ixcham ko'rsatadi: 45 s / 2 daq 10 s / 1 soat."""
    secs = max(0, int(secs))
    if lang == "ru":
        if secs < 60:
            return f"{secs} с"
        if secs < 3600:
            return f"{secs // 60} мин {secs % 60:02d} с"
        return f"{secs // 3600} ч {(secs % 3600) // 60:02d} мин"
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} daq {secs % 60:02d} s"
    return f"{secs // 3600} soat {(secs % 3600) // 60:02d} daq"


class Prefs:
    """Foydalanuvchi tanlovlari (xotirada; restartda standartga qaytadi)."""

    __slots__ = ("slides", "theme", "lang")

    def __init__(self) -> None:
        self.slides = 8
        self.theme = "minimal"
        self.lang: Lang = DEFAULT_LANG

    def summary(self) -> str:
        theme_label = THEMES[self.lang][self.theme].split(" (")[0]
        return f"{self.slides} · PDF · {theme_label}"


class SlideBot:
    def __init__(self) -> None:
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not self._token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN topilmadi. .env faylida yoki muhit "
                "o'zgaruvchisida bering (@BotFather dan olinadi)."
            )
        self._api = f"https://api.telegram.org/bot{self._token}"
        self._offset = 0
        self._http = httpx.AsyncClient(timeout=60.0)
        self._prefs: dict[int, Prefs] = {}
        # Bitta foydalanuvchi bir vaqtda faqat bitta ish yuborishi mumkin —
        # bot spam qilinishining eng oddiy oldini olish usuli.
        self._busy: set[int] = set()
        # Oxirgi mavzu — "🔄 Qayta yaratish" tugmasi uchun
        self._last_topic: dict[int, str] = {}
        self._username = BOT_USERNAME

    # ------------------------------------------------------------------ #
    # Telegram API
    # ------------------------------------------------------------------ #
    async def _call(self, method: str, **kwargs: Any) -> Any:
        resp = await self._http.post(f"{self._api}/{method}", json=kwargs)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API xatosi ({method}): {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> int | None:
        try:
            payload: dict[str, Any] = {
                "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            }
            if keyboard:
                payload["reply_markup"] = keyboard
            msg = await self._call("sendMessage", **payload)
            return msg.get("message_id")
        except Exception as exc:  # noqa: BLE001
            logger.warning("sendMessage xato (%s): %s", chat_id, exc)
            return None

    async def edit(self, chat_id: int, message_id: int, text: str, keyboard: dict | None = None) -> None:
        try:
            payload: dict[str, Any] = {
                "chat_id": chat_id, "message_id": message_id, "text": text,
                "parse_mode": "HTML",
            }
            if keyboard is not None:
                payload["reply_markup"] = keyboard
            await self._call("editMessageText", **payload)
        except Exception as exc:  # noqa: BLE001
            # "message is not modified" normal holat — logni shovqinlantirmaymiz
            if "not modified" not in str(exc):
                logger.debug("editMessageText xato: %s", exc)

    async def answer_callback(self, cb_id: str, text: str = "") -> None:
        try:
            await self._call("answerCallbackQuery", callback_query_id=cb_id, text=text)
        except Exception:  # noqa: BLE001
            pass

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> bool:
        """Faylni yuboradi. Transient tarmoq xatolarida 2 marta qayta urinadi."""
        last_exc: Exception | None = None
        for attempt in range(1, 3):
            try:
                resp = await self._http.post(
                    f"{self._api}/sendDocument",
                    data={"chat_id": str(chat_id), "caption": caption[:1000]},
                    files={"document": (filename, io.BytesIO(data))},
                    timeout=300.0,
                )
                body = resp.json()
                if not body.get("ok"):
                    logger.warning(
                        "sendDocument rad etildi (urinish %d): HTTP %s — %s",
                        attempt, resp.status_code, body.get("description"),
                    )
                    return False
                return True
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "sendDocument xato (%s, urinish %d): %r — %s",
                    chat_id, attempt, exc,
                    "".join(traceback.format_exception_only(type(exc), exc)).strip(),
                )
                if attempt < 2:
                    await asyncio.sleep(2.0 * attempt)
        logger.error("sendDocument 2 urinishda ham muvaffaqiyatsiz (%s): %r",
                     chat_id, last_exc)
        return False

    # ------------------------------------------------------------------ #
    # Klaviaturalar va matnlar
    # ------------------------------------------------------------------ #
    def _settings_keyboard(self, p: Prefs) -> dict:
        def mark(active: bool, label: str) -> str:
            return ("✅ " if active else "") + label

        lang = p.lang
        themes = THEMES[lang]
        home_label = _t(lang, "🏠 Asosiy menyu", "🏠 Главное меню")
        kb = {
            "inline_keyboard": [
                [
                    {"text": mark(p.slides == n, str(n)), "callback_data": f"slides:{n}"}
                    for n in (5, 8, 10)
                ],
                [
                    {"text": mark(p.slides == n, str(n)), "callback_data": f"slides:{n}"}
                    for n in (15, 20)
                ],
                [
                    {"text": mark(p.theme == "minimal", themes["minimal"].split(" (")[0]), "callback_data": "theme:minimal"},
                    {"text": mark(p.theme == "corporate", themes["corporate"].split(" (")[0]), "callback_data": "theme:corporate"},
                ],
                [
                    {"text": mark(p.theme == "warm", themes["warm"].split(" (")[0]), "callback_data": "theme:warm"},
                    {"text": mark(p.theme == "forest", themes["forest"].split(" (")[0]), "callback_data": "theme:forest"},
                ],
                [{"text": home_label, "callback_data": "menu:main"}],
            ]
        }
        return kb

    def _main_keyboard(self, lang: Lang) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": _t(lang, "⚙️ Sozlamalar", "⚙️ Настройки"), "callback_data": "menu:settings"},
                    {"text": _t(lang, "🎁 Referal", "🎁 Реферал"), "callback_data": "menu:referral"},
                ],
                [
                    {"text": _t(lang, "❓ Yordam", "❓ Помощь"), "callback_data": "menu:help"},
                    {"text": "🌐 uz / ru", "callback_data": "menu:lang"},
                ],
            ]
        }

    def _lang_keyboard(self) -> dict:
        return {
            "inline_keyboard": [[
                {"text": "🇺🇿 O'zbekcha", "callback_data": "lang:uz"},
                {"text": "🇷🇺 Русский", "callback_data": "lang:ru"},
            ]]
        }

    def _cancel_keyboard(self, job_id: str, lang: Lang) -> dict:
        return {
            "inline_keyboard": [[
                {"text": _t(lang, "❌ Bekor qilish", "❌ Отменить"), "callback_data": f"cancel:{job_id}"},
            ]]
        }

    def _done_keyboard(self, lang: Lang) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": _t(lang, "🔄 Qayta yaratish", "🔄 Создать заново"), "callback_data": "again"},
                    {"text": _t(lang, "⚙️ Sozlamalar", "⚙️ Настройки"), "callback_data": "menu:settings"},
                ],
            ]
        }

    def _referral_keyboard(self, lang: Lang, link: str) -> dict:
        share_text = L("referral_share_text", lang, link=link)
        share_url = "https://t.me/share/url?" + httpx.QueryParams(
            {"url": link, "text": share_text}
        ).__str__()
        return {
            "inline_keyboard": [
                [{"text": L("referral_share_button", lang), "url": share_url}],
                [{"text": _t(lang, "🏠 Asosiy menyu", "🏠 Главное меню"), "callback_data": "menu:main"}],
            ]
        }

    def prefs(self, uid: int) -> Prefs:
        return self._prefs.setdefault(uid, Prefs())

    def _referral_link(self, uid: int) -> str:
        uname = self._username or "your_bot"
        return f"https://t.me/{uname}?start=ref_{uid}"

    def _quota_text(self, lang: Lang, remaining: dict, bonus: int) -> str:
        left = remaining.get("day_left", -1)
        total = remaining.get("day_limit", -1)
        if left is None or left < 0 or total is None or total <= 0:
            base = L("quota_line_unlimited", lang)
        else:
            base = L("quota_line", lang, left=left, total=total)
        hint = ""
        if bonus < 10:  # hali maksimal bonusga yetmagan bo'lsa taklif qilamiz
            hint = "\n" + L("quota_hint_referral", lang, bonus=REFERRAL_BONUS_PER_INVITE)
        return base + hint

    def _welcome_text(self, p: Prefs, quota_text: str = "") -> str:
        lang = p.lang
        parts = [
            L("welcome_title", lang),
            "",
            L("welcome_body", lang),
        ]
        if quota_text:
            parts += ["", quota_text]
        parts += [
            "",
            f"{L('current_settings', lang)}: <b>{p.summary()}</b>",
        ]
        return "\n".join(parts)

    def _help_text(self, p: Prefs) -> str:
        lang = p.lang
        return "\n\n".join([
            L("help_title", lang),
            L("help_body", lang),
            f"{L('current_settings', lang)}: <b>{p.summary()}</b>",
            L("help_commands", lang),
        ])

    def _settings_text(self, p: Prefs) -> str:
        lang = p.lang
        return (
            f"{L('settings_title', lang)}\n\n"
            f"{L('current_settings', lang)}: {p.summary()}\n\n"
            f"{L('settings_change_hint', lang)}"
        )

    # ------------------------------------------------------------------ #
    # Backend API
    # ------------------------------------------------------------------ #
    def _headers(self, uid: int) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if INTERNAL_TOKEN:
            h["X-Internal-Token"] = INTERNAL_TOKEN
            h["X-Client-Id"] = f"tg:{uid}"
            h["X-User-Id"] = str(uid)
        return h

    async def _api_generate(self, uid: int, topic: str, p: Prefs) -> tuple[int, dict]:
        try:
            r = await self._http.post(
                f"{API_BASE}/generate",
                json={
                    "topic": topic,
                    "slide_count": p.slides,
                    "theme": p.theme,
                },
                headers=self._headers(uid),
                timeout=30.0,
            )
            try:
                return r.status_code, r.json()
            except Exception:  # noqa: BLE001
                return r.status_code, {"detail": r.text[:300]}
        except Exception as exc:  # noqa: BLE001
            return 0, {"detail": f"Serverga ulanib bo'lmadi: {exc}"}

    async def _api_status(self, job_id: str) -> dict:
        r = await self._http.get(f"{API_BASE}/status/{job_id}", timeout=20.0)
        if r.status_code != 200:
            raise RuntimeError(f"status {r.status_code}")
        return r.json()

    async def _api_cancel(self, job_id: str) -> tuple[int, dict]:
        try:
            r = await self._http.post(f"{API_BASE}/jobs/{job_id}/cancel", timeout=20.0)
            try:
                return r.status_code, r.json()
            except Exception:  # noqa: BLE001
                return r.status_code, {"detail": r.text[:200]}
        except Exception as exc:  # noqa: BLE001
            return 0, {"detail": str(exc)}

    async def _api_download(self, job_id: str) -> bytes:
        r = await self._http.get(f"{API_BASE}/download/{job_id}", timeout=300.0)
        r.raise_for_status()
        return r.content

    async def _api_limits(self, uid: int) -> dict | None:
        try:
            r = await self._http.get(
                f"{API_BASE}/limits", headers=self._headers(uid), timeout=15.0
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("limits olinmadi (%s): %s", uid, exc)
        return None

    async def _api_register_referral(self, new_uid: int, referrer_uid: int) -> dict | None:
        if not INTERNAL_TOKEN:
            return None
        try:
            r = await self._http.post(
                f"{API_BASE}/referrals/register",
                json={"new_user_id": new_uid, "referrer_id": referrer_uid},
                headers={"X-Internal-Token": INTERNAL_TOKEN, "Content-Type": "application/json"},
                timeout=15.0,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("referral register xato: %s", exc)
        return None

    async def _api_referral_status(self, uid: int) -> dict | None:
        if not INTERNAL_TOKEN:
            return None
        try:
            r = await self._http.get(
                f"{API_BASE}/referrals/{uid}",
                headers={"X-Internal-Token": INTERNAL_TOKEN},
                timeout=15.0,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("referral status xato: %s", exc)
        return None

    # ------------------------------------------------------------------ #
    # Progress xabari matni
    # ------------------------------------------------------------------ #
    def _progress_text(self, head: str, st: dict, lang: Lang) -> str:
        """Navbat/progress holatidan chiroyli matn yasaydi."""
        status = st.get("status")
        if status == "queued":
            pos = st.get("queue_position", 0)
            eta = _fmt_secs(st.get("eta_seconds", 0), lang)
            if pos:
                body = L("queue_position", lang, pos=pos, eta=eta)
            else:
                body = L("queue_waiting", lang, eta=eta)
        elif status == "running":
            pr = st.get("progress") or {}
            pct = pr.get("pct", 0)
            stage = pr.get("stage", "")
            slide, total = pr.get("slide", 0), pr.get("total_slides", 0)
            stage_txt = STAGE_LABELS.get(lang, STAGE_LABELS["uz"]).get(
                stage, _t(lang, "⚙️ Tayyorlanmoqda", "⚙️ Подготовка")
            )
            slide_txt = f" ({slide}/{total})" if total else ""
            eta = st.get("eta_seconds")
            eta_txt = L("eta_remaining", lang, eta=_fmt_secs(eta, lang)) if eta else ""
            body = (
                f"{_bar(pct)} <b>{pct}%</b>\n"
                f"{stage_txt}{slide_txt}{eta_txt}"
            )
        else:  # done/error/cancelled — oxirgi kadr
            body = L("finalizing", lang)
        return f"{head}\n\n{body}"

    # ------------------------------------------------------------------ #
    # Asosiy oqim: mavzu → taqdimot
    # ------------------------------------------------------------------ #
    async def _handle_topic(self, chat_id: int, uid: int, topic: str) -> None:
        p = self.prefs(uid)
        lang = p.lang

        if uid in self._busy:
            await self.send(chat_id, L("busy", lang))
            return

        topic = topic.strip()
        if len(topic) < MIN_TOPIC_LEN:
            await self.send(chat_id, L("topic_too_short", lang))
            return
        if len(topic) > MAX_TOPIC_LEN:
            await self.send(
                chat_id, L("topic_too_long", lang, n=len(topic), max=MAX_TOPIC_LEN)
            )
            return

        self._busy.add(uid)
        self._last_topic[uid] = topic
        try:
            code, data = await self._api_generate(uid, topic, p)

            if code == 429:
                await self.send(
                    chat_id, L("limit_reached", lang, detail=html.escape(str(data.get("detail", ""))))
                )
                return
            if code == 503:
                await self.send(
                    chat_id, L("server_busy", lang, detail=html.escape(str(data.get("detail", ""))))
                )
                return
            if code not in (200, 202):
                detail = data.get("detail")
                if isinstance(detail, list) and detail:
                    detail = detail[0].get("msg", str(detail))
                await self.send(
                    chat_id, L("not_accepted", lang, detail=html.escape(str(detail)))
                )
                return

            job_id = data["job_id"]
            head = f"📝 <b>{html.escape(topic[:120])}</b>\n{p.summary()}"

            msg_id = await self.send(
                chat_id,
                self._progress_text(head, data, lang),
                self._cancel_keyboard(job_id, lang),
            )

            deadline = time.time() + 15 * 60
            last_text = ""
            last_edit = 0.0
            st: dict = data

            while time.time() < deadline:
                await asyncio.sleep(2.5)
                try:
                    st = await self._api_status(job_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("status olinmadi: %s", exc)
                    continue

                if st.get("status") in ("done", "error", "cancelled"):
                    break

                # Progress xabarini yangilash (har 3 soniyada, matn o'zgarsa)
                text = self._progress_text(head, st, lang)
                now = time.time()
                if text != last_text and msg_id and (now - last_edit >= 3.0):
                    await self.edit(chat_id, msg_id, text, self._cancel_keyboard(job_id, lang))
                    last_text = text
                    last_edit = now

            if st.get("status") == "done":
                if msg_id:
                    await self.edit(chat_id, msg_id, f"{head}\n\n{L('sending_file', lang)}")
            elif st.get("status") == "cancelled":
                if msg_id:
                    await self.edit(chat_id, msg_id, f"{head}\n\n{L('cancelled', lang)}")
                else:
                    await self.send(chat_id, L("cancelled", lang))
                return
            elif st.get("status") == "error":
                err = st.get("error", "")
                body = f"{head}\n\n{L('error_prefix', lang, err=html.escape(err[:300]))}"
                if msg_id:
                    await self.edit(chat_id, msg_id, body, self._done_keyboard(lang))
                else:
                    await self.send(chat_id, body, self._done_keyboard(lang))
                return
            else:
                if msg_id:
                    await self.edit(chat_id, msg_id, f"{head}\n\n{L('took_too_long', lang)}")
                return

            try:
                blob = await self._api_download(job_id)
            except Exception as exc:  # noqa: BLE001
                await self.edit(
                    chat_id, msg_id or 0,
                    f"{head}\n\n{L('file_download_failed', lang, err=html.escape(str(exc)))}",
                )
                return

            if len(blob) > MAX_SEND_BYTES:
                await self.edit(
                    chat_id, msg_id or 0,
                    f"{head}\n\n{L('file_too_big', lang, mb=len(blob) / 1e6)}",
                )
                return

            filename = st.get("filename") or data.get("filename") or "taqdimot.pdf"
            took = st.get("took_seconds", "?")
            ok = await self.send_document(
                chat_id, blob, filename,
                caption=L("done_caption", lang, slides=p.slides, took=took),
            )
            if ok and msg_id:
                await self.edit(
                    chat_id, msg_id,
                    f"{head}\n\n{L('done_ready', lang, took=took, size=_fmt_size(len(blob)))}",
                    self._done_keyboard(lang),
                )
            elif not ok:
                await self.send(chat_id, L("file_not_sent", lang))
        finally:
            self._busy.discard(uid)

    # ------------------------------------------------------------------ #
    # Komandalar / callback
    # ------------------------------------------------------------------ #
    async def _show_referral(self, chat_id: int, uid: int) -> None:
        p = self.prefs(uid)
        lang = p.lang
        status = await self._api_referral_status(uid)
        count = status.get("invite_count", 0) if status else 0
        current_bonus = status.get("bonus", 0) if status else 0
        link = self._referral_link(uid)
        text = (
            f"{L('referral_title', lang)}\n\n"
            + L(
                "referral_body", lang,
                bonus=REFERRAL_BONUS_PER_INVITE, link=link,
                count=count, current_bonus=current_bonus,
            )
        )
        await self.send(chat_id, text, self._referral_keyboard(lang, link))

    async def _handle_start(self, chat_id: int, uid: int, text: str) -> None:
        p = self.prefs(uid)

        # Referal payload: /start ref_<referrer_id>
        parts = text.strip().split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        welcome_extra = ""
        if payload.startswith("ref_"):
            ref_id_raw = payload[4:]
            try:
                referrer_id = int(ref_id_raw)
                result = await self._api_register_referral(uid, referrer_id)
                if result and result.get("granted"):
                    welcome_extra = "\n\n" + L("referral_welcome_bonus", p.lang)
            except (TypeError, ValueError):
                pass

        remaining = await self._api_limits(uid)
        quota_text = ""
        if remaining:
            quota_text = self._quota_text(
                p.lang, remaining.get("remaining", {}), remaining.get("referral_bonus", 0)
            )

        await self.send(
            chat_id,
            self._welcome_text(p, quota_text) + welcome_extra,
            self._main_keyboard(p.lang),
        )

    async def _handle_command(self, chat_id: int, uid: int, text: str) -> None:
        cmd = text.strip().split()[0].lower().split("@")[0]
        p = self.prefs(uid)

        if cmd == "/start":
            await self._handle_start(chat_id, uid, text)
        elif cmd == "/help":
            await self.send(chat_id, self._help_text(p))
        elif cmd in ("/sozlama", "/settings"):
            await self.send(chat_id, self._settings_text(p), self._settings_keyboard(p))
        elif cmd in ("/referal", "/referral"):
            await self._show_referral(chat_id, uid)
        elif cmd == "/til":
            await self.send(chat_id, L("lang_choose", p.lang), self._lang_keyboard())
        else:
            await self.send(chat_id, L("unknown_command", p.lang))

    async def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        uid = (cb.get("from") or {}).get("id")
        if not chat_id or not uid:
            return

        p = self.prefs(uid)
        lang = p.lang
        kind, _, value = data.partition(":")
        note = ""

        if kind == "slides" and value.isdigit() and int(value) in SLIDE_CHOICES:
            p.slides = int(value)
            note = f"{p.slides} ✅"
            await self.answer_callback(cb["id"], note)
            if message_id:
                await self.edit(chat_id, message_id, self._settings_text(p), self._settings_keyboard(p))
            return
        elif kind == "theme" and value in THEMES[lang]:
            p.theme = value
            note = f"{THEMES[lang][value]} ✅"
            await self.answer_callback(cb["id"], note)
            if message_id:
                await self.edit(chat_id, message_id, self._settings_text(p), self._settings_keyboard(p))
            return

        if kind == "lang" and value in ("uz", "ru"):
            p.lang = value
            await self.answer_callback(cb["id"], L("lang_set", value))
            if message_id:
                await self.edit(chat_id, message_id, self._welcome_text(p), self._main_keyboard(p.lang))
            return

        # Menyu tugmalari
        if kind == "menu":
            if value == "settings":
                await self.answer_callback(cb["id"], L("settings_title", lang))
                await self.send(chat_id, self._settings_text(p), self._settings_keyboard(p))
            elif value == "referral":
                await self.answer_callback(cb["id"], L("referral_title", lang))
                await self._show_referral(chat_id, uid)
            elif value == "help":
                await self.answer_callback(cb["id"], L("help_title", lang))
                await self.send(chat_id, self._help_text(p))
            elif value == "lang":
                await self.answer_callback(cb["id"])
                await self.send(chat_id, L("lang_choose", lang), self._lang_keyboard())
            elif value == "main":
                await self.answer_callback(cb["id"])
                if message_id:
                    await self.edit(chat_id, message_id, self._welcome_text(p), self._main_keyboard(lang))
            return

        # Bekor qilish
        if kind == "cancel" and value:
            await self.answer_callback(cb["id"], L("cancelling", lang))
            code, body = await self._api_cancel(value)
            if code not in (200, 202) and not (body or {}).get("status") == "cancelled":
                await self.send(
                    chat_id,
                    L("cancel_failed", lang, detail=html.escape(str(body.get("detail", body)))),
                )
            return

        # Qayta yaratish
        if kind == "again":
            topic = self._last_topic.get(uid, "")
            if not topic:
                await self.answer_callback(cb["id"], L("topic_not_found", lang))
                return
            await self.answer_callback(cb["id"], L("regenerating", lang))
            asyncio.create_task(self._handle_topic(chat_id, uid, topic))
            return

        # Sozlamalar xabarida qolgan tugmalar (masalan, eski xabar) — shunchaki
        # yangilab qo'yamiz
        await self.answer_callback(cb["id"])

    # ------------------------------------------------------------------ #
    # Long-polling
    # ------------------------------------------------------------------ #
    async def _process_update(self, update: dict) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        uid = (message.get("from") or {}).get("id") or chat_id
        text = message.get("text") or ""

        if not text:
            await self.send(chat_id, L("topic_only_text", self.prefs(uid).lang))
            return

        if text.startswith("/"):
            await self._handle_command(chat_id, uid, text)
        else:
            # Har bir generatsiya uzoq davom etadi — polling siklini
            # bloklamaslik uchun alohida taskda bajaramiz.
            asyncio.create_task(self._handle_topic(chat_id, uid, text))

    async def run(self) -> None:
        me = await self._call("getMe")
        if not self._username:
            self._username = me.get("username", "")
        logger.info("Bot ishga tushdi: @%s (API_BASE=%s)", me.get("username"), API_BASE)
        # Eski, to'planib qolgan updatelarni tashlab yuboramiz — restartdan
        # keyin bot bir necha kunlik xabarlarga javob bermasin.
        try:
            pending = await self._call("getUpdates", offset=-1, timeout=0)
            if pending:
                self._offset = pending[-1]["update_id"] + 1
        except Exception:  # noqa: BLE001
            pass

        while True:
            try:
                updates = await self._call("getUpdates", offset=self._offset, timeout=30)
                for update in updates:
                    self._offset = update["update_id"] + 1
                    try:
                        await self._process_update(update)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Update qayta ishlanmadi: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("getUpdates xatosi: %s — 3s dan keyin qayta urinamiz", exc)
                await asyncio.sleep(3.0)


async def main() -> None:
    bot = SlideBot()
    await bot.run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
