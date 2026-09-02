"""
Telegram bot: mavzu → tayyor PDF taqdimot.

Long-polling (getUpdates) asosida ishlaydi — webhook, ochiq port yoki HTTPS
sertifikat kerak emas. Bot backend'ning HTTP API'sini chaqiradi:

  POST /generate          → 202 {job_id, remaining} (navbatga tushadi)
  GET  /status/{job_id}   → holat + REAL progress (pct, stage, slayd i/n)
  GET  /limits            → foydalanuvchi limitlari
  POST /jobs/{job_id}/cancel — ishni bekor qilish
  GET  /download/{job_id} → tayyor PDF

UX: ish davomida xabar jonli progress bar bilan yangilanadi (navbat holati,
bosqich, foiz, qolgan vaqt), "❌ Bekor qilish" tugmasi bor. Tugagach
"🔄 Qayta yaratish" va "⚙️ Sozlamalar" tugmalari chiqadi. Barcha menyular
inline tugmalar orqali.

Ishga tushirish:
    export TELEGRAM_BOT_TOKEN=...            # @BotFather
    export API_BASE=http://127.0.0.1:8003    # backend manzili
    export INTERNAL_TOKEN=...                # per-user limit uchun
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

# Telegram bot API orqali yuborish chegarasi
MAX_SEND_BYTES = 50 * 1024 * 1024

# Progress bar uzunligi (belgi)
BAR_LEN = 14

STAGE_LABELS = {
    "llm": "🤖 Matn tayyorlanmoqda",
    "render": "🎨 Slaydlar chizilmoqda",
    "merge": "📦 PDF yig'ilmoqda",
}


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


def _fmt_secs(secs: int) -> str:
    """Sekundlarni ixcham ko'rsatadi: 45 s / 2 daq 10 s / 1 soat."""
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} daq {secs % 60:02d} s"
    return f"{secs // 3600} soat {(secs % 3600) // 60:02d} daq"


THEMES = {
    "minimal": "Minimal (qora urg'u)",
    "corporate": "Corporate (ko'k)",
    "warm": "Warm (to'q sariq)",
    "forest": "Forest (yashil)",
}
SLIDE_CHOICES = [5, 8, 10, 15, 20]

MIN_TOPIC_LEN = 2
MAX_TOPIC_LEN = 300


class Prefs:
    """Foydalanuvchi tanlovlari (xotirada; restartda standartga qaytadi)."""

    __slots__ = ("slides", "theme")

    def __init__(self) -> None:
        self.slides = 8
        self.theme = "minimal"

    def summary(self) -> str:
        return (
            f"{self.slides} slayd · PDF · {THEMES[self.theme].split(' (')[0]}"
        )


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
                    {"text": mark(p.theme == "minimal", "Minimal"), "callback_data": "theme:minimal"},
                    {"text": mark(p.theme == "corporate", "Corporate"), "callback_data": "theme:corporate"},
                ],
                [
                    {"text": mark(p.theme == "warm", "Warm"), "callback_data": "theme:warm"},
                    {"text": mark(p.theme == "forest", "Forest"), "callback_data": "theme:forest"},
                ],
                [{"text": "🏠 Asosiy menyu", "callback_data": "menu:main"}],
            ]
        }
        return kb

    def _main_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "⚙️ Sozlamalar", "callback_data": "menu:settings"},
                    {"text": "❓ Yordam", "callback_data": "menu:help"},
                ],
            ]
        }

    def _cancel_keyboard(self, job_id: str) -> dict:
        return {
            "inline_keyboard": [[
                {"text": "❌ Bekor qilish", "callback_data": f"cancel:{job_id}"},
            ]]
        }

    def _done_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "🔄 Qayta yaratish", "callback_data": "again"},
                    {"text": "⚙️ Sozlamalar", "callback_data": "menu:settings"},
                ],
            ]
        }

    def prefs(self, uid: int) -> Prefs:
        return self._prefs.setdefault(uid, Prefs())

    def _welcome_text(self, p: Prefs) -> str:
        return (
            "🎯 <b>Slide — AI taqdimot generatori</b>\n\n"
            "Menga mavzuni yozib yuboring — tayyor PDF taqdimotni "
            "fayl ko'rinishida qaytaraman.\n\n"
            "📝 Masalan: <i>Sun'iy intellekt tibbiyotda</i>\n\n"
            f"Hozirgi sozlamalar: <b>{p.summary()}</b>\n\n"
            "Quyidagi tugmalardan sozlamalarni o'zgartirishingiz mumkin 👇"
        )

    def _help_text(self, p: Prefs) -> str:
        return (
            "🎯 <b>Slide — AI taqdimot generatori</b>\n\n"
            "Ishlatish juda oddiy: mavzuni yozasiz, men tayyor PDF "
            "taqdimotni yuboraman. Ish davomida progress bar ko'rinib "
            "turadi, istasangiz <b>❌ Bekor qilish</b> tugmasi bilan "
            "to'xtatishingiz mumkin.\n\n"
            f"Hozirgi sozlamalar: <b>{p.summary()}</b>\n\n"
            "Komandalar:\n"
            "  /start — asosiy menyu\n"
            "  /sozlama — slayd soni va dizayn\n"
            "  /help — bu yordam\n\n"
            "Sozlamalarni <b>⚙️ Sozlamalar</b> tugmasi orqali ham "
            "o'zgartirsa bo'ladi."
        )

    # ------------------------------------------------------------------ #
    # Backend API
    # ------------------------------------------------------------------ #
    def _headers(self, uid: int) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if INTERNAL_TOKEN:
            h["X-Internal-Token"] = INTERNAL_TOKEN
            h["X-Client-Id"] = f"tg:{uid}"
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

    # ------------------------------------------------------------------ #
    # Progress xabari matni
    # ------------------------------------------------------------------ #
    def _progress_text(self, head: str, st: dict) -> str:
        """Navbat/progress holatidan chiroyli matn yasaydi."""
        status = st.get("status")
        if status == "queued":
            pos = st.get("queue_position", 0)
            eta = st.get("eta_seconds", 0)
            body = (
                f"⏳ Navbatda: oldingizda {pos} ta so'rov\n"
                f"Taxminiy kutish: {_fmt_secs(eta)}"
                if pos else f"⏳ Navbatda… Taxminiy kutish: {_fmt_secs(eta)}"
            )
        elif status == "running":
            pr = st.get("progress") or {}
            pct = pr.get("pct", 0)
            stage = pr.get("stage", "")
            slide, total = pr.get("slide", 0), pr.get("total_slides", 0)
            stage_txt = STAGE_LABELS.get(stage, "⚙️ Tayyorlanmoqda")
            slide_txt = f" ({slide}/{total} slayd)" if total else ""
            eta = st.get("eta_seconds")
            eta_txt = f"\n⏱ Qolgan vaqt: ~{_fmt_secs(eta)}" if eta else ""
            body = (
                f"{_bar(pct)} <b>{pct}%</b>\n"
                f"{stage_txt}{slide_txt}{eta_txt}"
            )
        else:  # done/error/cancelled — oxirgi kadr
            body = "⚙️ Yakunlanmoqda…"
        return f"{head}\n\n{body}"

    # ------------------------------------------------------------------ #
    # Asosiy oqim: mavzu → taqdimot
    # ------------------------------------------------------------------ #
    async def _handle_topic(self, chat_id: int, uid: int, topic: str) -> None:
        p = self.prefs(uid)

        if uid in self._busy:
            await self.send(
                chat_id,
                "⏳ Sizning oldingi so'rovingiz hali bajarilmoqda. "
                "Tayyor bo'lishini kuting yoki bekor qiling.",
            )
            return

        topic = topic.strip()
        if len(topic) < MIN_TOPIC_LEN:
            await self.send(chat_id, "Mavzu juda qisqa. Kamida 2 belgi yozing.")
            return
        if len(topic) > MAX_TOPIC_LEN:
            await self.send(
                chat_id, f"Mavzu juda uzun ({len(topic)} belgi). {MAX_TOPIC_LEN} belgidan oshmasin."
            )
            return

        self._busy.add(uid)
        self._last_topic[uid] = topic
        try:
            code, data = await self._api_generate(uid, topic, p)

            if code == 429:
                await self.send(
                    chat_id, f"🚫 {html.escape(str(data.get('detail', 'Limit tugadi.')))}"
                )
                return
            if code == 503:
                await self.send(
                    chat_id, f"🕒 {html.escape(str(data.get('detail', 'Server band.')))}"
                )
                return
            if code not in (200, 202):
                detail = data.get("detail")
                if isinstance(detail, list) and detail:
                    detail = detail[0].get("msg", str(detail))
                await self.send(
                    chat_id,
                    f"❌ So'rov qabul qilinmadi: {html.escape(str(detail))}",
                )
                return

            job_id = data["job_id"]
            head = f"📝 <b>{html.escape(topic[:120])}</b>\n{p.summary()}"

            msg_id = await self.send(
                chat_id,
                self._progress_text(head, data),
                self._cancel_keyboard(job_id),
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
                text = self._progress_text(head, st)
                now = time.time()
                if text != last_text and msg_id and (now - last_edit >= 3.0):
                    await self.edit(chat_id, msg_id, text, self._cancel_keyboard(job_id))
                    last_text = text
                    last_edit = now

            if st.get("status") == "done":
                if msg_id:
                    await self.edit(
                        chat_id, msg_id,
                        f"{head}\n\n📤 Fayl yuborilmoqda…",
                    )
            elif st.get("status") == "cancelled":
                if msg_id:
                    await self.edit(chat_id, msg_id, f"{head}\n\n❌ Bekor qilindi.")
                else:
                    await self.send(chat_id, "❌ Bekor qilindi.")
                return
            elif st.get("status") == "error":
                err = st.get("error", "noma'lum xato")
                body = f"{head}\n\n❌ Xatolik: {html.escape(err[:300])}"
                if msg_id:
                    await self.edit(chat_id, msg_id, body, self._done_keyboard())
                else:
                    await self.send(chat_id, body, self._done_keyboard())
                return
            else:
                if msg_id:
                    await self.edit(
                        chat_id, msg_id,
                        f"{head}\n\n⌛️ Juda uzoq davom etdi. Keyinroq qayta urinib ko'ring.",
                    )
                return

            try:
                blob = await self._api_download(job_id)
            except Exception as exc:  # noqa: BLE001
                await self.edit(
                    chat_id, msg_id or 0,
                    f"{head}\n\n❌ Fayl yuklanmadi: {html.escape(str(exc))}",
                )
                return

            if len(blob) > MAX_SEND_BYTES:
                await self.edit(
                    chat_id, msg_id or 0,
                    f"{head}\n\n❌ Fayl juda katta ({len(blob) / 1e6:.1f} MB). "
                    "Slayd sonini kamaytirib ko'ring.",
                )
                return

            filename = st.get("filename") or data.get("filename") or "taqdimot.pdf"
            took = st.get("took_seconds", "?")
            ok = await self.send_document(
                chat_id, blob, filename,
                caption=f"✅ Tayyor — {p.slides} slayd, {took} s",
            )
            if ok and msg_id:
                await self.edit(
                    chat_id, msg_id,
                    f"{head}\n\n✅ <b>Tayyor</b> ({took} s, {_fmt_size(len(blob))})",
                    self._done_keyboard(),
                )
            elif not ok:
                await self.send(chat_id, "❌ Faylni yuborib bo'lmadi. Qayta urinib ko'ring.")
        finally:
            self._busy.discard(uid)

    # ------------------------------------------------------------------ #
    # Komandalar / callback
    # ------------------------------------------------------------------ #
    async def _handle_command(self, chat_id: int, uid: int, text: str) -> None:
        cmd = text.strip().split()[0].lower().split("@")[0]
        p = self.prefs(uid)

        if cmd in ("/start",):
            await self.send(
                chat_id, self._welcome_text(p), self._main_keyboard(),
            )
        elif cmd in ("/help",):
            await self.send(chat_id, self._help_text(p))
        elif cmd in ("/sozlama", "/settings"):
            await self.send(
                chat_id,
                f"⚙️ <b>Sozlamalar</b>\n\nHozir: {p.summary()}\n\n"
                "O'zgartirish uchun tugmani bosing:",
                self._settings_keyboard(p),
            )
        else:
            await self.send(chat_id, "Noma'lum komanda. /help — yordam.")

    async def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        uid = (cb.get("from") or {}).get("id")
        if not chat_id or not uid:
            return

        p = self.prefs(uid)
        kind, _, value = data.partition(":")
        note = ""

        if kind == "slides" and value.isdigit() and int(value) in SLIDE_CHOICES:
            p.slides = int(value)
            note = f"{p.slides} slayd ✅"
            await self.answer_callback(cb["id"], note)
            if message_id:
                await self.edit(
                    chat_id, message_id,
                    f"⚙️ <b>Sozlamalar</b>\n\nHozir: {p.summary()}\n\n"
                    "O'zgartirish uchun tugmani bosing:",
                    self._settings_keyboard(p),
                )
            return
        elif kind == "theme" and value in THEMES:
            p.theme = value
            note = f"Dizayn: {THEMES[value]} ✅"
            await self.answer_callback(cb["id"], note)
            if message_id:
                await self.edit(
                    chat_id, message_id,
                    f"⚙️ <b>Sozlamalar</b>\n\nHozir: {p.summary()}\n\n"
                    "O'zgartirish uchun tugmani bosing:",
                    self._settings_keyboard(p),
                )
            return

        # Menyu tugmalari
        if kind == "menu":
            if value == "settings":
                await self.answer_callback(cb["id"], "⚙️ Sozlamalar")
                await self.send(
                    chat_id,
                    f"⚙️ <b>Sozlamalar</b>\n\nHozir: {p.summary()}\n\n"
                    "O'zgartirish uchun tugmani bosing:",
                    self._settings_keyboard(p),
                )
            elif value == "help":
                await self.answer_callback(cb["id"], "❓ Yordam")
                await self.send(chat_id, self._help_text(p))
            elif value == "main":
                await self.answer_callback(cb["id"], "🏠 Asosiy menyu")
                if message_id:
                    await self.edit(
                        chat_id, message_id,
                        self._welcome_text(p), self._main_keyboard(),
                    )
            return

        # Bekor qilish
        if kind == "cancel" and value:
            await self.answer_callback(cb["id"], "⏹ Bekor qilinyapti…")
            code, body = await self._api_cancel(value)
            if code not in (200, 202) and not (body or {}).get("status") == "cancelled":
                await self.send(
                    chat_id,
                    "❌ Ishnni bekor qilib bo'lmadi: "
                    f"{html.escape(str(body.get('detail', body)))}",
                )
            return

        # Qayta yaratish
        if kind == "again":
            topic = self._last_topic.get(uid, "")
            if not topic:
                await self.answer_callback(cb["id"], "Mavzu topilmadi — yangi mavzu yozing")
                return
            await self.answer_callback(cb["id"], "🔄 Qayta yaratilmoqda…")
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
            await self.send(
                chat_id,
                "Faqat matnli mavzu qabul qilaman. Masalan: Yashil energiya O'zbekistonda",
            )
            return

        if text.startswith("/"):
            await self._handle_command(chat_id, uid, text)
        else:
            # Har bir generatsiya uzoq davom etadi — polling siklini
            # bloklamaslik uchun alohida taskda bajaramiz.
            asyncio.create_task(self._handle_topic(chat_id, uid, text))

    async def run(self) -> None:
        me = await self._call("getMe")
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
