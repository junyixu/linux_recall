"""What Anki is showing, via AnkiConnect (``localhost:8765``).

- reviewing (main window): ``guiCurrentCard`` -> the card under review
- Browse window: ``guiSelectedNotes`` -> the selected notes
Card fields are stored as plain text so captures are searchable by card content.
Re-open later with ``guiBrowse`` on the stored ``browse_query`` (``cid:...`` / ``nid:...``).
"""

import html
import re
from typing import Any

import requests

URL = "http://127.0.0.1:8765"
MAX_NOTES = 5  # of a multi-selection in the Browse window


def is_anki(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() == "anki"


def invoke(action: str, **params: Any) -> Any:
    resp = requests.post(URL, json={"action": action, "version": 6, "params": params}, timeout=2)
    resp.raise_for_status()
    body = resp.json()
    if body["error"]:
        raise RuntimeError(f"AnkiConnect {action}: {body['error']}")
    return body["result"]


def plain(field_html: str) -> str:
    text = re.sub(r"<br\s*/?>|</div>|</p>", "\n", field_html, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{2,}", "\n", text).strip()


def _fields(fields: dict[str, Any]) -> dict[str, str]:
    ordered = sorted(fields.items(), key=lambda kv: kv[1]["order"])
    return {name: plain(f["value"]) for name, f in ordered}


def get_anki_context(window: dict[str, Any]) -> dict[str, Any] | None:
    if (window.get("caption") or "").startswith("Browse"):
        note_ids = invoke("guiSelectedNotes")
        if not note_ids:
            return {"mode": "browse", "note_ids": [], "browse_query": None}
        notes = invoke("notesInfo", notes=note_ids[:MAX_NOTES])
        return {
            "mode": "browse",
            "note_ids": note_ids,
            "model": notes[0]["modelName"],
            "notes": [{"note_id": n["noteId"], "fields": _fields(n["fields"])} for n in notes],
            "browse_query": " or ".join(f"nid:{n}" for n in note_ids),
        }

    if not invoke("guiReviewActive"):
        return {"mode": "other"}  # deck list, overview, add/edit dialogs
    card = invoke("guiCurrentCard")
    (info,) = invoke("cardsInfo", cards=[card["cardId"]])
    return {
        "mode": "review",
        "card_id": card["cardId"],
        "note_id": info["note"],
        "deck": card["deckName"],
        "model": card["modelName"],
        "fields": _fields(card["fields"]),
        "browse_query": f"cid:{card['cardId']}",
    }


def browse(query: str) -> None:
    """Open Anki's Browse window on ``query`` and select the first result."""
    card_ids = invoke("guiBrowse", query=query)
    if card_ids:
        invoke("guiSelectCard", card=card_ids[0])


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "--browse":
        browse(sys.argv[2])
    else:
        caption = sys.argv[1] if len(sys.argv) > 1 else "junyi - Anki"
        print(json.dumps(get_anki_context({"resource_class": "anki", "caption": caption}),
                         indent=2, ensure_ascii=False)[:1500])
