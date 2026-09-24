"""Resolve what's open in Zotero via its Local API (Zotero 7+, ``localhost:23119/api``).

The Local API has no endpoint for the selected item or the open reader tab,
so we parse the window caption, which for a reader tab is
``<title> - <creator> - <year> - Zotero``, search the library with
``qmode=titleCreatorYear``, and read the current page from the attachment's
``storage/<key>/.zotero-reader-state`` (``pageIndex`` is 0-based).
Needs Settings → Advanced → "Allow other applications on this computer to
communicate with Zotero". https://www.zotero.org/support/dev/web_api/v3/local_api
"""

import json
from pathlib import Path
from typing import Any

import requests

API = "http://127.0.0.1:23119/api/users/0"
SUFFIX = " - Zotero"
# no dataDir pref means the default; Zotero 10 on this machine uses the XDG one
DATA_DIRS = [Path.home() / ".local/share/Zotero", Path.home() / "Zotero"]


def is_zotero(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() == "zotero"


def _get(path: str, **params: str) -> Any:
    resp = requests.get(f"{API}/{path}", params=params, timeout=2)
    resp.raise_for_status()
    return resp.json()


def _reader_state(attachment_key: str) -> tuple[dict[str, Any], float] | None:
    for data_dir in DATA_DIRS:
        path = data_dir / "storage" / attachment_key / ".zotero-reader-state"
        try:
            return json.loads(path.read_text()), path.stat().st_mtime
        except (OSError, ValueError):
            continue
    return None


def get_open_item(window: dict[str, Any]) -> dict[str, Any] | None:
    """Return the item shown in Zotero's active reader tab, or None (library view, not found)."""
    caption = window.get("caption") or ""
    parts = caption.removesuffix(SUFFIX).rsplit(" - ", 2)
    if not caption.endswith(SUFFIX) or len(parts) != 3:
        return None
    title, _creator, year = parts

    matches = [it["data"] for it in _get("items", q=title, qmode="titleCreatorYear")
               if it["data"]["itemType"] not in ("attachment", "note", "annotation")
               and it["data"].get("title", "").casefold() == title.casefold()
               and year in it["data"].get("date", "")]
    if not matches:
        return None
    item = matches[0]

    # the open attachment is the one whose reader state was touched most recently
    best = None
    for child in _get(f"items/{item['key']}/children"):
        data = child["data"]
        if data["itemType"] != "attachment" or not (state := _reader_state(data["key"])):
            continue
        if best is None or state[1] > best[2]:
            best = (data, state[0], state[1])

    doi = item.get("DOI") or None
    result: dict[str, Any] = {
        "item_key": item["key"],
        "title": item.get("title"),
        "creators": [c.get("lastName") or c.get("name") for c in item.get("creators", [])],
        "date": item.get("date") or None,
        "item_type": item["itemType"],
        "doi": doi,
        "url": item.get("url") or (doi and f"https://doi.org/{doi}"),
        "select_link": f"zotero://select/library/items/{item['key']}",
        "ambiguous": len(matches) > 1,
        "source": "zotero-local-api",
    }
    if best:
        attachment, state, _ = best
        page = state.get("pageIndex")
        result["attachment_key"] = attachment["key"]
        result["page"] = page + 1 if isinstance(page, int) else None
        result["open_link"] = (f"zotero://open-pdf/library/items/{attachment['key']}"
                               + (f"?page={page + 1}" if isinstance(page, int) else ""))
    return result


if __name__ == "__main__":
    import sys

    caption = sys.argv[1] if len(sys.argv) > 1 else None
    if caption is None:
        from linux_recall.kwin import get_kwin_state

        window = get_kwin_state()["window"] or {}
    else:
        window = {"resource_class": "Zotero", "caption": caption}
    print(json.dumps(get_open_item(window) if is_zotero(window) else None, indent=2, ensure_ascii=False))
