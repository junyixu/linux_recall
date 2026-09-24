"""One capture: active window context + screenshot + OCR -> ``<id>.webp`` and ``<id>.json``.

``take()`` grabs the screenshot and context into the cache dir; ``save()``
writes a copy downscaled to logical pixels (HiDPI screens make captures 2x
per side) into the captures dir and OCRs the full-resolution original, since
downscaled small text loses most OCR lines (105 -> 42 on a kitty window). The hotkey does both at once,
the daemon (``daemon.py``) drops shots too similar to the last saved one.

The JSON sidecar is written twice:
right after the screenshot (``"ocr": null``) and again once OCR finishes, so
a crash in OCR never loses the capture itself.
"""

import argparse
import json
import logging
import sys
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image
from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from linux_recall.apps.anki import get_anki_context, is_anki
from linux_recall.apps.browser import get_active_tab, is_browser
from linux_recall.apps.kitty import get_kitty_context, is_kitty
from linux_recall.apps.nvim import get_neovide_context, is_neovide
from linux_recall.apps.obsidian import get_open_note, is_obsidian
from linux_recall.apps.zotero import get_open_item, is_zotero
from linux_recall.exclude import excluded
from linux_recall.kwin import get_kwin_state
from linux_recall.ocr import CLOUD_TIMEOUT, run_ocr
from linux_recall.paths import CACHE_DIR, DATA_DIR
from linux_recall.screenshot import MODES, take_screenshot, window_region
from linux_recall.similarity import dhash

SCHEMA_VERSION = 11  # 2: + "screens", OCR only the active window; 3: + screenshot.window_region/dhash;
                    # 4: default mode "window", image downscaled (screenshot.scale), boxes in saved pixels;
                    # 5: + "zotero" (item/page open in Zotero's reader); 6: + "anki" (card under review)
                    # 7: + "kitty" (focused tab/window, foreground processes, Neovim file)
                    # 8: + kitty.nvim.windows (visible buffer text of each Neovim window)
                    # 9: + "neovide" ({"nvim": ...}, same fields as kitty.nvim)
                    # 10: + kitty.shell (last command + output), kitty.claude (Claude Code session)
                    # 11: + "obsidian" (active note, visible source lines)

log = logging.getLogger("linux_recall")


def write_json(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    tmp.replace(path)


def notify(summary: str, body: str) -> None:
    addr = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                       interface="org.freedesktop.Notifications")
    msg = new_method_call(addr, "Notify", "susssasa{sv}i",
                          ("linux_recall", 0, "camera-photo", summary, body, [], {}, 3000))
    with open_dbus_connection(bus="SESSION") as conn:
        conn.send_and_get_reply(msg, timeout=2)


class Excluded(Exception):
    """The active window is on the exclusion list (``exclude.py``); nothing was captured."""


@dataclass
class Shot:
    """A screenshot plus its context, not yet saved (the image sits in the cache dir)."""

    captured_at: datetime
    id: str
    image: Path
    mode: str
    size: tuple[int, int]
    state: dict[str, Any]
    browser: dict[str, Any] | None
    zotero: dict[str, Any] | None
    anki: dict[str, Any] | None
    kitty: dict[str, Any] | None
    neovide: dict[str, Any] | None
    obsidian: dict[str, Any] | None
    region: tuple[int, int, int, int] | None  # active window in screenshot pixels
    dhash: str  # of the active window region, or of the whole image
    device_scale: float  # screenshot pixels per logical pixel (the output scale, e.g. 2)

    @property
    def app(self) -> str | None:
        window = self.state["window"]
        return window and (window.get("desktop_file") or window.get("app_name"))


def take(mode: str = "window") -> Shot:
    now = datetime.now().astimezone()
    shot_id = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"

    # Window first: it's ~50 ms, whereas Spectacle takes ~2 s to start.
    try:
        state = get_kwin_state()
    except Exception:
        log.exception("KWin query failed")
        state = {"window": None, "screens": None}
    window = state["window"]
    # the URL before the screenshot too, so excluded sites are never captured
    browser = None
    if window and is_browser(window):
        try:
            browser = get_active_tab(window)
        except Exception:
            log.exception("browser tab query failed")
    if reason := excluded(window, browser):
        raise Excluded(reason)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    image = CACHE_DIR / f"{shot_id}.webp"
    t = time.perf_counter()
    take_screenshot(image, mode)
    log.debug("screenshot in %.2fs", time.perf_counter() - t)

    zotero = None
    if window and is_zotero(window):
        try:
            zotero = get_open_item(window)
        except Exception:
            log.exception("Zotero query failed")
    anki = None
    if window and is_anki(window):
        try:
            anki = get_anki_context(window)
        except Exception:
            log.exception("AnkiConnect query failed")
    kitty = None
    if window and is_kitty(window):
        try:
            kitty = get_kitty_context(window)
        except Exception:
            log.exception("kitty remote control query failed")
    neovide = None
    if window and is_neovide(window):
        try:
            neovide = get_neovide_context(window)
        except Exception:
            log.exception("Neovide query failed")
    obsidian = None
    if window and is_obsidian(window):
        try:
            obsidian = get_open_note(window)
        except Exception:
            log.exception("Obsidian CLI query failed")

    with Image.open(image) as im:
        size = im.size
        region = None
        if mode == "fullscreen" and window and state["screens"]:
            region = window_region(window, state["screens"], size)
        digest = dhash(im.crop(region) if region else im)
    return Shot(now, shot_id, image, mode, size, state, browser, zotero, anki, kitty, neovide, obsidian,
                region, digest,
                _device_scale(mode, size, state))


def _device_scale(mode: str, size: tuple[int, int], state: dict[str, Any]) -> float:
    screens, window = state["screens"], state["window"]
    if not screens:
        return 1.0
    if mode == "fullscreen":  # Spectacle renders the whole desktop at the highest output scale
        x0 = min(s["geometry"]["x"] for s in screens)
        x1 = max(s["geometry"]["x"] + s["geometry"]["width"] for s in screens)
        return size[0] / (x1 - x0)
    # window / monitor: native pixels of the output the window is on
    output = window and window.get("output")
    return next((s["scale"] for s in screens if s["name"] == output), 1.0)


def _scale_ocr(ocr: dict[str, Any], factor: float) -> dict[str, Any]:
    """Map OCR boxes/region from full-resolution to saved-image pixels."""
    ocr["region"] = {k: round(v * factor) for k, v in ocr["region"].items()}
    for line in ocr["lines"]:
        line["box"] = [[round(x * factor), round(y * factor)] for x, y in line["box"]]
    return ocr


def save(shot: Shot, data_dir: Path, trigger: str, ocr: bool, ocr_timeout: float,
         full_res: bool = False) -> Path:
    """Save the shot (downscaled unless ``full_res``) into the captures dir, write its JSON,
    then OCR the full-resolution original and delete it."""
    try:
        return _save(shot, data_dir, trigger, ocr, ocr_timeout, full_res)
    finally:
        shot.image.unlink(missing_ok=True)


def _save(shot: Shot, data_dir: Path, trigger: str, ocr: bool, ocr_timeout: float,
          full_res: bool) -> Path:
    day_dir = data_dir / "captures" / f"{shot.captured_at:%Y-%m-%d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    image_path = day_dir / shot.image.name
    json_path = image_path.with_suffix(".json")

    factor = 1.0 if full_res or shot.device_scale <= 1 else 1 / shot.device_scale
    if factor == 1.0:
        shutil.copyfile(shot.image, image_path)
        size = shot.size
    else:
        size = (round(shot.size[0] * factor), round(shot.size[1] * factor))
        with Image.open(shot.image) as im:
            im.resize(size, Image.Resampling.LANCZOS).save(image_path, quality=80)
    log.info("saved %s %dx%d (%s)", image_path, *size, shot.app)
    region = shot.region and tuple(round(v * factor) for v in shot.region)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": shot.id,
        "captured_at": shot.captured_at.isoformat(timespec="milliseconds"),
        "trigger": trigger,
        "screenshot": {
            "file": image_path.name, "mode": shot.mode, "width": size[0], "height": size[1],
            # saved pixels per captured pixel; OCR boxes and window_region are in saved pixels
            "scale": round(factor, 4), "captured_width": shot.size[0], "captured_height": shot.size[1],
            "window_region": region and dict(zip(("left", "top", "right", "bottom"), region)),
            "dhash": shot.dhash,
        },
        "screens": shot.state["screens"],
        "window": shot.state["window"],
        "browser": shot.browser,
        "zotero": shot.zotero,
        "anki": shot.anki,
        "kitty": shot.kitty,
        "neovide": shot.neovide,
        "obsidian": shot.obsidian,
        "ocr": None,
    }
    write_json(json_path, record)

    if not ocr:
        return json_path
    # OCR only the active window; "window"/"monitor" screenshots are small enough as a whole
    if shot.mode == "fullscreen" and not shot.region:
        log.warning("no active window region, skipping OCR")
        return json_path
    try:
        record["ocr"] = _scale_ocr(run_ocr(shot.image, shot.region, ocr_timeout), factor)
    except Exception:
        log.exception('OCR failed; capture kept with "ocr": null')
        return json_path
    write_json(json_path, record)
    log.info("ocr (%s) %d lines in %.2fs", record["ocr"]["engine"], len(record["ocr"]["lines"]), record["ocr"]["elapsed_s"])
    return json_path


def capture(data_dir: Path, mode: str, ocr: bool, trigger: str, ocr_timeout: float,
            full_res: bool = False) -> Path:
    return save(take(mode), data_dir, trigger, ocr, ocr_timeout, full_res)


def main() -> None:
    parser = argparse.ArgumentParser(description="Take one Recall capture.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--mode", choices=MODES, default="window",
                        help="window: only the active window (default); fullscreen: whole desktop")
    parser.add_argument("--full-res", action="store_true",
                        help="keep HiDPI resolution instead of downscaling to logical pixels")
    parser.add_argument("--no-ocr", dest="ocr", action="store_false")
    parser.add_argument("--ocr-timeout", type=float, default=CLOUD_TIMEOUT,
                        help="seconds to wait for cloud OCR before falling back to local")
    parser.add_argument("--no-notify", dest="notify", action="store_false")
    parser.add_argument("--trigger", default="hotkey", help="recorded as-is in the JSON")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(CACHE_DIR / "capture.log"), logging.StreamHandler()],
    )
    try:
        json_path = capture(args.data_dir, args.mode, args.ocr, args.trigger, args.ocr_timeout, args.full_res)
    except Excluded as e:
        log.info("excluded window (%s), not captured", e)
        if args.notify:
            notify("Not captured", f"Excluded window: {e}")
        return
    except Exception as e:
        log.exception("capture failed")
        if args.notify:
            notify("Capture failed", str(e))
        sys.exit(1)

    if args.notify:
        record = json.loads(json_path.read_text())
        window, browser, zotero = record["window"] or {}, record["browser"] or {}, record["zotero"] or {}
        page = f" p.{zotero['page']}" if zotero.get("page") else ""
        anki = record["anki"] or {}
        kitty = record["kitty"] or {}
        nvim = (kitty or record["neovide"] or {}).get("nvim") or {}
        claude, shell = kitty.get("claude") or {}, kitty.get("shell") or {}
        obsidian = record["obsidian"] or {}
        notify("Captured", browser.get("url") or (zotero.get("title") and zotero["title"] + page)
               or (anki.get("deck") and f"Anki: {anki['deck']}")
               or (nvim.get("file") and f"nvim: {nvim['file']}:{nvim['line']}")
               or (obsidian.get("file") and f"Obsidian: {obsidian['file']}")
               or (claude.get("title") and f"Claude: {claude['title']}")
               or (shell.get("cmdline") and f"$ {shell['cmdline']}")
               or window.get("app_name") or json_path.stem)
    print(json_path)


if __name__ == "__main__":
    main()
