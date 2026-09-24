"""What the focused kitty window is running, via kitty remote control (``kitten @ ls``).

kitty -> OS window -> tab -> window (its ``pid``/``cmdline`` is the shell, e.g. zsh) ->
foreground processes (what the shell is running); if one of them is Neovim, ``nvim.py`` asks it
for the current file and the visible text; if it's Claude Code, ``claude.py`` finds the session.
With kitty's shell integration, a plain shell also gets its last command and that command's output
(``get-text --extent last_cmd_output``: the exact text, no OCR needed).
Needs ``allow_remote_control`` and ``listen_on`` in kitty.conf; kitty exports the socket
address to its children as ``KITTY_LISTEN_ON``, so it's read from there.
"""

import json
import os
import subprocess
from typing import Any

from linux_recall.claude import get_claude, is_claude
from linux_recall.nvim import TIMEOUT, children, get_nvim, is_nvim, is_secret

MAX_OUTPUT_LINES = 200  # the end of a long output is kept
MAX_LINE = 500
# Output of these commands isn't stored, only the command line (secret files: nvim.is_secret)
SECRET_COMMANDS = {"pass", "gopass", "gpg", "gpg2", "age", "secret-tool", "keyring", "op", "bw", "rbw",
                   "env", "printenv", "export", "set", "ssh-keygen", "openssl", "kwallet-query"}

def is_kitty(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() == "kitty"


def listen_address(kitty_pid: int) -> str | None:
    """kitty's remote control socket, from ``KITTY_LISTEN_ON`` in a child's environment."""
    for child in children(kitty_pid):
        try:
            with open(f"/proc/{child}/environ", "rb") as f:
                env = f.read().split(b"\0")
        except OSError:
            continue
        for var in env:
            if var.startswith(b"KITTY_LISTEN_ON="):
                return var.split(b"=", 1)[1].decode()
    return None


def _focused(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return (next((i for i in items if i.get("is_focused")), None)
            or next((i for i in items if i.get("is_active")), None))


def _is_secret_command(cmdline: str) -> bool:
    words = cmdline.replace("|", " ").replace(";", " ").replace("&", " ").split()
    return is_secret(cmdline) or any(os.path.basename(w) in SECRET_COMMANDS for w in words)


def _kitty(address: str, *args: str) -> str:
    return subprocess.run(["kitten", "@", "--to", address, *args], capture_output=True, text=True,
                          errors="replace", timeout=TIMEOUT, check=True).stdout


def get_shell(address: str, win: dict[str, Any], tui: bool) -> dict[str, Any] | None:
    """The last (or running) command of the shell in ``win``; needs kitty's shell integration."""
    cmdline = win.get("last_reported_cmdline")
    if cmdline is None:
        return None
    at_prompt = win.get("at_prompt", False)
    shell: dict[str, Any] = {
        "cmdline": cmdline,
        "at_prompt": at_prompt,  # false: still running, and "output" is its output so far
        "exit_status": win.get("last_cmd_exit_status") if at_prompt else None,  # stale while running
        "output": None,
    }
    # full-screen programs (vim, htop, less, Claude Code) have no line output worth keeping
    if tui or win.get("in_alternate_screen") or _is_secret_command(cmdline):
        return shell
    lines = _kitty(address, "get-text", "--match", f"id:{win['id']}", "--extent", "last_cmd_output").splitlines()
    shell["output_lines"] = len(lines)
    shell["output"] = [line[:MAX_LINE] for line in lines[-MAX_OUTPUT_LINES:]]
    return shell


def get_kitty_context(window: dict[str, Any]) -> dict[str, Any] | None:
    address = window.get("pid") and listen_address(window["pid"])
    if not address:
        return None  # remote control not enabled
    if not (os_window := _focused(json.loads(_kitty(address, "ls")))):
        return None
    if not (tab := _focused(os_window["tabs"])) or not (win := _focused(tab["windows"])):
        return None

    foreground = [{"pid": p["pid"], "cmdline": p["cmdline"], "cwd": p.get("cwd")}
                  for p in win.get("foreground_processes", [])]
    proc = next((p for p in foreground if is_nvim(p["cmdline"])), None)
    nvim = proc and get_nvim(proc["pid"], proc["cmdline"])
    proc = next((p for p in foreground if is_claude(p["cmdline"])), None)
    claude = proc and get_claude(proc["pid"])
    try:
        shell = get_shell(address, win, tui=bool(nvim or claude))
    except (subprocess.SubprocessError, OSError) as e:
        shell = {"error": str(e)}
    return {
        "address": address,  # remote control socket, for `kitten @ --to` (lr's ctrl-o)
        "tab": {"id": tab["id"], "title": tab["title"]},
        "window": {"id": win["id"], "title": win["title"], "cwd": win.get("cwd"),
                   "pid": win.get("pid"), "cmdline": win.get("cmdline")},
        "foreground_processes": foreground,
        "shell": shell,
        "nvim": nvim,
        "claude": claude,
    }


if __name__ == "__main__":
    import sys

    from linux_recall.kwin import get_kwin_state

    if len(sys.argv) > 1:  # kitty pid, e.g. to test while another window has focus
        window = {"pid": int(sys.argv[1]), "resource_class": "kitty"}
    else:
        window = get_kwin_state()["window"]
    print(json.dumps(get_kitty_context(window) if is_kitty(window) else None, indent=2, ensure_ascii=False))
