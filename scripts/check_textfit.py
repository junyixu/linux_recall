"""Regression check for linux_recall/clicktodo/textfit.py.

Refits every Click to Do page in ~/.cache/linux_recall/clicktodo that has a
raw OCR result (<id>.ocr.json) and flags lines that look grossly wrong:

- SQUEEZED: the fitted characters cover < 70% of the line's ink (a line
  squeezed into part of its box; false positives: an icon inside the box)
- moved: a character's center is > 1.5 character widths from Baidu's
  estimate (Baidu is off by less than one character)

    uv run python scripts/check_textfit.py [-v]

Flagged lines still need a look: render them with the text layer shown.
"""

import glob
import json
import os
import sys

import numpy as np
from PIL import Image

from linux_recall.clicktodo import text_lines
from linux_recall.paths import CACHE_DIR
from linux_recall.clicktodo.textfit import _split_chars, fit_lines


def check(raw: dict, image_path: str) -> tuple[int, int, list[str]]:
    img = Image.open(image_path)
    img.load()
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    lines = text_lines(raw)
    base = [_split_chars(l["words"]) for l in lines]
    fitted = json.loads(json.dumps(lines))
    n = fit_lines(fitted, img)
    bad = []
    for l, b, f in zip(lines, base, fitted):
        assert "".join(w["t"] for w in f["words"]) == l["text"], "fitting changed the text"
        x0, y0, x1, y1 = l["box"]
        h = y1 - y0
        solid = [(bc, fc) for bc, fc in zip(b, f["words"]) if fc["t"].strip()]
        if not solid:
            continue
        band = gray[y0 + h // 8:y1, x0:x1]
        diff = np.abs(band - int(np.median(np.concatenate([band[:, :2].ravel(), band[:, -2:].ravel()]))))
        cols = np.flatnonzero((diff > min(60, max(20, 0.35 * np.percentile(diff, 99.5)))).any(axis=0)) + x0
        first, last = solid[0][1]["b"][0], solid[-1][1]["b"][2]
        squeezed = len(cols) and (last - first) < 0.7 * (cols[-1] + 1 - cols[0])
        moved = [fc["t"] for bc, fc in solid
                 if abs((fc["b"][0] + fc["b"][2]) / 2 - (bc["b"][0] + bc["b"][2]) / 2)
                 > 1.5 * max(bc["b"][2] - bc["b"][0], 0.3 * h)]
        if squeezed or moved:
            bad.append(f"    {l['text'][:40]!r} fitted {first:.0f}-{last:.0f}"
                       + (" SQUEEZED" if squeezed else "") + (f" moved: {''.join(moved)[:20]}" if moved else ""))
    return n, len(lines), bad


def main() -> None:
    verbose = "-v" in sys.argv
    totals = [0, 0, 0]
    for raw_path in sorted(glob.glob(str(CACHE_DIR / "clicktodo" / "*.ocr.json"))):
        n, total, bad = check(json.load(open(raw_path)), raw_path.replace(".ocr.json", ".webp"))
        print(f"{os.path.basename(raw_path)[:19]}  fitted {n:3}/{total:3}  suspicious {len(bad)}")
        for line in bad[: None if verbose else 5]:
            print(line)
        totals = [totals[0] + n, totals[1] + total, totals[2] + len(bad)]
    print(f"TOTAL fitted {totals[0]}/{totals[1]}, suspicious {totals[2]}")


if __name__ == "__main__":
    main()
