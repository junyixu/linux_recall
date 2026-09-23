"""OCR via the PaddleOCR cloud API (PP-OCRv6 on Baidu AI Studio).

Needs ``PADDLEOCR_TOKEN``. Only the active window's region is uploaded:
detection runs at the service default (longest side 960), which reads a
window crop fine (~100 lines) but leaves a whole multi-monitor desktop
nearly empty. Raising ``textDetLimitSideLen`` (tried 3938/4000/6098) makes
the job fail with HTTP 500, so we don't. Based on
``~/WorkSpace/anki_agent/anki_ocr_paddle.py``.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from linux_recall.paths import CACHE_DIR

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PP-OCRv6"
MAX_SIDE = 4000  # server-side max_side_limit; larger uploads get shrunk first
QUEUE_FULL = 10010


def _headers() -> dict[str, str]:
    token = os.environ.get("PADDLEOCR_TOKEN")
    if not token:
        raise RuntimeError("PADDLEOCR_TOKEN is not set")
    return {"Authorization": f"bearer {token}"}


def _submit(image_path: Path) -> str:
    optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useTextlineOrientation": False,
    }
    data = {"model": MODEL, "optionalPayload": json.dumps(optional_payload)}
    for delay in (5, 10, 20, 40, None):
        with open(image_path, "rb") as f:
            resp = requests.post(JOB_URL, headers=_headers(), data=data, files={"file": f}, timeout=60)
        # 400 + code 10010 "任务提交队列已满，请稍后重试": the shared queue is busy, not our fault
        if resp.status_code == 400 and resp.json().get("code") == QUEUE_FULL and delay:
            time.sleep(delay)
            continue
        resp.raise_for_status()
        return resp.json()["data"]["jobId"]
    raise AssertionError("unreachable")


# the job itself takes ~1-10 s, but it can sit in the shared queue for minutes
def _wait(job_id: str, timeout: float = 600, interval: float = 2) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = requests.get(f"{JOB_URL}/{job_id}", headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()["data"]
        if data["state"] == "done":
            return data
        if data["state"] == "failed":
            raise RuntimeError(f"PaddleOCR job {job_id} failed: {data.get('errorMsg')}")
        time.sleep(interval)
    raise TimeoutError(f"PaddleOCR job {job_id} timed out")


def run_ocr(image_path: Path, region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    """OCR ``region`` (left, top, right, bottom in screenshot pixels) of the image, or all of it.

    Returned boxes are in full-screenshot pixels, not relative to the region.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    upload = CACHE_DIR / f"{image_path.stem}.ocr.png"
    with Image.open(image_path) as im:
        region = region or (0, 0, *im.size)
        crop = im.crop(region).convert("RGB")
    if max(crop.size) > MAX_SIDE:
        crop.thumbnail((MAX_SIDE, MAX_SIDE))
    factor = (region[2] - region[0]) / crop.width  # undo the thumbnail when mapping boxes back

    start = time.perf_counter()
    try:
        crop.save(upload)
        job = _wait(_submit(upload))
    finally:
        upload.unlink(missing_ok=True)
    resp = requests.get(job["resultUrl"]["jsonUrl"], timeout=30)
    resp.raise_for_status()
    pruned = json.loads(resp.text.splitlines()[0])["result"]["ocrResults"][0]["prunedResult"]
    elapsed = time.perf_counter() - start

    left, top = region[:2]
    lines = [
        {
            "text": text,
            "score": round(float(score), 3),
            # 4 corners, clockwise from top-left, in screenshot pixels
            "box": [[round(left + x * factor), round(top + y * factor)] for x, y in poly],
        }
        for text, score, poly in zip(pruned["rec_texts"], pruned["rec_scores"], pruned["rec_polys"])
        if text.strip()
    ]
    return {
        "engine": f"paddleocr-cloud {MODEL}",
        "elapsed_s": round(elapsed, 2),
        "region": dict(zip(("left", "top", "right", "bottom"), region)),
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
    }
