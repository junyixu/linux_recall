"""Click to Do: make the text in the active window selectable.

Hotkey -> ``spectacle -a -S`` (full resolution) -> PaddleOCR cloud with word
boxes -> an HTML page with the screenshot under a transparent text layer ->
Firefox. Select text there and copy it with Ctrl+C. Cloud only: any OCR
failure or timeout is reported and the command exits non-zero.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from PIL import Image

from linux_recall.capture import notify
from linux_recall.kwin import get_kwin_state
from linux_recall.ocr import CLOUD_TIMEOUT, MAX_SIDE, cloud_result
from linux_recall.paths import CACHE_DIR
from linux_recall.screenshot import take_screenshot
from linux_recall.textfit import fit_lines

OUT_DIR = CACHE_DIR / "clicktodo"
KEEP_SECONDS = 24 * 3600

log = logging.getLogger("linux_recall")


def text_lines(pruned: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{"text", "box", "words": [{"t": token, "b": [x0, y0, x1, y1]}]}]`` in image pixels.

    Joining a line's tokens gives its text back: spaces are tokens of their own.
    ``textfit.fit_lines`` later turns the tokens into single characters.
    """
    lines = []
    words = pruned.get("text_word") or [None] * len(pruned["rec_texts"])
    word_boxes = pruned.get("text_word_boxes") or [None] * len(pruned["rec_texts"])
    for text, box, tokens, boxes in zip(pruned["rec_texts"], pruned["rec_boxes"], words, word_boxes):
        if not text.strip():
            continue
        if not tokens:  # no word boxes: the whole line is one token
            tokens, boxes = [text], [box]
        lines.append({"text": text, "box": box,
                      "words": [{"t": t, "b": b} for t, b in zip(tokens, boxes)]})
    return reading_order(lines)


def reading_order(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order lines column by column, so a drag across lines stays in one column.

    OCR returns lines strictly top to bottom, interleaving e.g. a sidebar with
    the main text; the DOM follows that order and so would a selection. A line
    joins the column whose last line overlaps it horizontally and sits at most
    two line heights above it; columns are then read by their first line.
    """
    columns: list[list[dict[str, Any]]] = []
    for line in sorted(lines, key=lambda l: (l["box"][1], l["box"][0])):
        x0, y0, x1, y1 = line["box"]
        for col in columns:
            px0, py0, px1, py1 = col[-1]["box"]
            overlap = min(x1, px1) - max(x0, px0)
            if overlap > 0.3 * min(x1 - x0, px1 - px0) and y0 - py1 < 2 * (y1 - y0):
                col.append(line)
                break
        else:
            columns.append([line])
    columns.sort(key=lambda c: (c[0]["box"][1], c[0]["box"][0]))
    return [line for col in columns for line in col]


def write_page(path: Path, image: Path, data: dict[str, Any]) -> None:
    template = files("linux_recall").joinpath("clicktodo.html").read_text()
    payload = json.dumps({**data, "image": image.name}, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(template.replace("/*DATA*/null", payload))


def prune_old() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for f in OUT_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink()


def click_to_do(timeout: float) -> Path:
    now = datetime.now().astimezone()
    shot_id = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prune_old()

    window = get_kwin_state()["window"] or {}
    image = OUT_DIR / f"{shot_id}.webp"
    t = time.perf_counter()
    take_screenshot(image, "window")
    with Image.open(image) as im:
        im.load()
    size = im.size
    log.info("screenshot %dx%d of %s in %.2fs", *size, window.get("app_name"), time.perf_counter() - t)
    if max(size) > MAX_SIDE:
        raise RuntimeError(f"窗口太大（{size[0]}x{size[1]}），PaddleOCR 最多 {MAX_SIDE}px")

    notify("Click to Do", f"正在识别 {window.get('app_name') or '窗口'} 的文字…")
    t = time.perf_counter()
    pruned = cloud_result(image, timeout, word_boxes=True)
    # Baidu's raw result, kept for debugging textfit (pruned with the page after a day)
    (OUT_DIR / f"{shot_id}.ocr.json").write_text(json.dumps(pruned, ensure_ascii=False))
    lines = text_lines(pruned)
    log.info("ocr %d lines in %.2fs", len(lines), time.perf_counter() - t)
    fitted = fit_lines(lines, im)  # Baidu's word boxes are off by up to 1.5 characters
    log.info("%d/%d lines fitted to the glyphs", fitted, len(lines))
    if not lines:
        raise RuntimeError("没有识别到文字")

    page = OUT_DIR / f"{shot_id}.html"
    write_page(page, image, {
        "title": window.get("caption") or "",
        "app": window.get("app_name") or "?",
        "time": f"{now:%H:%M:%S}",
        "lines": lines,
    })
    subprocess.Popen(["firefox", "--new-window", page.as_uri()], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description="Select text from the active window (Click to Do).")
    parser.add_argument("--timeout", type=float, default=CLOUD_TIMEOUT,
                        help="seconds to wait for PaddleOCR before giving up")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(CACHE_DIR / "clicktodo.log"), logging.StreamHandler()],
    )
    try:
        page = click_to_do(args.timeout)
    except Exception as e:
        log.exception("click to do failed")
        notify("Click to Do 失败", f"{type(e).__name__}: {e}")
        sys.exit(1)
    print(page)


if __name__ == "__main__":
    main()
