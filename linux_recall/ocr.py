"""OCR of the active window: PaddleOCR cloud first, local RapidOCR as fallback.

Cloud: PP-OCRv6 on Baidu AI Studio, needs ``PADDLEOCR_TOKEN``. Only the
active window's region is uploaded: detection runs at the service default
(longest side 960), which reads a window crop fine (~100 lines) but leaves a
whole multi-monitor desktop nearly empty. Raising ``textDetLimitSideLen``
(tried 3938/4000/6098) makes the job fail with HTTP 500, so we don't. Based
on ``~/WorkSpace/anki_agent/anki_ocr_paddle.py``.

Local: RapidOCR (PaddleOCR models on ONNX Runtime), used whenever the cloud
misses its deadline or errors out — its queue can stall for minutes.
"""

import json
import logging
import os
import time
from importlib.metadata import version
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

log = logging.getLogger("linux_recall")

# (text, score, 4 corner points) in crop pixels
Line = tuple[str, float, list[list[float]]]


def _remaining(deadline: float) -> float:
    """Per-request timeout so a slow upload or poll can't overrun the overall deadline."""
    return max(1.0, min(30.0, deadline - time.monotonic()))


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
        with open(image_path, "rb") as f:
            resp = requests.post(JOB_URL, headers=_headers(), data=data, files={"file": f},
                                 timeout=_remaining(deadline))
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


def _local(image_path: Path, max_side: int) -> list[Line]:
    from rapidocr import RapidOCR  # heavy import, only needed on fallback

    # RapidOCR downscales to 2000px by default, blurring small text on HiDPI crops
    engine = RapidOCR(params={"Global.max_side_len": max_side, "Global.log_level": "warning"})
    result = engine(str(image_path))
    if result.txts is None:
        return []
    return [(t, s, b.tolist()) for t, s, b in zip(result.txts, result.scores, result.boxes)]


def run_ocr(image_path: Path, region: tuple[int, int, int, int] | None = None,
            cloud_timeout: float = CLOUD_TIMEOUT) -> dict[str, Any]:
    """OCR ``region`` (left, top, right, bottom in screenshot pixels) of the image, or all of it.

    Returned boxes are in full-screenshot pixels, not relative to the region.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    crop_path = CACHE_DIR / f"{image_path.stem}.ocr.png"
    with Image.open(image_path) as im:
        region = region or (0, 0, *im.size)
        crop = im.crop(region).convert("RGB")
    if max(crop.size) > MAX_SIDE:
        crop.thumbnail((MAX_SIDE, MAX_SIDE))
    factor = (region[2] - region[0]) / crop.width  # undo the thumbnail when mapping boxes back

    start = time.perf_counter()
    fallback_reason = None
    try:
        crop.save(crop_path)
        try:
            raw = _cloud(crop_path, cloud_timeout)
            engine = f"paddleocr-cloud {MODEL}"
        except Exception as e:
            fallback_reason = f"{type(e).__name__}: {e}"
            log.warning("cloud OCR failed after %.1fs (%s), falling back to local",
                        time.perf_counter() - start, fallback_reason)
            raw = _local(crop_path, max(crop.size))
            engine = f"rapidocr {version('rapidocr')}"
    finally:
        crop_path.unlink(missing_ok=True)
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
