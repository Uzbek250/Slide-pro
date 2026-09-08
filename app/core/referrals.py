"""
Referal (do'stni taklif qilish) tizimi: har bir foydalanuvchi o'zining
referal havolasini oladi, do'sti o'sha havola orqali botga birinchi marta
kirsa — taklif qiluvchiga kunlik limitga qo'shimcha bonus beriladi.

Saqlash xotirada (in-memory) — bu loyihaning qolgan qismi (jobs, limits)
bilan bir xil model. Server qayta ishga tushsa referal hisoblari nolga
qaytadi. Bitta backend nusxasi uchun bu yetarli; agar kelajakda bir nechta
instansiya kerak bo'lsa, bu ham Redis'ga ko'chirilishi kerak (jobs.py va
limits.py dagi kabi).

Muhim qoidalar:
  - O'zini-o'zi taklif qilib bonus ololmaydi (referrer_id == new_user_id
    tekshiriladi)
  - Bitta foydalanuvchi faqat BITTA marta "taklif qilingan" deb hisoblanadi
    (ikkinchi marta /start qilsa yoki boshqa havola bosib qayta kirsa, bonus
    qayta berilmaydi) — aks holda bitta odam bir nechta marta start bosib,
    o'ziga cheksiz bonus yasashi mumkin bo'lardi
  - Har bir taklif qiluvchi uchun umumiy bonus REFERRAL_BONUS_MAX bilan
    cheklanadi (0 = cheksiz) — aks holda "bot ferma" orqali suiiste'mol qilish
    xavfi bor
"""
import os
import threading


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


BONUS_PER_INVITE = _env_int("REFERRAL_BONUS_PER_INVITE", 2)
BONUS_MAX = _env_int("REFERRAL_BONUS_MAX", 10)


class ReferralStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # user_id -> shu foydalanuvchini kim taklif qilgani (bir marta yoziladi)
        self._invited_by: dict[int, int] = {}
        # referrer_id -> muvaffaqiyatli taklif qilingan foydalanuvchilar soni
        self._invite_counts: dict[int, int] = {}

    def register_referral(self, new_user_id: int, referrer_id: int) -> bool:
        """
        Yangi foydalanuvchi referrer_id havolasi orqali kirganini qayd etadi.
        True qaytaradi — agar bu haqiqatan YANGI, muvaffaqiyatli referal bo'lsa
        (bonus berilishi kerak). False — agar allaqachon ro'yxatdan o'tgan
        bo'lsa yoki o'zini-o'zi taklif qilish urinishi bo'lsa (bonus YO'Q).
        """
        if new_user_id == referrer_id:
            return False
        with self._lock:
            if new_user_id in self._invited_by:
                # Bu foydalanuvchi allaqachon (birinchi marta kirganda) qайд
                # etilgan — qayta bonus berilmaydi.
                return False
            current = self._invite_counts.get(referrer_id, 0)
            if BONUS_MAX and current * BONUS_PER_INVITE >= BONUS_MAX:
                # Taklif qiluvchi allaqachon maksimal bonusga yetgan — baribir
                # "kim taklif qilgani"ni yozib qo'yamiz (statistika uchun),
                # lekin hisoblagichni oshirmaymiz.
                self._invited_by[new_user_id] = referrer_id
                return False
            self._invited_by[new_user_id] = referrer_id
            self._invite_counts[referrer_id] = current + 1
            return True

    def bonus_for(self, user_id: int) -> int:
        """Shu foydalanuvchining joriy kunlik bonus limitini qaytaradi."""
        with self._lock:
            count = self._invite_counts.get(user_id, 0)
        bonus = count * BONUS_PER_INVITE
        if BONUS_MAX:
            bonus = min(bonus, BONUS_MAX)
        return bonus

    def invite_count(self, user_id: int) -> int:
        with self._lock:
            return self._invite_counts.get(user_id, 0)


store = ReferralStore()
