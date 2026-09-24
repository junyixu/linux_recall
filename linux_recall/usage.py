"""Time spent per app, from the daemon's per-cycle log ``daemon.jsonl``.

Counting captures on disk undercounts apps whose window stays still (the
daemon skips near-duplicates), but ``daemon.jsonl`` has one record per cycle,
skipped ones included. Each cycle counts until the next one, capped at
``--max-gap`` so time the daemon wasn't running (suspend, shutdown) doesn't
count. Locked cycles are away time and are reported separately.

``--by detail`` splits browsers by site and kitty by foreground program, read
from the capture JSON of each kept cycle; skipped (similar) cycles reuse the
last kept capture of the same app, which they look nearly identical to.
"""

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from linux_recall.paths import CACHE_DIR

LOCKED, EXCLUDED, NO_WINDOW = "(locked)", "(excluded)", "(no window)"
# report the script, not the interpreter running it
INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "uv"}


def read_cycles(path: Path) -> list[dict[str, Any]]:
    cycles = []
    for line in path.read_text().splitlines():
        if line.strip():
            cycle = json.loads(line)
            cycle["time"] = datetime.fromisoformat(cycle["time"])
            cycles.append(cycle)
    cycles.sort(key=lambda c: c["time"])
    return cycles


@cache
def capture_detail(file: str) -> str | None:
    """Site of the browser tab, or the program in the foreground of kitty."""
    try:
        record = json.loads(Path(file).read_text())
    except (OSError, ValueError):
        return None
    if url := (record.get("browser") or {}).get("url"):
        parts = urlsplit(url)
        return parts.hostname or parts.scheme
    if procs := (record.get("kitty") or {}).get("foreground_processes"):
        cmdline = procs[0]["cmdline"]
        name = Path(cmdline[0]).name
        if name in INTERPRETERS and len(cmdline) > 1 and not cmdline[1].startswith("-"):
            name = Path(cmdline[1]).name
        return name
    return None


def label(cycle: dict[str, Any], by: str, last_detail: dict[str, str | None]) -> str:
    if cycle["reason"] == "locked":
        return LOCKED
    if cycle["reason"] == "excluded":  # `app` holds the exclusion reason, not the app
        return EXCLUDED
    app = cycle.get("app") or NO_WINDOW
    if by == "app":
        return app
    if cycle.get("file"):
        last_detail[app] = capture_detail(cycle["file"])
    detail = last_detail.get(app)
    return f"{app} · {detail}" if detail else app


def tally(cycles: list[dict[str, Any]], by: str, max_gap: float, interval: float,
          since: date | None, until: date | None, daily: bool) -> dict[str, dict[str, float]]:
    """{day (or "all"): {label: seconds}}"""
    usage: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    last_detail: dict[str, str | None] = {}
    for cur, nxt in zip(cycles, [*cycles[1:], None]):
        name = label(cur, by, last_detail)  # before filtering: detail carries over across days
        day = cur["time"].date()
        if (since and day < since) or (until and day > until):
            continue
        seconds = (nxt["time"] - cur["time"]).total_seconds() if nxt else interval
        usage[day.isoformat() if daily else "all"][name] += min(seconds, max_gap)
    return usage


def fmt(seconds: float) -> str:
    h, m = divmod(round(seconds / 60), 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def bar(frac: float, width: int = 30) -> str:
    full, rest = divmod(frac * width, 1)
    return "█" * int(full) + " ▏▎▍▌▋▊▉"[int(rest * 8)].strip()


def print_table(usage: dict[str, float], top: int) -> None:
    usage = dict(usage)
    away = usage.pop(LOCKED, 0)
    total = sum(usage.values())
    rows = sorted(usage.items(), key=lambda kv: -kv[1])
    if top and len(rows) > top:
        rest = rows[top:]
        rows = rows[:top] + [(f"({len(rest)} more)", sum(s for _, s in rest))]
    longest = max((s for _, s in rows), default=0)
    width = max([len(name) for name, _ in rows] + [len("locked")])
    for name, seconds in rows:
        print(f"{name:<{width}}  {fmt(seconds):>7}  {seconds / total:6.1%}  {bar(seconds / longest)}")
    print(f"{'total':<{width}}  {fmt(total):>7}")
    if away:
        print(f"{'locked':<{width}}  {fmt(away):>7}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Time spent per app, from the daemon's per-cycle log.")
    parser.add_argument("--by", choices=("app", "detail"), default="app",
                        help="detail: split browsers by site and kitty by foreground program")
    parser.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument("--today", action="store_true", help="same as --since and --until today")
    parser.add_argument("--daily", action="store_true", help="one table per day")
    parser.add_argument("--top", type=int, default=20, help="rows per table, the rest are summed (0: all)")
    parser.add_argument("--max-gap", type=float,
                        help="seconds a cycle counts at most (default: twice the median interval)")
    parser.add_argument("--json", action="store_true", help="print {day: {label: seconds}}")
    parser.add_argument("--log", type=Path, default=CACHE_DIR / "daemon.jsonl")
    args = parser.parse_args()
    if args.today:
        args.since = args.until = date.today()

    cycles = read_cycles(args.log)
    if len(cycles) < 2:
        raise SystemExit(f"not enough records in {args.log}")
    interval = statistics.median((b["time"] - a["time"]).total_seconds() for a, b in zip(cycles, cycles[1:]))
    usage = tally(cycles, args.by, args.max_gap or 2 * interval, interval,
                  args.since, args.until, args.daily)

    if args.json:
        print(json.dumps({day: dict(u) for day, u in sorted(usage.items())}, ensure_ascii=False, indent=2))
        return
    if not usage:
        raise SystemExit("no records in that range")
    for i, (day, u) in enumerate(sorted(usage.items())):
        if args.daily:
            print(("\n" if i else "") + day)
        print_table(u, args.top)


if __name__ == "__main__":
    main()
