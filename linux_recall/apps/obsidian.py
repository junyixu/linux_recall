"""What's open in Obsidian, via its CLI (Obsidian 1.12+, ``obsidian eval``).

The window caption is ``<note> - <vault> - Obsidian <version>``; the vault name goes to
``obsidian vault=<name> eval``, which runs JavaScript in that vault's window and prints ``=> <result>``.
One call returns the active file, the view mode, the cursor, and the source lines visible on screen
(reading view: the rendered sections inside the pane; editing view: CodeMirror's top/bottom lines),
so captures are searchable without OCR errors. Needs Settings → General → Command line interface.
https://help.obsidian.md/cli
"""

import json
import subprocess
from typing import Any
from urllib.parse import quote

from linux_recall.apps.nvim import is_secret

TIMEOUT = 2
MAX_LINE = 500  # chars kept per visible line
CLASSES = ("obsidian", "md.obsidian.obsidian")

# Joined into one line and passed as ``code=``: no // comments, and no backslashes (the CLI turns
# \n and \t in values into newlines and tabs), hence String.fromCharCode(10).
JS = """
(() => {
  const leaf = app.workspace.activeLeaf, view = leaf && leaf.view, file = view && view.file;
  const out = {vault: app.vault.getName(), vault_path: app.vault.adapter.basePath,
               view: view ? view.getViewType() : null, file: file ? file.path : null,
               tabs: app.workspace.getLeavesOfType("markdown").map(l => l.view.file && l.view.file.path)};
  if (!file || out.view !== "markdown") return JSON.stringify(out);
  const cache = app.metadataCache.getFileCache(file) || {};
  const fm = cache.frontmatter || {};
  out.mode = view.getMode();
  out.tags = [...new Set([...[].concat(fm.tags || []).map(t => "#" + t), ...(cache.tags || []).map(t => t.tag)])];
  out.aliases = [].concat(fm.aliases || []);
  let first = 0, last = -1;
  if (out.mode === "preview") {
    const box = view.previewMode.containerEl.getBoundingClientRect();
    const shown = view.previewMode.renderer.sections.filter(s => {
      const r = s.el.getBoundingClientRect();
      return s.el.isConnected && r.height > 0 && r.bottom > box.top && r.top < box.bottom;
    });
    if (shown.length) { first = shown[0].start.line; last = shown[shown.length - 1].end.line; }
  } else {
    const cm = view.editor.cm, box = cm.scrollDOM.getBoundingClientRect(), x = box.left + box.width / 2;
    const at = y => cm.state.doc.lineAt(cm.posAtCoords({x, y}, false)).number - 1;
    first = at(box.top + 1); last = at(box.bottom - 1);
    const cursor = view.editor.getCursor();
    out.line = cursor.line + 1; out.col = cursor.ch + 1;
  }
  const heading = (cache.headings || []).filter(h => h.position.start.line <= first).pop();
  out.heading = heading ? heading.heading : null;
  out.first = first + 1;
  out.lines = view.data.split(String.fromCharCode(10)).slice(first, last + 1);
  return JSON.stringify(out);
})()
"""


def is_obsidian(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() in CLASSES


def vault_name(caption: str) -> str | None:
    parts = caption.rsplit(" - ", 2)  # the note name may contain " - " too
    if len(parts) == 3 and parts[2].startswith("Obsidian"):
        return parts[1]
    return None


def _eval(vault: str | None, code: str) -> str:
    cmd = ["obsidian", *([f"vault={vault}"] if vault else []), "eval", f"code={' '.join(code.split())}"]
    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT,
                         check=True).stdout.strip()
    if not out.startswith("=> "):  # e.g. "Error: ..." (exit status is 0 either way)
        raise RuntimeError(f"obsidian eval: {out[:200]}")
    return out.removeprefix("=> ")


def get_open_note(window: dict[str, Any]) -> dict[str, Any] | None:
    """Return the note in Obsidian's active pane, or None (no file open, e.g. the graph view)."""
    vault = vault_name(window.get("caption") or "")
    note = json.loads(_eval(vault, JS))
    if not note["file"]:
        return None
    if note.get("lines") is not None:
        note["lines"] = None if is_secret(note["file"]) else [line[:MAX_LINE] for line in note["lines"]]
    note["ambiguous"] = vault is None  # couldn't tell the vault from the caption: the CLI used the last focused one
    note["open_link"] = f"obsidian://open?vault={quote(note['vault'])}&file={quote(note['file'])}"
    note["source"] = "obsidian-cli"
    return note


if __name__ == "__main__":
    import sys

    caption = sys.argv[1] if len(sys.argv) > 1 else None
    if caption is None:
        from linux_recall.kwin import get_kwin_state

        window = get_kwin_state()["window"] or {}
    else:
        window = {"resource_class": "obsidian", "caption": caption}
    print(json.dumps(get_open_note(window) if is_obsidian(window) else None, indent=2, ensure_ascii=False))
