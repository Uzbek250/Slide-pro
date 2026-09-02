#!/usr/bin/env python3
"""Ikki render orasidagi FARQNI vizual ko'rsatadi (ASCII)."""
import subprocess
import sys

from PIL import Image, ImageChops

CHROMIUM = "/tmp/compare_chromium.pdf"
WEASY = "/tmp/weasy_v3.pdf"
PAGE = int(sys.argv[1]) if len(sys.argv) > 1 else 2

for name, pdf in (("c", CHROMIUM), ("w", WEASY)):
    subprocess.run(["pdftoppm", "-png", "-r", "72", "-f", str(PAGE), "-l", str(PAGE), pdf, f"/tmp/diff_{name}"],
                   check=True, capture_output=True)
import glob as _g
import os as _os
ci = Image.open(sorted(_g.glob("/tmp/diff_c-*.png"), key=_os.path.getmtime)[-1]).convert("RGB")
wi = Image.open(sorted(_g.glob("/tmp/diff_w-*.png"), key=_os.path.getmtime)[-1]).convert("RGB")
wi = wi.resize(ci.size)
diff = ImageChops.difference(ci, wi)
# Farqni kuchaytirish: har qanday farq qizil
h, w = ci.size[1], ci.size[0]
out = Image.new("RGB", (w, h), (255, 255, 255))
dpx = diff.load()
opx = out.load()
cpx = ci.load()
for y in range(h):
    for x in range(w):
        dr, dg, db = dpx[x, y]
        if dr + dg + db > 45:  # sezilarli farq
            opx[x, y] = (220, 40, 40)
        else:
            opx[x, y] = cpx[x, y]

# ASCII ko'rinishi: qizil = farq, to'q = Chromium kontenti
COLS = 100
rows = max(1, int(COLS * h / w * 0.45))
small = out.resize((COLS, rows))
px = small.load()
for y in range(rows):
    line = ""
    for x in range(COLS):
        r, g, b = px[x, y]
        if r > 180 and g < 90 and b < 90:
            line += "X"          # FARQ
        elif 0.299 * r + 0.587 * g + 0.114 * b < 100:
            line += "#"          # to'q kontent
        else:
            line += " "          # oq
    print(line)
