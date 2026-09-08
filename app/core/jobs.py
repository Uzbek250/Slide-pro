"""
Navbat (queue) va bir vaqtda bajarilishni cheklash.

Nega kerak: har bir generatsiya headless Chromium ishga tushiradi (~300-400MB
RAM). Cheklovsiz bir vaqtda 10 so'rov kelsa server OOM bo'ladi yoki hammasi
sekinlashib timeout beradi. Shuning uchun:

  - MAX_CONCURRENT_JOBS ta ish bir vaqtda bajariladi
  - qolganlar navbatda kutadi (MAX_QUEUE tagacha), undan oshsa 503
  - har bir ish holati (queued/running/done/error/cancelled) so'rab olinadi
  - bajarilayotgan ish REAL progress hisobot beradi: pct + bosqich
    (llm → render → merge) + qaysi slayd render bo'lyapti
  - foydalanuvchi ishni bekor qilishi mumkin (cancel) — navbatda turgan
    ish darhol, bajarilayotgan ish esa keyingi slayd oralig'ida to'xtaydi
  - server xatosi bilan tugagan ish uchun limit "qaytariladi" (refund) —
    foydalanuvchi o'z aybi bo'lmagan xato uchun limit yemaydi

Tayyor fayllar FILE_TTL_SECONDS dan keyin o'chiriladi — /tmp cheksiz
o'smasligi kerak.

Eslatma: holat xotirada (in-memory) saqlanadi — bitta uvicorn worker
yetarli (web.sh da WORKERS=1). Bir nechta instansiya/worker kerak bo'lsa
(jobs Redis'ga ko'chirilishi shart), Contabo migratsiya rejasiga qarang.
"""
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.limits import limiter
from app.core.pipeline import generate_presentation

logger = logging.getLogger("jobs")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


MAX_CONCURRENT_JOBS = max(1, _env_int("MAX_CONCURRENT_JOBS", 2))
MAX_QUEUE = max(1, _env_int("MAX_QUEUE", 12))
FILE_TTL_SECONDS = _env_int("FILE_TTL_SECONDS", 3600)
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/slide_outputs")

os.makedirs(OUTPUT_DIR, exist_ok=True)


class QueueFull(Exception):
    """Navbat to'lgan — so'rovni hozir qabul qilib bo'lmaydi."""


class JobCancelled(Exception):
    """Ish foydalanuvchi tomonidan bekor qilindi (ichki signal)."""


@dataclass
class Job:
    job_id: str
    topic: str
    slide_count: int
    output_format: str
    theme: str
    owner: str
    status: str = "queued"  # queued | running | done | error | cancelled
    filename: str = ""
    path: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    # Progress (running paytida)
    pct: int = 0
    stage: str = ""  # llm | render | merge
    slide: int = 0
    total_slides: int = 0
    cancel_requested: bool = False

    def public(self, position: int = 0) -> dict:
        out = {
            "job_id": self.job_id,
            "status": self.status,
            "topic": self.topic,
            "slide_count": self.slide_count,
            "output_format": self.output_format,
        }
        if self.status == "queued":
            out["queue_position"] = position
            out["eta_seconds"] = _eta_seconds(position, self.slide_count)
        if self.status == "running":
            elapsed = int(time.time() - self.started_at)
            out["elapsed_seconds"] = elapsed
            out["progress"] = {
                "pct": self.pct,
                "stage": self.stage,
                "slide": self.slide,
                "total_slides": self.total_slides,
            }
            # ETA: kuzatilgan tezlikka asoslanadi (real), aks holda taxminiy
            if self.slide > 0 and self.total_slides > self.slide:
                per_slide = elapsed / self.slide
                out["eta_seconds"] = int(
                    per_slide * (self.total_slides - self.slide)
                )
            else:
                out["eta_seconds"] = max(
                    _eta_seconds(0, self.slide_count) - elapsed, 5
                )
        if self.status == "done":
            out["filename"] = self.filename
            out["download_url"] = f"/download/{self.job_id}"
            out["took_seconds"] = round(self.finished_at - self.started_at, 1)
        if self.status == "error":
            out["error"] = self.error
        return out


# Bitta slaydga taxminiy vaqt (Gemini bazasi + render). Faqat ETA ko'rsatish
# uchun — aniqlik talab qilinmaydi.
_BASE_SECONDS = _env_int("ETA_BASE_SECONDS", 25)
_PER_SLIDE_SECONDS = _env_int("ETA_PER_SLIDE_SECONDS", 3)


def _eta_seconds(position: int, slide_count: int) -> int:
    one = _BASE_SECONDS + _PER_SLIDE_SECONDS * slide_count
    waves = position // MAX_CONCURRENT_JOBS
    return int(one * (waves + 1))


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # queued job_id lar tartibi
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="slidegen"
        )
        self._active = 0

    # ------------------------------------------------------------------ #
    def submit(
        self,
        topic: str,
        slide_count: int,
        theme: str,
        owner: str,
        display_name: str,
    ) -> Job:
        with self._lock:
            queued = len(self._order)
            if queued >= MAX_QUEUE:
                raise QueueFull(
                    f"Navbat to'lgan ({queued} ta so'rov kutmoqda). "
                    "Bir necha daqiqadan keyin qayta urinib ko'ring."
                )
            job = Job(
                job_id=uuid.uuid4().hex,
                topic=topic,
                slide_count=slide_count,
                output_format="pdf",
                theme=theme,
                owner=owner,
                filename=display_name,
            )
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

        self._pool.submit(self._run, job.job_id)
        self._cleanup_old()
        return job

    # ------------------------------------------------------------------ #
    def cancel(self, job_id: str) -> Job | None:
        """
        Navbatdagi ishni darhol bekor qiladi; bajarilayotgan ishga esa
        keyingi slayd oralig'ida to'xtash buyrug'i beriladi.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                if job_id in self._order:
                    self._order.remove(job_id)
                logger.info("Job %s navbatdan bekor qilindi", job.job_id[:8])
            elif job.status == "running":
                job.cancel_requested = True
                logger.info("Job %s to'xtatish buyrug'i olindi", job.job_id[:8])
            return job

    # ------------------------------------------------------------------ #
    def _progress_cb(self, job: Job):
        """Pipeline'dan kelgan progress hisobotini job'ga yozadi."""

        def _cb(pct: int, stage: str, meta: dict) -> None:
            with self._lock:
                if job.cancel_requested and job.status == "running":
                    raise JobCancelled(job.job_id)
                job.pct = int(pct)
                job.stage = stage
                job.slide = meta.get("slide", 0)
                job.total_slides = meta.get("total_slides", 0)

        return _cb

    # ------------------------------------------------------------------ #
    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.status == "cancelled":  # navbatda turganida bekor qilingan
                return
            if job_id in self._order:
                self._order.remove(job_id)
            job.status = "running"
            job.started_at = time.time()
            job.pct = 0
            self._active += 1

        path = os.path.join(OUTPUT_DIR, f"{job.job_id}.{job.output_format}")
        try:
            generate_presentation(
                job.topic,
                job.slide_count,
                path,
                theme=job.theme,
                progress=self._progress_cb(job),
            )
            with self._lock:
                job.path = path
                job.pct = 100
                job.stage = "done"
                job.status = "done"
                job.finished_at = time.time()
            logger.info(
                "Job %s tayyor: %s slayd, %.1fs",
                job.job_id[:8], job.slide_count, job.finished_at - job.started_at,
            )
        except JobCancelled:
            with self._lock:
                job.status = "cancelled"
                job.finished_at = time.time()
            logger.info("Job %s foydalanuvchi tomonidan bekor qilindi", job.job_id[:8])
        except Exception as exc:  # noqa: BLE001
            # XAVFSIZLIK: xatoning to'liq matnini (str(exc)) to'g'ridan-to'g'ri
            # foydalanuvchiga ko'rsatmaymiz — bunda tashqi API'dan kelgan ichki
            # tafsilotlar (masalan xato javobi ichidagi texnik ma'lumot) oshkor
            # bo'lishi mumkin edi. Foydalanuvchiga faqat umumiy, xavfsiz xabar
            # ko'rsatiladi; to'liq xato faqat server logiga (pastda) yoziladi.
            with self._lock:
                job.status = "error"
                job.error = (
                    "Ichki xatolik yuz berdi. Iltimos, qayta urinib ko'ring "
                    "yoki bir necha daqiqadan keyin sinab ko'ring."
                )
                job.finished_at = time.time()
            # Server xatosi — foydalanuvchi limiti yemasligi kerak
            try:
                limiter.refund(job.owner)
            except Exception:  # noqa: BLE001
                pass
            logger.exception("Job %s xato: %s", job.job_id[:8], exc)
        finally:
            with self._lock:
                self._active -= 1

    # ------------------------------------------------------------------ #
    def get(self, job_id: str) -> tuple[Job | None, int]:
        with self._lock:
            job = self._jobs.get(job_id)
            position = self._order.index(job_id) if job_id in self._order else 0
            return job, position

    def stats(self) -> dict:
        with self._lock:
            return {
                "queued": len(self._order),
                "running": self._active,
                "max_concurrent": MAX_CONCURRENT_JOBS,
                "max_queue": MAX_QUEUE,
                "total_jobs": len(self._jobs),
            }

    # ------------------------------------------------------------------ #
    def _cleanup_old(self) -> None:
        """TTL o'tgan fayllarni va yozuvlarni o'chiradi."""
        now = time.time()
        stale: list[str] = []
        with self._lock:
            for jid, job in self._jobs.items():
                if (
                    job.status in ("done", "error", "cancelled")
                    and now - job.finished_at > FILE_TTL_SECONDS
                ):
                    stale.append(jid)
            for jid in stale:
                self._jobs.pop(jid, None)

        for jid in stale:
            p = os.path.join(OUTPUT_DIR, f"{jid}.pdf")
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

        # Registrda yo'q, lekin diskda qolgan eski fayllar (server
        # restartidan keyin qolgan "yetim" fayllar) ham tozalanadi.
        try:
            for name in os.listdir(OUTPUT_DIR):
                p = os.path.join(OUTPUT_DIR, name)
                try:
                    if os.path.isfile(p) and now - os.path.getmtime(p) > FILE_TTL_SECONDS:
                        os.remove(p)
                except OSError:
                    pass
        except OSError:
            pass


manager = JobManager()
