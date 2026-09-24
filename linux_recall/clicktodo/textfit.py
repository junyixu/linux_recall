"""Fit OCR text onto the glyphs of the screenshot, one box per character.

PaddleOCR's line boxes and text are good, but its word boxes come from coarse
CTC steps and are not: left edges were a median 7px (up to 11px) right of the
glyphs on a Firefox window, "System Settings" came back with "System" twice
its width, and in a shell prompt "environment" started 12px (0.6 of a
monospace cell) late, so dragging from its "n" selected the "e". Click to Do needs character-accurate boxes, so each line is
refitted from the pixels, top-down:

1. drop icons: split the line's ink at gaps much wider than a space; blocks
   that overlap no word's Baidu range enough are icons (a button's icon)
2. line -> words: each space is the ink gap nearest to Baidu's position for
   it; not simply the widest gaps, since full-width punctuation like "），"
   is padded wider than a space
3. word -> characters: glyphs (runs of ink columns) are assigned to
   characters by dynamic programming (``_align``): a character may take
   several glyphs (CJK radicals), touching letters may share one, icons are
   skipped, and the spacing between characters should match Baidu's

A line that fails a step keeps Baidu's positions, split evenly per character.
"""

from typing import Any

import numpy as np
from PIL import Image

Box = list[float]  # [x0, y0, x1, y1]


def fit_lines(lines: list[dict[str, Any]], image: Image.Image) -> int:
    """Replace each line's ``words`` with one entry per character; returns how many
    lines were fitted to the pixels (the rest keep Baidu's positions)."""
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    fitted = 0
    for line in lines:
        chars = _split_chars(line["words"])
        if _fit_line(line["box"], chars, gray):
            fitted += 1
        line["words"] = [{"t": c["t"], "b": c["b"]} for c in chars]
    return fitted


def _advance(ch: str) -> float:
    """Rough advance width in em, to split a token's box among its characters."""
    if ord(ch) > 0x2E80 or ch in "，。：；！？（）“”‘’、《》【】":
        return 1.0  # CJK and full-width punctuation
    if ch in "ilIjtf.,:;!|'`[]()":
        return 0.3
    if ch in "mwMW@%":
        return 0.85
    if ch.isupper() or ch.isdigit():
        return 0.65
    return 0.55


def _split_chars(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tokens -> characters, sharing the token's box by rough advance width
    (an even split would give "W" and "i" the same width)."""
    chars = []
    for tok in tokens:
        x0, y0, x1, y1 = tok["b"]
        weights = [_advance(ch) for ch in tok["t"]]
        total, pos = sum(weights), x0
        for ch, wt in zip(tok["t"], weights):
            nxt = pos + (x1 - x0) * wt / total
            chars.append({"t": ch, "b": [pos, y0, nxt, y1]})
            pos = nxt
    return chars


def _runs(cols: np.ndarray, gap: float) -> list[tuple[int, int]]:
    """Group sorted column indices into [start, end) runs split at gaps > ``gap``."""
    if len(cols) == 0:
        return []
    cut = np.flatnonzero(np.diff(cols) > gap + 1)
    return list(zip(cols[np.r_[0, cut + 1]].tolist(), (cols[np.r_[cut, len(cols) - 1]] + 1).tolist()))


def _fit_line(box: Box, chars: list[dict[str, Any]], gray: np.ndarray) -> bool:
    x0, y0, x1, y1 = (int(v) for v in box)
    h = y1 - y0
    # skip the top eighth (descenders of the line above) but keep the bottom ("_" sits there)
    band = gray[y0 + h // 8:y1, x0:x1]
    if band.size == 0:
        return False
    edges = np.concatenate([band[:, :2].ravel(), band[:, -2:].ravel()])
    diff = np.abs(band - int(np.median(edges)))
    # grey-on-white text differs from its background by only ~55, black text by ~250
    contrast = float(np.percentile(diff, 99.5))
    if contrast < 20:
        return False
    cols = np.flatnonzero((diff > min(60, max(20, 0.35 * contrast))).any(axis=0)) + x0
    big = max(8, 0.35 * h)  # wider than any space between words

    # words: runs of non-space characters
    words: list[list[int]] = []
    for i, c in enumerate(chars):
        if c["t"].strip():
            if not words or not chars[i - 1]["t"].strip():
                words.append([])
            words[-1].append(i)
    if not words:
        return False

    # 1. drop icons: blocks (ink split at gaps much wider than a space) that overlap no word's
    #    Baidu range by 30% of the narrower of the two
    spans = [(chars[w[0]]["b"][0], chars[w[-1]]["b"][2]) for w in words]
    text_blocks = [(b0, b1) for b0, b1 in _runs(cols, big)
                   if any(min(wx1, b1) - max(wx0, b0) >= 0.3 * min(b1 - b0, wx1 - wx0) for wx0, wx1 in spans)]
    if not text_blocks:
        return False
    cols = np.concatenate([cols[(cols >= b0) & (cols < b1)] for b0, b1 in text_blocks])

    # 2. line -> words: each space is the gap nearest to where Baidu puts it. Not simply the
    #    widest gaps: full-width punctuation such as "），" carries padding wider than a space.
    gaps = np.diff(cols)
    candidates = np.flatnonzero(gaps > max(4, 0.12 * h))
    cuts = []
    for k in range(len(words) - 1):
        want = (chars[words[k][-1]]["b"][2] + chars[words[k + 1][0]]["b"][0]) / 2
        options = candidates[candidates >= (cuts[-1] + 1 if cuts else 0)]
        if not len(options):
            return False
        centers = (cols[options] + cols[options + 1]) / 2
        best = int(np.argmin(np.abs(centers - want)))
        if abs(centers[best] - want) > 1.5 * h:  # Baidu is off by far less than that
            return False
        cuts.append(int(options[best]))
    starts = cols[np.r_[0, np.array(cuts, dtype=int) + 1]]
    ends = cols[np.r_[np.array(cuts, dtype=int), len(cols) - 1]] + 1

    # 3. word -> characters
    for w, s0, s1 in zip(words, starts.tolist(), ends.tolist()):
        glyphs = _runs(cols[(cols >= s0) & (cols < s1)], 0)  # 1 blank column splits glyphs
        _fit_chars([chars[i] for i in w], glyphs, s0, s1, h)

    # spaces fill the gap between their neighbours
    for i, c in enumerate(chars):
        if not c["t"].strip():
            left = chars[i - 1]["b"][2] if i else c["b"][0]
            right = chars[i + 1]["b"][0] if i + 1 < len(chars) else c["b"][2]
            c["b"] = [left, c["b"][1], max(left, right), c["b"][3]]
    return True


def _fit_chars(chars: list[dict[str, Any]], glyphs: list[tuple[int, int]], s0: int, s1: int,
               h: int) -> None:
    spans = _align(glyphs, chars, h) if glyphs else None
    if spans is None:  # no consistent assignment: Baidu's positions stretched onto the word's ink
        bx0, bx1 = chars[0]["b"][0], chars[-1]["b"][2]
        scale = (s1 - s0) / max(1e-6, bx1 - bx0)
        spans = [(s0 + (c["b"][0] - bx0) * scale, s0 + (c["b"][2] - bx0) * scale) for c in chars]
    for c, (a, b) in zip(chars, spans):
        c["b"] = [a, c["b"][1], b, c["b"][3]]


def _align(glyphs: list[tuple[int, int]], chars: list[dict[str, Any]], h: int,
           max_parts: int = 4, max_share: int = 3) -> list[tuple[float, float]] | None:
    """Assign glyphs to characters by dynamic programming.

    Equal counts are not proof of a one-to-one match: in "机制：Windows" the
    two halves of 制 are two glyphs while the touching "ws" is one, so a
    one-to-one match shifts every later letter. Moves, from a state (chars
    placed, glyphs used):

    - merge: the next character takes 1-4 glyphs (CJK radicals, quotes)
    - share: the next 2-3 characters split one glyph by estimated width
      (touching letters)
    - skip: a glyph belongs to no character (an icon), penalised

    Each placed character costs how far its distance from the previous
    character differs from Baidu's (Baidu drifts by up to a character
    mid-line, but its spacing stays close), a little absolute drift, and
    half the difference from its estimated width (Baidu's word box shared
    out by ``_advance``); merged groups also pay for the gaps inside them.
    """
    n, m = len(chars), len(glyphs)
    centers = [(c["b"][0] + c["b"][2]) / 2 for c in chars]
    widths = [c["b"][2] - c["b"][0] for c in chars]
    skip_cost, share_cost, drift = 0.25 * h, 0.1 * h, 0.1

    def place(i: int, a: float, b: float, prev: float | None) -> float:
        c = (a + b) / 2
        spacing = abs(c - centers[0]) if prev is None else abs(c - prev - (centers[i] - centers[i - 1]))
        return spacing + drift * abs(c - centers[i]) + 0.5 * abs(b - a - widths[i])

    inf = float("inf")
    # best[i][j] = (cost, last center, previous (i, j), spans added by the move)
    best: list[list[tuple[float, float | None, tuple[int, int] | None, list]]] = [
        [(inf, None, None, [])] * (m + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, None, None, [])
    for i in range(n + 1):
        for j in range(m + 1):
            cost, prev, _, _ = best[i][j]
            if cost == inf:
                continue

            def relax(ni: int, nj: int, add: float, last: float | None, spans: list) -> None:
                if cost + add < best[ni][nj][0]:
                    best[ni][nj] = (cost + add, last, (i, j), spans)

            if j < m:  # skip glyph j
                relax(i, j + 1, skip_cost, prev, [])
            if i == n:
                continue
            for e in range(j + 1, min(j + max_parts, m) + 1):  # merge glyphs j..e-1 into char i
                a, b = glyphs[j][0], glyphs[e - 1][1]
                inner = sum(glyphs[k + 1][0] - glyphs[k][1] for k in range(j, e - 1))
                relax(i + 1, e, place(i, a, b, prev) + inner, (a + b) / 2, [(a, b)])
            if j < m:
                for t in range(2, min(max_share, n - i) + 1):  # chars i..i+t-1 share glyph j
                    a, b = glyphs[j]
                    total = sum(widths[i:i + t]) or 1
                    add, last, spans, x = share_cost * (t - 1), prev, [], a
                    for k in range(i, i + t):
                        nx = x + (b - a) * widths[k] / total
                        add += place(k, x, nx, last)
                        last, x = (x + nx) / 2, nx
                        spans.append((spans[-1][1] if spans else a, nx))
                    relax(i + t, j + 1, add, last, spans)
    if best[n][m][0] == inf:
        return None
    out: list[tuple[float, float]] = []
    state: tuple[int, int] | None = (n, m)
    while state and state != (0, 0):
        _, _, back, spans = best[state[0]][state[1]]
        out = spans + out
        state = back
    return out
