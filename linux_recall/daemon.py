"""Periodic capture: every ``--interval`` seconds take a screenshot, keep it
only if the app changed or the active window looks different enough from the
last saved capture.

Similarity is dHash over the active window region (see ``similarity.py``).
Measured on this setup: identical 1.0, clock tick 0.988, one new text line
0.977, a new paragraph 0.93, 300px scroll 0.82, unrelated content 0.6-0.73.

Every cycle logs one line (journal) and appends one record to
``~/.cache/linux_recall/daemon.jsonl`` for later dedup statistics.
"""

import argparse
import json
import logging
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from linux_recall.capture import save, take
from linux_recall.ocr import CLOUD_TIMEOUT
from linux_recall.paths import CACHE_DIR, DATA_DIR
from linux_recall.similarity import similarity

log = logging.getLogger("linux_recall")

SCREENSAVER = DBusAddress("/ScreenSaver", bus_name="org.freedesktop.ScreenSaver",
                          interface="org.freedesktop.ScreenSaver")


def screen_locked() -> bool:
    with open_dbus_connection(bus="SESSION") as conn:
        return bool(conn.send_and_get_reply(new_method_call(SCREENSAVER, "GetActive"), timeout=2).body[0])


def last_saved(data_dir: Path) -> tuple[str | None, str] | None:
    """(app, dhash) of the newest capture on disk, so a restart doesn't re-save the same screen."""
    for path in sorted((data_dir / "captures").glob("*/*.json"), reverse=True):
        record = json.loads(path.read_text())
        if digest := record["screenshot"].get("dhash"):  # schema >= 3
            window = record["window"] or {}
            return window.get("desktop_file") or window.get("app_name"), digest
    return None


def record_cycle(path: Path, started: datetime, **fields: Any) -> None:
    fields = {"time": started.isoformat(timespec="seconds"), **fields}
    with path.open("a") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture the screen periodically, skipping near-duplicates.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--interval", type=float, default=60, help="seconds between screenshots")
    parser.add_argument("--threshold", type=float, default=0.95,
                        help="skip when same app and similarity >= this (0-1)")
    parser.add_argument("--no-ocr", dest="ocr", action="store_false")
    parser.add_argument("--ocr-timeout", type=float, default=CLOUD_TIMEOUT)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # stdout goes to the journal under systemd; no timestamps needed there
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    logging.getLogger("PIL").setLevel(logging.INFO)

    # finish the running cycle on SIGTERM (systemctl stop) instead of dying
    # between moving the image and writing its JSON
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    for leftover in CACHE_DIR.glob("*.webp"):  # shots taken() but never saved or dropped
        leftover.unlink()
    last = last_saved(args.data_dir)
    stats_path = CACHE_DIR / "daemon.jsonl"
    counts = {"keep": 0, "skip": 0}
    log.info("started: interval=%ss threshold=%s last=%s, cycle log %s",
             args.interval, args.threshold, last and last[0], stats_path)

    while not stop.is_set():
        start = time.monotonic()
        started = datetime.now().astimezone()
        try:
            if screen_locked():
                counts["skip"] += 1
                log.info("SKIP  screen locked  [kept %(keep)d, skipped %(skip)d]", counts)
                record_cycle(stats_path, started, decision="skip", reason="locked")
            else:
                shot = take()
                # similarity is informative even across apps, but only same-app shots get deduplicated
                sim = similarity(last[1], shot.dhash) if last else None
                same_app = bool(last) and last[0] == shot.app
                if same_app and sim is not None and sim >= args.threshold:
                    decision, reason = "skip", "similar"
                    shot.image.unlink()
                else:
                    decision = "keep"
                    reason = ("first" if not last else "changed" if same_app
                              else f"app {last[0]} -> {shot.app}")
                counts[decision] += 1
                log.info("%-5s %-12s similarity %s (threshold %.2f)  %s  [kept %d, skipped %d]",
                         decision.upper(), shot.app, "  -  " if sim is None else f"{sim:.3f}",
                         args.threshold, reason, counts["keep"], counts["skip"])
                json_path = None
                if decision == "keep":
                    json_path = save(shot, args.data_dir, "timer", args.ocr, args.ocr_timeout)
                    last = (shot.app, shot.dhash)
                record_cycle(stats_path, started, decision=decision, reason=reason, app=shot.app,
                             similarity=sim and round(sim, 4), threshold=args.threshold,
                             file=json_path and str(json_path))
        except Exception:
            log.exception("capture cycle failed")
        stop.wait(max(1.0, args.interval - (time.monotonic() - start)))
    log.info("stopped")


if __name__ == "__main__":
    main()
