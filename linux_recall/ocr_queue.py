"""Background OCR for the daemon, so a slow cloud queue never delays the next screenshot.

Each kept shot is saved at once with ``"ocr": null``; its full-resolution
original goes to ``~/.cache/linux_recall/pending/`` as ``<id>.webp`` plus
``<id>.job.json`` (the capture's JSON path and OCR region). A worker thread
OCRs the newest pending shot, writes the result into the capture's JSON and
deletes both files. Pending files survive a restart, so nothing is lost when
the daemon stops mid-OCR.

Cloud failures open a circuit breaker: no cloud calls for 1 min, doubling up
to 30 min; the first shot after the pause is the probe. Missing token and
HTTP 401/403 won't fix themselves, so they pause the longest and notify once.
On a metered connection (``--cloud-ocr unmetered``, the default) the cloud
isn't tried at all, and hotkey captures queue their originals here too.

Without the cloud, local RapidOCR (~22 s of CPU per 3840x2080 window) only
runs while the screen is locked and the laptop is on mains power, and only if
RapidOCR is installed; unlocking kills the running OCR within 0.5 s and the
shot stays queued. A merely failing cloud first gets ``LOCAL_AFTER`` to
recover; a metered or disabled one won't, so its shots go local right away.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests
from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from linux_recall.capture import add_ocr, notify
from linux_recall.ocr import HAS_LOCAL, Interrupted, cloud_blocked, online
from linux_recall.paths import CACHE_DIR

log = logging.getLogger("linux_recall")

PENDING_DIR = CACHE_DIR / "pending"
# ~0.35 MB each (Spectacle's lossy WebP, ~3x the saved copy): a couple of days of shots waiting
# for the screen to lock; the oldest are dropped (their JSON keeps "ocr": null)
MAX_PENDING = 1500
LOCAL_AFTER = 3600  # seconds a failing cloud gets before local OCR may take a shot
# ONNX Runtime threads; per 3840x2080 window, 2/4/8 threads take 8.6/5.3/4.2 s and
# 17.6/22.3/29.0 s of CPU: past 4 (the P-cores) extra threads mostly burn CPU
LOCAL_THREADS = 4
FIRST_PAUSE, LONGEST_PAUSE = 60, 1800

SCREENSAVER = DBusAddress("/ScreenSaver", bus_name="org.freedesktop.ScreenSaver",
                          interface="org.freedesktop.ScreenSaver")


def screen_locked() -> bool:
    with open_dbus_connection(bus="SESSION") as conn:
        return bool(conn.send_and_get_reply(new_method_call(SCREENSAVER, "GetActive"), timeout=2).body[0])


def _unlocked() -> bool:
    """Stop local OCR once the user is back (or the lock state can't be read)."""
    try:
        return not screen_locked()
    except Exception:
        return True


def on_mains() -> bool:
    """Plugged in, or a machine without a mains adapter (desktop)."""
    supplies = [p for p in Path("/sys/class/power_supply").glob("*")
                if (p / "type").read_text().strip() == "Mains"]
    return not supplies or any((p / "online").read_text().strip() == "1" for p in supplies)


def _permanent(e: Exception) -> bool:
    """Errors that retrying won't fix: missing token, rejected token."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code in (401, 403)
    return isinstance(e, RuntimeError) and "PADDLEOCR_TOKEN" in str(e)


class Breaker:
    """Skip the cloud for a while after it fails, doubling the pause on each failure."""

    def __init__(self) -> None:
        self.pause = 0.0
        self.until = 0.0
        self.reason: str | None = None

    @property
    def closed(self) -> bool:
        return time.monotonic() >= self.until

    def fail(self, reason: str, permanent: bool = False) -> None:
        self.pause = LONGEST_PAUSE if permanent else min(max(self.pause * 2, FIRST_PAUSE), LONGEST_PAUSE)
        self.until = time.monotonic() + self.pause
        self.reason = reason
        log.warning("cloud OCR paused for %ds: %s", self.pause, reason)

    def succeed(self) -> None:
        if self.pause:
            log.info("cloud OCR is back")
        self.pause, self.until, self.reason = 0.0, 0.0, None


def _jobs() -> list[Path]:
    return sorted(PENDING_DIR.glob("*.job.json"))  # ids sort by capture time


def _remove(job: Path) -> None:
    job.with_name(job.name.removesuffix(".job.json") + ".webp").unlink(missing_ok=True)
    job.unlink(missing_ok=True)


def enqueue(original: Path, json_path: Path, region: tuple[int, int, int, int] | None) -> None:
    """Move ``original`` into the queue; the capture's JSON gets its OCR later (from the daemon)."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    job = PENDING_DIR / f"{original.stem}.job.json"
    original.replace(job.with_name(original.name))
    tmp = job.with_suffix(".tmp")  # the worker must never read a half-written job
    tmp.write_text(json.dumps({"json": str(json_path), "region": region}))
    tmp.replace(job)
    for old in _jobs()[:-MAX_PENDING]:
        log.warning("ocr queue full, dropping %s", old.name)
        _remove(old)


class OcrQueue:
    def __init__(self, cloud_timeout: float, cloud: str) -> None:
        self.cloud_timeout = cloud_timeout
        self.cloud = cloud  # one of ocr.CLOUD_POLICIES
        self.breaker = Breaker()
        self.wake = threading.Event()
        self.notified = False
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, name="ocr", daemon=True)

    def start(self) -> None:
        log.info("ocr queue: %d pending in %s", len(_jobs()), PENDING_DIR)
        self.thread.start()

    def add(self, original: Path, json_path: Path, region: tuple[int, int, int, int] | None) -> None:
        enqueue(original, json_path, region)
        self.wake.set()

    def _run(self) -> None:
        while True:  # daemon thread: dies with the process; an unfinished job stays pending
            try:
                busy = self._step()
            except Exception:
                log.exception("ocr queue step failed")
                busy = False
            if not busy:
                self.wake.wait(60)
                self.wake.clear()

    def _step(self) -> bool:
        """OCR one pending shot; False when there's nothing to do right now."""
        jobs = _jobs()
        if not jobs:
            return False
        blocked = cloud_blocked(self.cloud)
        if not blocked and self.breaker.closed and online():
            return self._ocr(jobs[-1])
        # no cloud: local OCR, but only while nobody is at the machine
        if not (HAS_LOCAL and on_mains() and screen_locked()):
            return False
        ready = [j for j in jobs if blocked or time.time() - j.stat().st_mtime >= LOCAL_AFTER]
        return bool(ready) and self._ocr(ready[-1], local=blocked or self.breaker.reason or "cloud unreachable")

    def _ocr(self, job: Path, local: str | None = None) -> bool:
        """OCR ``job`` in the cloud, or locally when ``local`` says why not the cloud."""
        spec: dict[str, Any] = json.loads(job.read_text())
        json_path = Path(spec["json"])
        original = job.with_name(job.name.removesuffix(".job.json") + ".webp")
        if not original.exists() or not json_path.exists() or json.loads(json_path.read_text())["ocr"]:
            _remove(job)  # capture deleted, or already OCRed before a crash
            return True
        region = spec["region"] and tuple(spec["region"])
        try:
            if local:
                add_ocr(json_path, original, region, engines=("local",), local_threads=LOCAL_THREADS,
                        fallback_reason=local, stop=_unlocked)
            else:
                add_ocr(json_path, original, region, cloud_timeout=self.cloud_timeout, engines=("cloud",))
        except Interrupted:
            log.info("screen unlocked, local OCR of %s stopped; it stays queued", job.name)
            return False
        except Exception as e:
            if local:
                log.exception("local OCR failed for %s, dropping it", job.name)
                _remove(job)
                return True
            reason = f"{type(e).__name__}: {e}"
            permanent = _permanent(e)
            self.breaker.fail(reason, permanent)
            if permanent and not self.notified:
                notify("linux_recall: cloud OCR unavailable", reason)
                self.notified = True
            return False
        if not local:
            self.breaker.succeed()
        _remove(job)
        return True
