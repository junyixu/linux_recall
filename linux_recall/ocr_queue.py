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
While the cloud is down, local RapidOCR (a few CPU-heavy seconds per shot)
only runs on shots older than ``LOCAL_AFTER``, when the user is idle and the
laptop is on mains power, and only if RapidOCR is installed.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests
from jeepney import DBusAddress
from jeepney.io.blocking import open_dbus_connection
from jeepney.wrappers import Properties

from linux_recall.capture import add_ocr, notify
from linux_recall.ocr import HAS_LOCAL
from linux_recall.paths import CACHE_DIR

log = logging.getLogger("linux_recall")

PENDING_DIR = CACHE_DIR / "pending"
MAX_PENDING = 200  # ~0.5 MB each (Spectacle writes lossy WebP); the oldest are dropped (their JSON keeps "ocr": null)
LOCAL_AFTER = 3600  # seconds a shot waits for the cloud before local OCR may take it
LOCAL_THREADS = 2  # ONNX Runtime threads: slower, but never saturates the machine
FIRST_PAUSE, LONGEST_PAUSE = 60, 1800

NETWORK_MANAGER = DBusAddress("/org/freedesktop/NetworkManager", bus_name="org.freedesktop.NetworkManager",
                              interface="org.freedesktop.NetworkManager")
NM_CONNECTIVITY_FULL = 4


def online() -> bool:
    """NetworkManager says the internet is reachable (or can't tell)."""
    try:
        with open_dbus_connection(bus="SYSTEM") as conn:
            reply = conn.send_and_get_reply(Properties(NETWORK_MANAGER).get("Connectivity"), timeout=2)
        return reply.body[0][1] == NM_CONNECTIVITY_FULL
    except Exception:
        return True


def on_mains() -> bool:
    """Plugged in, or a machine without a mains adapter (desktop)."""
    supplies = [p for p in Path("/sys/class/power_supply").glob("*")
                if (p / "type").read_text().strip() == "Mains"]
    return not supplies or any((p / "online").read_text().strip() == "1" for p in supplies)


def cpu_pressure() -> float:
    """% of the last 60 s some runnable task waited for a CPU (PSI ``some avg60``)."""
    try:
        some = Path("/proc/pressure/cpu").read_text().splitlines()[0]
    except OSError:
        return 0.0
    return float(dict(f.split("=") for f in some.split()[1:])["avg60"])


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


class OcrQueue:
    def __init__(self, cloud_timeout: float) -> None:
        self.cloud_timeout = cloud_timeout
        self.breaker = Breaker()
        self.idle = False  # set by the daemon loop every cycle
        self.wake = threading.Event()
        self.notified = False
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, name="ocr", daemon=True)

    def start(self) -> None:
        log.info("ocr queue: %d pending in %s", len(self._jobs()), PENDING_DIR)
        self.thread.start()

    def add(self, original: Path, json_path: Path, region: tuple[int, int, int, int] | None) -> None:
        """Move ``original`` into the queue; the capture's JSON gets its OCR later."""
        job = PENDING_DIR / f"{original.stem}.job.json"
        original.replace(job.with_name(original.name))
        tmp = job.with_suffix(".tmp")  # the worker must never read a half-written job
        tmp.write_text(json.dumps({"json": str(json_path), "region": region}))
        tmp.replace(job)
        for old in self._jobs()[:-MAX_PENDING]:
            log.warning("ocr queue full, dropping %s", old.name)
            self._remove(old)
        self.wake.set()

    def _jobs(self) -> list[Path]:
        return sorted(PENDING_DIR.glob("*.job.json"))  # ids sort by capture time

    @staticmethod
    def _remove(job: Path) -> None:
        job.with_name(job.name.removesuffix(".job.json") + ".webp").unlink(missing_ok=True)
        job.unlink(missing_ok=True)

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
        jobs = self._jobs()
        if not jobs:
            return False
        if self.breaker.closed and online():
            return self._ocr(jobs[-1], cloud=True)
        # cloud down: local OCR, but only when it won't get in the user's way
        old = [j for j in jobs if time.time() - j.stat().st_mtime >= LOCAL_AFTER]
        if old and HAS_LOCAL and self.idle and on_mains():
            return self._ocr(old[-1], cloud=False)
        return False

    def _ocr(self, job: Path, cloud: bool) -> bool:
        spec: dict[str, Any] = json.loads(job.read_text())
        json_path = Path(spec["json"])
        original = job.with_name(job.name.removesuffix(".job.json") + ".webp")
        if not original.exists() or not json_path.exists() or json.loads(json_path.read_text())["ocr"]:
            self._remove(job)  # capture deleted, or already OCRed before a crash
            return True
        region = spec["region"] and tuple(spec["region"])
        try:
            if cloud:
                add_ocr(json_path, original, region, cloud_timeout=self.cloud_timeout, engines=("cloud",))
            else:
                add_ocr(json_path, original, region, engines=("local",), local_threads=LOCAL_THREADS,
                        fallback_reason=self.breaker.reason or "cloud unreachable")
        except Exception as e:
            if not cloud:
                log.exception("local OCR failed for %s, dropping it", job.name)
                self._remove(job)
                return True
            reason = f"{type(e).__name__}: {e}"
            permanent = _permanent(e)
            self.breaker.fail(reason, permanent)
            if permanent and not self.notified:
                notify("linux_recall: cloud OCR unavailable", reason)
                self.notified = True
            return False
        if cloud:
            self.breaker.succeed()
        self._remove(job)
        return True
