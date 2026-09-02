"""
To'liq pipeline: mavzu -> Gemini JSON -> HTML render -> vektor PDF sahifalar
-> birlashtirilgan PDF.

Eslatma: ilova avval PPTX va PDF ikkalasini ham qo'llab-quvvatlagan, lekin
ikkalasi ham bir xil screenshot(JPEG) rasmlaridan yig'ilgani va matn
tahrirlanmasligi (rasm sifatida joylashgani) uchun PPTX'ning amaliy foydasi
kam edi, shuning uchun PPTX yo'li olib tashlandi.

Keyinroq screenshot(JPEG)+Pillow bosqichi ham page.pdf() (Chromium "Print to
PDF") bilan almashtirildi — natija endi vektor PDF, matn rasm emas, zoom
qilinganda sifat yo'qolmaydi, va oraliq JPEG encode/decode bosqichi
yo'qolgani uchun biroz tezroq.
"""
import tempfile
from typing import Callable

from app.core.content_generator import generate_deck_structure
from app.core.renderer import render_deck_to_pdf_pages
from app.core.pdf_builder import build_pdf

# Progress bildirishnomasi: cb(pct: int, stage: str, meta: dict)
#   stage: llm (2-15) → render (15-92) → merge (95-100)
ProgressCallback = Callable[[int, str, dict], None]


def _notify(progress: ProgressCallback | None, pct: int, stage: str, **meta: object) -> None:
    if progress is not None:
        progress(int(pct), stage, meta)


def generate_presentation(
    topic: str,
    slide_count: int,
    output_path: str,
    theme: str = "minimal",
    progress: ProgressCallback | None = None,
) -> str:
    """
    To'liq oqimni ishga tushiradi va tayyor PDF fayl yo'lini qaytaradi.
    theme: foydalanuvchi tanlagan dizayn (minimal / corporate / warm / forest).
    progress: ixtiyoriy callback — real vaqt progress foizi va bosqichi.
    """
    _notify(progress, 2, "llm")
    deck = generate_deck_structure(topic, slide_count, theme=theme)
    _notify(progress, 15, "llm")

    with tempfile.TemporaryDirectory() as tmp_dir:
        _notify(progress, 17, "render")
        pdf_page_paths = render_deck_to_pdf_pages(deck, tmp_dir, progress=progress)
        deck_title = deck.get("title", topic)
        _notify(progress, 95, "merge")
        build_pdf(pdf_page_paths, output_path, deck_title=deck_title)

    _notify(progress, 100, "done")
    return output_path
