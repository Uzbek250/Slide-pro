#!/usr/bin/env python3
"""PDF layout sifatini tekshiradi: matn qoplashmasi, chegaradan chiqish, bo'sh joylar."""
import re
import subprocess
import sys
from collections import defaultdict

PDF = sys.argv[1] if len(sys.argv) > 1 else "/tmp/async_test.pdf"


def words_of_page(pdf, page):
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), pdf, "-"],
        capture_output=True, text=True,
    ).stdout
    words = []
    for m in re.finditer(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
        out,
    ):
        x0, y0, x1, y1 = map(float, m.groups()[:4])
        words.append((x0, y0, x1, y1, m.group(5)))
    return words


def overlap(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (x1 - x0) * (y1 - y0)
    return area / min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


info = subprocess.run(["pdfinfo", PDF], capture_output=True, text=True).stdout
pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
psize = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
pw, ph = float(psize.group(1)), float(psize.group(2))
print(f"PDF: {pages} sahifa, {pw:.0f}x{ph:.0f}pt\n")

total_issues = 0
for p in range(1, pages + 1):
    words = words_of_page(PDF, p)
    issues = []
    # 1) Sahifa chegarasidan chiqqan matn
    for w in words:
        if w[2] > pw + 1 or w[3] > ph + 1 or w[0] < -1 or w[1] < -1:
            issues.append(f"  CHIQIB KETGAN: {w[4][:40]!r} @({w[0]:.0f},{w[1]:.0f})-({w[2]:.0f},{w[3]:.0f})")
    # 2) Qoplashma (ikki so'z bir-birini 40%+ yopadi)
    for i in range(len(words)):
        for j in range(i + 1, len(words)):
            ov = overlap(words[i], words[j])
            if ov > 0.4:
                issues.append(f"  QOPLASHMA ({ov:.0%}): {words[i][4][:25]!r} vs {words[j][4][:25]!r}")
    # 3) Judayam bo'sh sahifa (< 5 so'z)
    if len(words) < 5:
        issues.append(f"  BO'SH SAHIFA: {len(words)} so'z")
    if issues:
        total_issues += len(issues)
        print(f"--- Sahifa {p} ({len(words)} so'z) ---")
        for it in issues[:8]:
            print(it)

print(f"\nJami muammo: {total_issues}" if total_issues else "\nMUAMMO TOPILMADI ✓")
