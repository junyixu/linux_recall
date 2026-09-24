"""Neovim's state over msgpack-RPC, for Neovim in kitty (``kitty.py``) and in Neovide.

Neovim is split into a UI (the TUI ``nvim`` process, or Neovide) and an ``nvim --embed``
server child, in its own process group; the server listens on ``$XDG_RUNTIME_DIR/nvim.<pid>.0``.
One ``nvim --server … --remote-expr`` call returns the current file and cursor, and the exact
text visible in each window of the current tabpage (buffer lines ``w0``..``w$`` with their line
numbers), so captures are searchable without OCR errors and ``lrf`` can jump to the matching line.
"""

import glob
import json
import os
import subprocess
from typing import Any

TIMEOUT = 2
MAX_LINE = 500  # chars kept per visible line (minified files)
TEXT_BUFTYPES = ("", "help", "terminal")  # others are plugin UIs (file trees, pickers, ...)
# Visible text of these files isn't stored, only the path (the same idea as the planned exclusion list)
SECRET_PATTERNS = ("/.env", ".env.", "/.ssh/", "/.gnupg/", ".gpg", ".age", "/dev/shm/", "/pass.",
                   "/.password-store/", "credentials", "secret")

# One RPC round trip: current buffer/cursor, listed buffers, and the visible lines of every
# non-floating window in the current tabpage. It's embedded in a Vimscript '...' string joined into
# one line, so: no single quotes, no -- comments. vim.json.encode passes non-UTF-8 bytes through.
NVIM_LUA = """
local buf, cursor, cur = vim.api.nvim_get_current_buf(), vim.api.nvim_win_get_cursor(0), vim.api.nvim_get_current_win()
local buffers, windows = {}, {}
for _, b in ipairs(vim.api.nvim_list_bufs()) do
  local name = vim.api.nvim_buf_get_name(b)
  if vim.bo[b].buflisted and name ~= "" then table.insert(buffers, name) end
end
for _, w in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
  if vim.api.nvim_win_get_config(w).relative == "" then
    local b = vim.api.nvim_win_get_buf(w)
    local first, last = vim.fn.line("w0", w), vim.fn.line("w$", w)
    table.insert(windows, {
      file = vim.api.nvim_buf_get_name(b), buftype = vim.bo[b].buftype, current = w == cur,
      first = first, lines = vim.api.nvim_buf_get_lines(b, first - 1, last, false),
    })
  end
end
return vim.json.encode({
  file = vim.api.nvim_buf_get_name(buf), line = cursor[1], col = cursor[2] + 1,
  filetype = vim.bo[buf].filetype, buftype = vim.bo[buf].buftype, modified = vim.bo[buf].modified,
  cwd = vim.fn.getcwd(), mode = vim.api.nvim_get_mode().mode, buffers = buffers, windows = windows,
})
"""


def children(pid: int) -> list[int]:
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as f:
            return [int(p) for p in f.read().split()]
    except OSError:
        return []


def _cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0")[:-1]]
    except OSError:
        return []


def is_nvim(cmdline: list[str]) -> bool:
    return bool(cmdline) and os.path.basename(cmdline[0]) in ("nvim", "nvim.appimage")


def nvim_address(pid: int, cmdline: list[str]) -> tuple[int, str] | None:
    """The RPC socket of the Neovim instance whose TUI (or server) is ``pid``."""
    for flag in ("--listen", "--server"):  # explicit address, e.g. `nvim --remote-ui --server ADDR`
        if flag in cmdline[:-1]:
            return pid, cmdline[cmdline.index(flag) + 1]
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    servers = [pid] + [c for c in children(pid) if "--embed" in _cmdline(c)]
    for server in reversed(servers):  # prefer the --embed child: the TUI itself has no socket
        if sockets := sorted(glob.glob(f"{runtime}/nvim.{server}.*")):
            return server, sockets[0]
    return None


def is_secret(path: str) -> bool:
    return any(p in path.lower() for p in SECRET_PATTERNS)


def nvim_state(address: str) -> dict[str, Any]:
    lua = " ".join(NVIM_LUA.split())
    expr = f"luaeval('(function() {lua} end)()')"
    out = subprocess.run(["nvim", "--server", address, "--remote-expr", expr], capture_output=True,
                         text=True, errors="replace", timeout=TIMEOUT, check=True).stdout
    state = json.loads(out)
    state["windows"] = [w for w in state["windows"] if w["file"]]  # unnamed buffers: nothing to jump to
    for win in state["windows"]:
        # "lines" (line `first + i` of the buffer) only for real text, and never for secret files
        if win["buftype"] not in TEXT_BUFTYPES or is_secret(win["file"]):
            win["lines"] = None
        else:
            win["lines"] = [line[:MAX_LINE] for line in win["lines"]]
    return state


def get_nvim(pid: int, cmdline: list[str]) -> dict[str, Any]:
    """``pid`` is the UI process: the TUI ``nvim`` or Neovide."""
    nvim: dict[str, Any] = {"pid": pid, "server_pid": None, "address": None}
    if found := nvim_address(pid, cmdline):
        nvim["server_pid"], nvim["address"] = found
        try:
            nvim.update(nvim_state(found[1]))
        except (subprocess.SubprocessError, ValueError) as e:
            nvim["error"] = str(e)
    return nvim


def is_neovide(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() == "neovide"


def get_neovide_context(window: dict[str, Any]) -> dict[str, Any] | None:
    if not (pid := window.get("pid")):
        return None
    return {"nvim": get_nvim(pid, _cmdline(pid))}


if __name__ == "__main__":
    import sys

    from linux_recall.kwin import get_kwin_state

    if len(sys.argv) > 1:  # Neovide or TUI nvim pid, e.g. to test while another window has focus
        pid = int(sys.argv[1])
        print(json.dumps(get_nvim(pid, _cmdline(pid)), indent=2, ensure_ascii=False))
    else:
        window = get_kwin_state()["window"]
        print(json.dumps(get_neovide_context(window) if is_neovide(window) else None, indent=2, ensure_ascii=False))
