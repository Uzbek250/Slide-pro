"""
Har bir slayd JSON obyektini HTML shablonga quyadi, so'ng Playwright (headless
Chromium) orqali to'g'ridan-to'g'ri vektor PDF sahifasiga ("Print to PDF")
render qiladi.

Nega HTML+CSS render yondashuvi: LLM chiqargan struktura ustida CSS orqali
to'liq nazorat saqlaymiz (gradient, shrift, layout) — bular python-pptx'ning
o'zida ishonchli chiqmaydi.

Nega screenshot(JPEG)+Pillow emas, balki page.pdf(): avvalgi versiyada har
bir slayd JPEG rasmga screenshot qilinib, keyin Pillow bilan PDF sahifalariga
yig'ilardi — bu matnni RASM qilib qo'yardi (zoom qilganda xiralashadi,
device_scale_factor past bo'lsa sifat pasayadi). page.pdf() esa Chromium'ning
o'z "Print to PDF" mexanizmidan foydalanadi: natija VEKTOR PDF (matn haqiqiy
matn sifatida qoladi, istalgan darajada zoom qilinsa ham aniq turadi) va
screenshot+JPEG encode+Pillow decode bosqichlari umuman yo'qoladi.
WeasyPrint kabi CSS-to-PDF renderer'lar sinalgan, lekin ular flexbox/grid'ni
to'liq qo'llab-quvvatlamagani uchun layout buzilib chiqqan — bu yerda esa
haqiqiy Chromium rendering engine ishlatilgani uchun layout screenshot
versiyasi bilan bir xil aniqlikda chiqadi.

Eslatma: bu ilova ataylab faqat matnga asoslangan — tashqi rasm/fotosurat
qidirish yo'q (avval Wikimedia/Openverse bilan sinalgan, lekin natija
sifat/mos kelish jihatidan qoniqarli bo'lmagani uchun olib tashlangan).
Vizual boylik faqat ikon (emoji), rang, tipografiya va layout xilma-xilligi
orqali beriladi.
"""
import logging
import os
import random
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

logger = logging.getLogger("renderer")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080

# page.pdf() sahifa o'lchamini piksel emas, balki fizik birlikda (dyuym)
# kutadi. CSS'dagi @page { size: 1920px 1080px } qoidasi ham bor, lekin
# ba'zi Playwright versiyalarida width/height parametrlari @page'dan
# ustun turadi - shuning uchun ikkalasini ham mos qilib beramiz.
# 1920px / 144 (standart CSS px->in, 1.5 device_scale hisobga olingan
# holda ekvivalent) o'rniga soddaroq: 96 CSS px = 1in standart nisbat.
_PDF_WIDTH_IN = SLIDE_WIDTH / 96  # = 20in
_PDF_HEIGHT_IN = SLIDE_HEIGHT / 96  # = 11.25in

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

# --------------------------------------------------------------------------- #
# Dizayn xilma-xilligi: har bir slaydga navbatma-navbat boshqa accent rangi.
#
# Nega kerak: butun taqdimot bitta accent rangda chiqsa, hamma prezertatsiya
# bir-biriga o'xshab qoladi. Endi har slayd o'z accent juftligini oladi:
#   accent  — asosiy urg'u rangi (sarlavha chizig'i, raqamlar, diagramma)
#   accent-2 — gradient/ikkinchi daraja (accent-bar, grafiklar)
# Hamma juftliklar OQ fonda kontrastli qilib tanlangan (matn o'qilishi
# buzilmaydi). Tanlov: seed + slayd indeksi — shu sababli qo'shni slaydlar
# har xil, "Qayta yaratish" esa butunlay boshqa taqsimot beradi.
# --------------------------------------------------------------------------- #
PALETTES: dict[str, list[tuple[str, str]]] = {
    "minimal": [
        ("#1a1a1a", "#6b7280"),   # grafit
        ("#1d4ed8", "#60a5fa"),   # ko'k
        ("#0f766e", "#2dd4bf"),   # firuza
        ("#7c2d12", "#fdba74"),   # olovli
        ("#4c1d95", "#a78bfa"),   # binafsha
        ("#0e7490", "#67e8f9"),   # havo
        ("#166534", "#4ade80"),   # yashil
        ("#be185d", "#f472b6"),   # pushti
    ],
    "corporate": [
        ("#1d4ed8", "#38bdf8"),
        ("#4338ca", "#818cf8"),
        ("#0e7490", "#22d3ee"),
        ("#2563eb", "#93c5fd"),
        ("#3730a3", "#a5b4fc"),
        ("#0369a1", "#7dd3fc"),
        ("#1e40af", "#60a5fa"),
        ("#0891b2", "#67e8f9"),
    ],
    "warm": [
        ("#c2571f", "#fbbf24"),
        ("#b91c1c", "#fca5a5"),
        ("#b45309", "#fcd34d"),
        ("#be185d", "#f9a8d4"),
        ("#ea580c", "#fdba74"),
        ("#9a3412", "#fed7aa"),
        ("#db2777", "#fbcfe8"),
        ("#a16207", "#fde047"),
    ],
    "forest": [
        ("#0f6e56", "#34d399"),
        ("#166534", "#4ade80"),
        ("#065f46", "#10b981"),
        ("#134e4a", "#2dd4bf"),
        ("#365314", "#a3e635"),
        ("#14532d", "#86efac"),
        ("#0f766e", "#99f6e4"),
        ("#3f6212", "#d9f99d"),
    ],
}

# Layout variantlari (bullets/title va b.) — shablon variant soni
VARIANT_COUNT = 2


def _accent_style(theme: str, seed: int, idx: int) -> str:
    """Berilgan slayd uchun accent ranglarini CSS var qilib qaytaradi."""
    pairs = PALETTES.get(theme) or PALETTES["minimal"]
    accent, accent2 = pairs[(seed + idx) % len(pairs)]
    return f'style="--accent:{accent};--accent-2:{accent2};"'


def render_slide_html(slide: dict, theme: str, page_num: int, seed: int = 0) -> str:
    """Bitta slayd uchun to'liq HTML matnini qaytaradi."""
    slide_type = slide.get("type", "bullets")
    template_name = f"{slide_type}.html"

    if not (TEMPLATES_DIR / template_name).exists():
        template_name = "bullets.html"  # fallback

    template = _jinja_env.get_template(template_name)
    context = {
        **slide,
        "theme": theme,
        "page_num": page_num,
        "accent_style": _accent_style(theme, seed, page_num),
        "variant": (seed + page_num) % VARIANT_COUNT,
    }
    return template.render(**context)


def render_deck_to_pdf_pages(
    deck: dict, output_dir: str, progress: object | None = None
) -> list[str]:
    """
    Butun deck (title + slides) uchun har bir slaydni bitta sahifali vektor
    PDF faylga render qiladi. Fayl yo'llari ro'yxatini tartib bilan
    qaytaradi - keyinroq pdf_builder bularni bitta ko'p sahifali faylga
    birlashtiradi.

    progress: ixtiyoriy callback (pct, stage, meta) — har bir slayd tugagach
    chaqiriladi. JobCancelled turidagi istisno ichkaridan kelib chiqsa
    (foydalanuvchi bekor qilgan bo'lsa) — render to'xtaydi.
    """
    os.makedirs(output_dir, exist_ok=True)
    theme = deck.get("theme", "minimal")
    pdf_paths = []
    slides = deck["slides"]
    total = len(slides)

    # Har bir generatsiya o'z seed'ini oladi — qo'shni slaydlar turli accent
    # rangda chiqadi, "Qayta yaratish" esa boshqa ranglar taqsimotini beradi.
    seed = random.randint(0, 100_000)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(
            viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
        )

        for i, slide in enumerate(slides):
            html = render_slide_html(slide, theme, page_num=i + 1, seed=seed)
            page.set_content(html, wait_until="load", timeout=15000)
            # Google Fonts @import CSS orqali asinxron yuklanadi. Vektor
            # PDF'da agar shrift hali tayyor bo'lmasa, matn fallback shrift
            # bilan "qotib" qolishi mumkin (screenshot'da bu faqat vizual
            # kamchilik edi, PDF'da esa embed qilingan holatda saqlanadi).
            page.evaluate("document.fonts.ready.then(() => true)")

            pdf_path = os.path.join(output_dir, f"slide_{i:03d}.pdf")
            page.pdf(
                path=pdf_path,
                width=f"{_PDF_WIDTH_IN}in",
                height=f"{_PDF_HEIGHT_IN}in",
                print_background=True,  # fon rang/gradientlarni chop etish
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            pdf_paths.append(pdf_path)

            # Real progress: render bosqichi 15%..92% oralig'ida
            if progress is not None:
                pct = 15 + round(77 * (i + 1) / total)
                progress(
                    pct, "render",
                    {"slide": i + 1, "total_slides": total},
                )

        browser.close()

    return pdf_paths
