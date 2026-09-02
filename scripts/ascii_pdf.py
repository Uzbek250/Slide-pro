#!/usr/bin/env python3
"""PDF sahifasini ASCII-art qilib ko'rsatadi — layoutni 'ko'rish' uchun."""
import subprocess
import sys

from PIL import Image

PDF = sys.argv[1] if len(sys.argv) > 1 else "/tmp/async_test.pdf"
PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 1
COLS = int(sys.argv[3]) if len(sys.argv) > 3 else 100

subprocess.run(["pdftoppm", "-png", "-r", "36", "-f", str(PAGE), "-l", str(PAGE), PDF, "/tmp/ascii_page"],
               check=True, capture_output=True)
import glob
for old in glob.glob("/tmp/ascii_page-*.png"):
    pass  # eski fayllar qolishi mumkin — quyida eng yangisini olamiz
png = sorted(glob.glob("/tmp/ascii_page-*.png"), key=__import__("os").path.getmtime)[-1]
img = Image.open(png).convert("RGB")
W, H = img.size
rows = max(1, int(COLS * H / W * 0.45))  # terminal belgilari 2x1

# Rang sinflari: (ism, shart)
def classify(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < 70:
        return "#"          # to'q (matn)
    if lum > 235:
        return " "          # oq fon
    if r > 150 and g > 150 and b > 150:
        return "."          # och kulrang (karta foni)
    if r > 180 and g < 120 and b < 120:
        return "R"          # qizil
    if r < 130 and g > 140 and b > 140:
        return "C"          # yashil-ko'k / cyan
    if r < 120 and g < 120 and b > 150:
        return "B"          # ko'k
    if r > 150 and 110 < g < 190 and b < 110:
        return "O"          # to'q sariq
    if r > 110 and g > 150 and b < 120:
        return "G"          # yashil
    if abs(r - g) < 25 and abs(g - b) < 25:
        return "+"          # kulrang-to'q (matn antialiasing)
    return "?"

small = img.resize((COLS, rows))
px = small.load()
for y in range(rows):
    line = ""
    for x in range(COLS):
        r, g, b = px[x, y]
        line += classify(r, g, b)
    print(line)
