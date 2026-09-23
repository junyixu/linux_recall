"""Non-interactive screenshots on KWin Wayland via Spectacle.

grim / wlr-screencopy don't exist on KWin and ``org.kde.KWin.ScreenShot2``
only accepts callers whitelisted by a .desktop file, so we let Spectacle
(which is whitelisted) do the capture in background mode.
"""

import subprocess
from pathlib import Path
from typing import Any

MODES = {"fullscreen": "-f", "monitor": "-m", "window": "-a"}


def take_screenshot(path: Path, mode: str = "fullscreen") -> Path:
    """Save a screenshot to ``path``; the format follows the file extension."""
    subprocess.run(
        ["spectacle", "--background", "--nonotify", MODES[mode], "--output", str(path)],
        check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if not path.is_file():  # old Spectacle versions silently went to clipboard instead
        raise RuntimeError(f"spectacle did not write {path}")
    return path


def window_region(window: dict[str, Any], screens: list[dict[str, Any]],
                  image_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Map the window's logical geometry onto a fullscreen screenshot.

    Spectacle renders the whole desktop at one uniform scale (the highest
    output scale), so pixel = (logical - desktop origin) * image_width / desktop_width.
    Returns (left, top, right, bottom) clipped to the image, or None if empty.
    """
    x0 = min(s["geometry"]["x"] for s in screens)
    y0 = min(s["geometry"]["y"] for s in screens)
    x1 = max(s["geometry"]["x"] + s["geometry"]["width"] for s in screens)
    scale = image_size[0] / (x1 - x0)

    g = window["geometry"]
    left = max(0, round((g["x"] - x0) * scale))
    top = max(0, round((g["y"] - y0) * scale))
    right = min(image_size[0], round((g["x"] + g["width"] - x0) * scale))
    bottom = min(image_size[1], round((g["y"] + g["height"] - y0) * scale))
    return (left, top, right, bottom) if right > left and bottom > top else None
