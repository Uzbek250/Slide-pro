"""
Foydalanuvchi bo'yicha so'rov chegarasi (rate limiting) va global kunlik limit.

Nega kerak: Gemini bepul tarifi ~15 RPM / ~1500 RPD, Chromium esa har so'rovda
~300-400MB RAM oladi. Cheklovsiz ochiq endpoint bitta foydalanuvchi tomonidan
ham butun kvotani yoki serverni yeb qo'yishi mumkin.

Saqlash xotirada (in-memory): server qayta ishga tushsa hisoblar nolga
qaytadi. Bitta instansiya uchun bu yetarli; bir nechta instansiya bo'lsa
Redis kerak bo'ladi.
"""
import os
import threading
import time
from collections import defaultdict, deque


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


class RateLimiter:
    """
    Ikki darajali sliding-window hisoblagich:
      - har bir foydalanuvchi uchun daqiqalik, soatlik va kunlik limit
      - butun servis uchun daqiqalik va kunlik limit (Gemini kvotasini himoya qiladi)

    0 (yoki None) = limit o'chiq. Asosiy himoya daqiqalik limit — server
    quvvatiga qarab sozlanadi (qarang: RATE_PER_MINUTE, RATE_GLOBAL_PER_MINUTE).
    """

    def __init__(
        self,
        per_minute: int | None = None,
        per_hour: int | None = None,
        per_day: int | None = None,
        global_per_minute: int | None = None,
        global_per_day: int | None = None,
    ) -> None:
        self.per_minute = per_minute if per_minute is not None else _env_int("RATE_PER_MINUTE", 3)
        self.per_hour = per_hour if per_hour is not None else _env_int("RATE_PER_HOUR", 0)
        self.per_day = per_day if per_day is not None else _env_int("RATE_PER_DAY", 0)
        self.global_per_minute = (
            global_per_minute
            if global_per_minute is not None
            else _env_int("RATE_GLOBAL_PER_MINUTE", 8)
        )
        self.global_per_day = (
            global_per_day if global_per_day is not None else _env_int("RATE_GLOBAL_PER_DAY", 0)
        )
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    @staticmethod
    def _trim(dq: deque[float], window: float, now: float) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    def check(self, key: str, extra_daily: int = 0) -> tuple[bool, str, int]:
        """
        (ruxsat, sabab, retry_after_sekund) qaytaradi. Hisobni oshirmaydi —
        so'rov qabul qilinganda alohida record() chaqiriladi.

        extra_daily: shu foydalanuvchiga referal orqali qo'shilgan bonus
        kunlik limit (asosiy self.per_day ustiga qo'shiladi). Bu boshqa
        foydalanuvchilarning limitiga ta'sir qilmaydi — faqat shu key
        uchun chaqiruvchi tomonidan hisoblab beriladi (qarang: main.py).
        """
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            self._trim(dq, 86400, now)
            self._trim(self._global, 86400, now)

            minute_hits = [t for t in dq if now - t <= 60]
            if self.per_minute and len(minute_hits) >= self.per_minute:
                retry = int(60 - (now - minute_hits[0])) + 1
                return (
                    False,
                    f"Daqiqalik limit tugadi ({self.per_minute} ta/daqiqa). "
                    f"{retry} soniyadan keyin qayta urinib ko'ring.",
                    retry,
                )

            hour_hits = [t for t in dq if now - t <= 3600]
            if self.per_hour and len(hour_hits) >= self.per_hour:
                retry = int(3600 - (now - hour_hits[0])) + 1
                return (
                    False,
                    f"Soatlik limit tugadi ({self.per_hour} ta/soat). "
                    f"{max(retry // 60, 1)} daqiqadan keyin qayta urinib ko'ring.",
                    retry,
                )

            effective_per_day = (self.per_day + extra_daily) if self.per_day else 0
            if effective_per_day and len(dq) >= effective_per_day:
                retry = int(86400 - (now - dq[0])) + 1
                return (
                    False,
                    f"Kunlik limit tugadi ({effective_per_day} ta/kun). "
                    f"{max(retry // 3600, 1)} soatdan keyin qayta urinib ko'ring.",
                    retry,
                )

            global_minute_hits = [t for t in self._global if now - t <= 60]
            if self.global_per_minute and len(global_minute_hits) >= self.global_per_minute:
                retry = int(60 - (now - global_minute_hits[0])) + 1
                return (
                    False,
                    f"Servisning daqiqalik limiti to'ldi (butun server uchun "
                    f"{self.global_per_minute} ta/daqiqa). {retry} soniyadan keyin "
                    f"qayta urinib ko'ring.",
                    retry,
                )

            if self.global_per_day and len(self._global) >= self.global_per_day:
                retry = int(86400 - (now - self._global[0])) + 1
                return (
                    False,
                    "Servisning kunlik umumiy limiti tugadi. Ertaga qayta urinib ko'ring.",
                    retry,
                )

        return True, "", 0

    def record(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._hits[key].append(now)
            self._global.append(now)

    def remaining(self, key: str, extra_daily: int = 0) -> dict[str, int]:
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            self._trim(dq, 86400, now)
            self._trim(self._global, 86400, now)
            minute_hits = sum(1 for t in dq if now - t <= 60)
            hour_hits = sum(1 for t in dq if now - t <= 3600)
            effective_per_day = (self.per_day + extra_daily) if self.per_day else 0
            return {
                "minute_left": max(self.per_minute - minute_hits, 0) if self.per_minute else -1,
                "hour_left": max(self.per_hour - hour_hits, 0) if self.per_hour else -1,
                "day_left": max(effective_per_day - len(dq), 0) if effective_per_day else -1,
                "day_limit": effective_per_day if effective_per_day else -1,
                "global_minute_left": (
                    max(self.global_per_minute - sum(1 for t in self._global if now - t <= 60), 0)
                    if self.global_per_minute
                    else -1
                ),
                "global_day_left": (
                    max(self.global_per_day - len(self._global), 0)
                    if self.global_per_day
                    else -1
                ),
            }

    def refund(self, key: str) -> None:
        """
        Generatsiya server xatosi bilan tugasa, foydalanuvchining hisobini
        qaytaradi — o'z aybi bo'lmagan xato uchun limit yemasligi kerak.
        """
        with self._lock:
            dq = self._hits.get(key)
            if dq:
                dq.pop()
            if self._global:
                self._global.pop()


limiter = RateLimiter()
