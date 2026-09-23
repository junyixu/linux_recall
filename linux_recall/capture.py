"""One capture: active window context + screenshot + OCR -> ``<id>.webp`` and ``<id>.json``.

Meant to be bound to a global shortcut. The JSON sidecar is written twice:
right after the screenshot (``"ocr": null``) and again once OCR finishes, so
a crash in OCR never loses the capture itself.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image
from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from linux_recall.browser import get_active_tab, is_browser
from linux_recall.kwin import get_kwin_state
from linux_recall.ocr import CLOUD_TIMEOUT, run_ocr
from linux_recall.paths import CACHE_DIR, DATA_DIR
from linux_recall.screenshot import MODES, take_screenshot, window_region

SCHEMA_VERSION = 2  # 2: + "screens", OCR limited to the active window ("ocr.region")

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


def capture(data_dir: Path, mode: str, ocr: bool, trigger: str, ocr_timeout: float) -> Path:
    now = datetime.now().astimezone()
    capture_id = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"
    day_dir = data_dir / "captures" / f"{now:%Y-%m-%d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    image_path = day_dir / f"{capture_id}.webp"
    json_path = day_dir / f"{capture_id}.json"

    # Window first: it's ~50 ms, whereas Spectacle takes ~2 s to start.
    try:
        state = get_kwin_state()
    except Exception:
        log.exception("KWin query failed")
        state = {"window": None, "screens": None}
    window = state["window"]

    t = time.perf_counter()
    take_screenshot(image_path, mode)
    log.info("screenshot %s in %.2fs", image_path, time.perf_counter() - t)

    browser = None
    if window and is_browser(window):
        try:
            browser = get_active_tab(window)
        except Exception:
            log.exception("browser tab query failed")

    with Image.open(image_path) as im:
        width, height = im.size

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": capture_id,
        "captured_at": now.isoformat(timespec="milliseconds"),
        "trigger": trigger,
        "screenshot": {"file": image_path.name, "mode": mode, "width": width, "height": height},
        "screens": state["screens"],
        "window": window,
        "browser": browser,
        "ocr": None,
    }
    write_json(json_path, record)

    if not ocr:
        return json_path
    # OCR only the active window; "window"/"monitor" screenshots are small enough as a whole
    region = None
    if mode == "fullscreen":
        region = window and state["screens"] and window_region(window, state["screens"], (width, height))
        if not region:
            log.warning("no active window region, skipping OCR")
            return json_path
    try:
        record["ocr"] = run_ocr(image_path, region, ocr_timeout)
    except Exception:
        log.exception('OCR failed; capture kept with "ocr": null')
        return json_path
    write_json(json_path, record)
    log.info("ocr (%s) %d lines in %.2fs", record["ocr"]["engine"], len(record["ocr"]["lines"]), record["ocr"]["elapsed_s"])
    return json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Take one Recall capture.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--mode", choices=MODES, default="fullscreen")
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
        json_path = capture(args.data_dir, args.mode, args.ocr, args.trigger, args.ocr_timeout)
    except Exception as e:
        log.exception("capture failed")
        if args.notify:
            notify("Capture failed", str(e))
        sys.exit(1)

    if args.notify:
        record = json.loads(json_path.read_text())
        window, browser = record["window"] or {}, record["browser"] or {}
        notify("Captured", browser.get("url") or window.get("app_name") or json_path.stem)
    print(json_path)


if __name__ == "__main__":
    main()
