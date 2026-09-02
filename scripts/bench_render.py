#!/usr/bin/env python3
"""Render tezligini o'lchash: RENDER_SCALE x RENDER_CONCURRENCY kombinatsiyalari."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("RENDER_SCALE", "1")
os.environ.setdefault("RENDER_CONCURRENCY", "3")

from app.core.renderer import render_deck_to_images  # noqa: E402

DECK = json.load(open(os.path.join(os.path.dirname(__file__), "weasy_fake_deck.json")))

for scale in ("1", "1.5"):
    for conc in ("3", "4"):
        os.environ["RENDER_SCALE"] = scale
        os.environ["RENDER_CONCURRENCY"] = conc
        times = []
        total_mb = 0.0
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                t0 = time.perf_counter()
                imgs = render_deck_to_images(DECK, tmp)
                times.append(time.perf_counter() - t0)
                total_mb = sum(os.path.getsize(p) for p in imgs) / 1e6
        print(f"scale={scale} concurrency={conc}: {min(times):.1f} s (7 slayd, {total_mb:.1f} MB rasm)")
