"""The Claude Code session running in a kitty window.

Claude Code writes ``~/.claude/sessions/<pid>.json`` for each running CLI (session id, cwd,
status, name), and the transcript to ``~/.claude/projects/<cwd with / and _ as ->/<session id>.jsonl``.
The transcript's latest ``ai-title`` and ``last-prompt`` records say what the session is about;
they're read from the end of the file, since transcripts grow to tens of MB.
Re-open later with ``claude --resume <session id>`` in the same cwd.
"""

import glob
import json
import os
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path.home() / ".claude"
TAIL_BYTES = 512 * 1024  # enough to hold the latest ai-title / last-prompt
MAX_PROMPT = 1000


def is_claude(cmdline: list[str]) -> bool:
    return bool(cmdline) and os.path.basename(cmdline[0]) == "claude"


def _tail_records(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as f:
        f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL_BYTES))
        lines = f.read().split(b"\n")[1:]  # the first line is probably cut
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def get_claude(pid: int) -> dict[str, Any] | None:
    try:
        session = json.loads((CLAUDE_DIR / "sessions" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    sid = session["sessionId"]
    claude: dict[str, Any] = {
        "pid": pid, "session_id": sid, "cwd": session.get("cwd"), "name": session.get("name"),
        "status": session.get("status"), "transcript": None, "title": None, "last_prompt": None,
    }
    if not (paths := glob.glob(str(CLAUDE_DIR / "projects" / "*" / f"{sid}.jsonl"))):
        return claude  # nothing sent yet
    claude["transcript"] = paths[0]
    for record in _tail_records(Path(paths[0])):
        if record.get("sessionId") != sid:
            continue
        if record.get("type") == "ai-title":
            claude["title"] = record.get("aiTitle")
        elif record.get("type") == "last-prompt":
            claude["last_prompt"] = (record.get("lastPrompt") or "")[:MAX_PROMPT]
    return claude


if __name__ == "__main__":
    import sys

    print(json.dumps(get_claude(int(sys.argv[1])), indent=2, ensure_ascii=False))
