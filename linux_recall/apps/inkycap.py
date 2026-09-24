"""What's open in InkyCap (https://inkycap.org), a Typst notes app built on Tauri.

InkyCap has no CLI or IPC for other programs, and its window caption is always ``InkyCap``.
What it does have: whenever the active tab changes to a note, the frontend writes that note's absolute
path to ``<notebox>/.inkycap/local.json`` (``last_active_file``, debounced by 500 ms), and
``~/.config/inkycap/config.json`` lists every notebox it has opened. Each window holds its own notebox,
so the notebox whose ``local.json`` changed last is the one in the window that last switched notes.
The note's properties come from the ``#note(title: …, tags: …)`` call InkyCap writes at the top of every
note; parsing it directly is much cheaper than ``typst query``.
There is no scroll position to read, so unlike Obsidian there are no visible source lines: OCR covers them.
When the active tab is not a note (a collection, the mycelial view, an empty tab), the previous note stays.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from linux_recall.apps.nvim import is_secret

CLASSES = ("inkycap",)
CONFIG = Path.home() / ".config/inkycap/config.json"
HEAD_BYTES = 8192  # the #note(...) header sits at the top of the file

STRING = r'"((?:[^"\\]|\\.)*)"'


def is_inkycap(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() in CLASSES


def _noteboxes() -> list[Path]:
    config = json.loads(CONFIG.read_text())
    paths = [config.get("notebox_path"), *(e.get("path") for e in config.get("notebox_registry", []))]
    return list(dict.fromkeys(Path(p) for p in paths if p))


def _note_args(text: str) -> str | None:
    """The argument list of the first ``#note(...)`` call, or None."""
    start = text.find("#note(")
    if start < 0:
        return None
    depth, i, quoted = 0, start + len("#note"), False
    for j in range(i, len(text)):
        c = text[j]
        if quoted:
            quoted = c != '"' or text[j - 1] == "\\"
        elif c == '"':
            quoted = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    return None


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def parse_note_header(text: str) -> dict[str, Any]:
    """title, date, zid, tags, aliases and description from ``#note(...)``; missing keys are left out."""
    args = _note_args(text)
    if args is None:
        return {}
    props: dict[str, Any] = {}
    for key in ("title", "date", "description"):
        if m := re.search(rf"\b{key}\s*:\s*{STRING}", args):
            props[key] = _unescape(m[1])
    if m := re.search(r"\bzid\s*:\s*(\d+)", args):
        props["zid"] = int(m[1])
    for key in ("tags", "aliases"):  # ("a", "b"), (), or a bare "a"
        if m := re.search(rf"\b{key}\s*:\s*(\([^)]*\)|{STRING})", args):
            props[key] = [_unescape(s) for s in re.findall(STRING, m[1])]
    return props


def get_open_note() -> dict[str, Any] | None:
    """Return the note InkyCap last made active, or None (no notebox or no note opened yet)."""
    states = [(root, root / ".inkycap/local.json") for root in _noteboxes()]
    states = [(root, local) for root, local in states if local.is_file()]
    if not states:
        return None
    root, local = max(states, key=lambda s: s[1].stat().st_mtime)
    path = json.loads(local.read_text()).get("last_active_file")
    if not path:
        return None
    file = Path(path)
    note: dict[str, Any] = {
        "notebox": root.name,
        "notebox_path": str(root),
        "file": str(file.relative_to(root)) if file.is_relative_to(root) else path,
        "path": path,
        "since": datetime.fromtimestamp(local.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        "ambiguous": len(states) > 1,  # several noteboxes: picked the one whose window switched notes last
    }
    if file.suffix == ".typ" and file.is_file() and not is_secret(path):
        with file.open(errors="replace") as f:
            note |= parse_note_header(f.read(HEAD_BYTES))
    note["source"] = "inkycap-local-state"
    return note


if __name__ == "__main__":
    print(json.dumps(get_open_note(), indent=2, ensure_ascii=False))
