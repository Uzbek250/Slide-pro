#!/usr/bin/env python3
"""Xuddi shu deckni Chromium yo'li bilan PDF qilish (taqqoslash uchun)."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.renderer import render_deck_to_images
from app.core.pdf_builder import build_pdf

DECK = json.load(open(os.path.join(os.path.dirname(__file__), "weasy_fake_deck.json")))

with tempfile.TemporaryDirectory() as tmp:
    t0 = time.perf_counter()
    imgs = render_deck_to_images(DECK, tmp)
    build_pdf(imgs, "/tmp/compare_chromium.pdf", deck_title=DECK["title"])
    print(f"Chromium yo'li: {time.perf_counter()-t0:.1f} s, "
          f"{os.path.getsize('/tmp/compare_chromium.pdf')/1e6:.2f} MB")
