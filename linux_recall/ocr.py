"""OCR of the active window: PaddleOCR cloud first, local RapidOCR as fallback.

Cloud: PP-OCRv6 on Baidu AI Studio, needs ``PADDLEOCR_TOKEN``. Only the
active window's region is uploaded: detection runs at the service default
(longest side 960), which reads a window crop fine (~100 lines) but leaves a
whole multi-monitor desktop nearly empty. Raising ``textDetLimitSideLen``
(tried 3938/4000/6098) makes the job fail with HTTP 500, so we don't. Based
on ``~/WorkSpace/anki_agent/anki_ocr_paddle.py``.

Local: RapidOCR (PaddleOCR models on ONNX Runtime), used whenever the cloud
misses its deadline or errors out — its queue can stall for minutes. It runs
in a child process: ONNX Runtime keeps ~400 MB after the first run, which the
long-running daemon would otherwise hold for good. RapidOCR is optional
(the AUR package only suggests it); without it, only the cloud is tried.
"""

import json
import logging
import multiprocessing
import os
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from linux_recall.paths import CACHE_DIR

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PP-OCRv6"
MAX_SIDE = 4000  # server-side max_side_limit; larger crops get shrunk first
QUEUE_FULL = 10010
CLOUD_TIMEOUT = 60.0
HAS_LOCAL = find_spec("rapidocr") is not None

log = logging.getLogger("linux_recall")

# (text, score, 4 corner points) in crop pixels
Line = tuple[str, float, list[list[float]]]


def _remaining(deadline: float, cap: float = 30.0) -> float:
    """Per-request timeout so a slow upload or poll can't overrun the overall deadline."""
    return max(1.0, min(cap, deadline - time.monotonic()))


def _headers() -> dict[str, str]:
    token = os.environ.get("PADDLEOCR_TOKEN")
    if not token:
        raise RuntimeError("PADDLEOCR_TOKEN is not set")
    return {"Authorization": f"bearer {token}"}


def _submit(image_path: Path, deadline: float, word_boxes: bool = False) -> str:
    optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useTextlineOrientation": False,
        # per-token boxes: English words, single CJK characters, and spaces as their own tokens
        "returnWordBox": word_boxes,
    }
    data = {"model": MODEL, "optionalPayload": json.dumps(optional_payload)}
    delay = 5
    while True:
        # no 30 s cap: the upload is one sendall() under a single timeout, and the
        # server sometimes takes only ~40 KB/s (a 0.6 MB window WebP needs ~15 s)
        with open(image_path, "rb") as f:
            resp = requests.post(JOB_URL, headers=_headers(), data=data, files={"file": f},
                                 timeout=_remaining(deadline, cap=deadline))
        # 400 + code 10010 "任务提交队列已满，请稍后重试": the shared queue is busy, not our fault
        if resp.status_code == 400 and resp.json().get("code") == QUEUE_FULL:
            if time.monotonic() + delay > deadline:
                raise TimeoutError("PaddleOCR submit queue stayed full")
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp.json()["data"]["jobId"]


# the job itself takes ~1-10 s, but it can sit in the shared queue for minutes
def _wait(job_id: str, deadline: float, interval: float = 2) -> dict[str, Any]:
    state = "submitted"
    while time.monotonic() < deadline:
        resp = requests.get(f"{JOB_URL}/{job_id}", headers=_headers(), timeout=_remaining(deadline))
        resp.raise_for_status()
        data = resp.json()["data"]
        state = data["state"]
        if state == "done":
            return data
        if state == "failed":
            raise RuntimeError(f"PaddleOCR job {job_id} failed: {data.get('errorMsg')}")
        time.sleep(interval)
    raise TimeoutError(f"PaddleOCR job {job_id} still {state!r} at deadline")


def cloud_result(image_path: Path, timeout: float, word_boxes: bool = False) -> dict[str, Any]:
    """Raw PaddleOCR ``prunedResult`` (``rec_texts``, ``rec_polys``, ``text_word``, ...);
    raises on any failure or when ``timeout`` seconds pass."""
    deadline = time.monotonic() + timeout
    job = _wait(_submit(image_path, deadline, word_boxes), deadline)
    resp = requests.get(job["resultUrl"]["jsonUrl"], timeout=_remaining(deadline))
    resp.raise_for_status()
    return json.loads(resp.text.splitlines()[0])["result"]["ocrResults"][0]["prunedResult"]


def _cloud(image_path: Path, timeout: float) -> list[Line]:
    pruned = cloud_result(image_path, timeout)
    return list(zip(pruned["rec_texts"], pruned["rec_scores"], pruned["rec_polys"]))


def _local(image_path: Path, max_side: int, threads: int) -> list[Line]:
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
        return pool.submit(_local_in_process, image_path, max_side, threads).result()


def _local_in_process(image_path: Path, max_side: int, threads: int) -> list[Line]:
    from rapidocr import RapidOCR  # heavy import, only needed on fallback

    engine = RapidOCR(params={
        # RapidOCR downscales to 2000px by default, blurring small text on HiDPI crops
        "Global.max_side_len": max_side, "Global.log_level": "warning",
        # -1 = all cores: ~29 s of CPU in ~4 s on a 3840x2080 window
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
    })
    result = engine(str(image_path))
    if result.txts is None:
        return []
    return [(t, s, b.tolist()) for t, s, b in zip(result.txts, result.scores, result.boxes)]


def run_ocr(image_path: Path, region: tuple[int, int, int, int] | None = None,
            cloud_timeout: float = CLOUD_TIMEOUT, engines: Sequence[str] = ("cloud", "local"),
            local_threads: int = -1, fallback_reason: str | None = None) -> dict[str, Any]:
    """OCR ``region`` (left, top, right, bottom in screenshot pixels) of the image, or all of it.

    ``engines`` are tried in order; only the last one's failure is raised.
    ``fallback_reason`` is recorded when the caller already skipped the cloud.
    Returned boxes are in full-screenshot pixels, not relative to the region.
    """
    engines = [e for e in engines if e != "local" or HAS_LOCAL]
    if not engines:
        raise RuntimeError("local OCR requested but RapidOCR is not installed")
    crop = None
    with Image.open(image_path) as im:
        region = region or (0, 0, *im.size)
        if tuple(region) == (0, 0, *im.size) and max(im.size) <= MAX_SIDE:
            # upload Spectacle's WebP as is: same OCR result as a re-encoded PNG at ~1/4 the size
            upload, upload_size = image_path, im.size
        else:  # fullscreen: only the active window, shrunk to the server's limit
            crop = im.crop(region).convert("RGB")
            if max(crop.size) > MAX_SIDE:
                crop.thumbnail((MAX_SIDE, MAX_SIDE))
            upload, upload_size = CACHE_DIR / f"{image_path.stem}.ocr.png", crop.size
    factor = (region[2] - region[0]) / upload_size[0]  # undo the thumbnail when mapping boxes back

    start = time.perf_counter()
    try:
        if crop:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            crop.save(upload)
        for i, name in enumerate(engines):
            try:
                if name == "cloud":
                    raw = _cloud(upload, cloud_timeout)
                    engine = f"paddleocr-cloud {MODEL}"
                else:
                    raw = _local(upload, max(upload_size), local_threads)
                    engine = f"rapidocr {version('rapidocr')}"
                break
            except Exception as e:
                if i == len(engines) - 1:
                    raise
                fallback_reason = f"{type(e).__name__}: {e}"
                log.warning("%s OCR failed after %.1fs (%s), falling back to %s", name,
                            time.perf_counter() - start, fallback_reason, engines[i + 1])
    finally:
        if crop:
            upload.unlink(missing_ok=True)
    elapsed = time.perf_counter() - start

    left, top = region[:2]
    lines = [
        {
            "text": text,
            "score": round(float(score), 3),
            # 4 corners, clockwise from top-left, in screenshot pixels
            "box": [[round(left + x * factor), round(top + y * factor)] for x, y in poly],
        }
        for text, score, poly in raw
        if text.strip()
    ]
    return {
        "engine": engine,
        "fallback_reason": fallback_reason,
        "elapsed_s": round(elapsed, 2),
        "region": dict(zip(("left", "top", "right", "bottom"), region)),
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
    }
