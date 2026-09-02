#!/usr/bin/env python3
"""Chromium vs WeasyPrint renderlarini piksel darajasida solishtiradi."""
import subprocess
import sys

from PIL import Image, ImageChops

CHROMIUM = "/tmp/compare_chromium.pdf"   # 7 slayd, eski usul
WEASY = "/tmp/weasy_v3.pdf"              # 7 slayd, yangi usul


def render_pages(pdf, prefix, dpi=72):
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf, prefix], check=True)
    import glob
    return sorted(glob.glob(prefix + "-*.png"))


print("Chromium render...")
c_pages = render_pages(CHROMIUM, "/tmp/cmp_c")
print("WeasyPrint render...")
w_pages = render_pages(WEASY, "/tmp/cmp_w")
print(f"Chromium: {len(c_pages)} sahifa, WeasyPrint: {len(w_pages)} sahifa")

for i, (cp, wp) in enumerate(zip(c_pages, w_pages), 1):
    ci = Image.open(cp).convert("RGB")
    wi = Image.open(wp).convert("RGB")
    # o'lchamlarni bir xillashtirish
    wi = wi.resize(ci.size)
    diff = ImageChops.difference(ci, wi)
    bbox = diff.getbbox()
    if bbox is None:
        print(f"Sahifa {i}: 100% bir xil ✓")
        continue
    # farq foizini hisoblash
    import numpy as np
    arr = np.asarray(diff)
    changed = (arr.sum(axis=2) > 30).mean() * 100
    # farq joylashuvi: yuqori/past/o'rta qismi
    h, w = arr.shape[:2]
    top = (arr[: h // 3].sum(axis=2) > 30).mean() * 100
    mid = (arr[h // 3 : 2 * h // 3].sum(axis=2) > 30).mean() * 100
    bot = (arr[2 * h // 3 :].sum(axis=2) > 30).mean() * 100
    print(f"Sahifa {i}: {changed:.0f}% farq | yuqori {top:.0f}% | o'rta {mid:.0f}% | past {bot:.0f}%")
