"""What's open in Okular, via the D-Bus interface each Okular process exports as ``org.kde.okular-<pid>``.

Every document tab is an ``Okular::Part`` registered at ``/okular`` (the first one), ``/okular2``, ``/okular3``…
(a per-process counter that never goes back, so closed tabs leave gaps). Each one answers
``currentDocument`` (local path), ``currentPage`` (1-based, 0 without a document), ``pages`` and
``documentMetaData(key)``. Nothing says which tab is active, so we match the window caption, which is
``<caption> — Okular`` where ``<caption>`` is the document title (if it has one and "Display document title"
is on), otherwise its file name or full path (Settings → General → "Display document title in titlebar").
https://invent.kde.org/graphics/okular/-/blob/master/part/part.cpp
"""

import os
import xml.etree.ElementTree as ET
from typing import Any

from jeepney import DBusAddress, MessageType, new_method_call
from jeepney.io.blocking import DBusConnection, open_dbus_connection

CLASSES = ("org.kde.okular", "okular")
SUFFIX = " — Okular"
INTROSPECT = "org.freedesktop.DBus.Introspectable"


def is_okular(window: dict[str, Any] | None) -> bool:
    return bool(window) and (window.get("resource_class") or "").lower() in CLASSES


def _call(conn: DBusConnection, addr: DBusAddress, method: str, sig: str = "", body: tuple = ()) -> Any:
    reply = conn.send_and_get_reply(new_method_call(addr, method, sig, body), timeout=2)
    if reply.header.message_type == MessageType.error:
        raise RuntimeError(f"{addr.object_path} {method}: {reply.body}")
    return reply.body[0]


def _parts(conn: DBusConnection, bus_name: str) -> list[str]:
    """Object paths of the document tabs: ``/okular``, ``/okular2``… (``/okularshell`` is the window)."""
    xml = _call(conn, DBusAddress("/", bus_name=bus_name, interface=INTROSPECT), "Introspect")
    names = [node.get("name") or "" for node in ET.fromstring(xml).iter("node")]
    return sorted((f"/{n}" for n in names if n == "okular" or n.removeprefix("okular").isdigit()),
                  key=lambda p: int(p.removeprefix("/okular") or 1))


def _document(conn: DBusConnection, bus_name: str, path: str) -> dict[str, Any] | None:
    addr = DBusAddress(path, bus_name=bus_name, interface="org.kde.okular")
    file = _call(conn, addr, "currentDocument")
    if not file:
        return None  # empty tab
    meta = {key: _call(conn, addr, "documentMetaData", "s", (key,)) or None
            for key in ("title", "author", "mimeType")}
    return {
        "path": file,
        "file": os.path.basename(file),
        "title": meta["title"],
        "author": meta["author"],
        "mime_type": meta["mimeType"],
        "page": _call(conn, addr, "currentPage") or None,
        "pages": _call(conn, addr, "pages") or None,
    }


def _captions(doc: dict[str, Any]) -> set[str]:
    """The captions this document can give the window, depending on Okular's titlebar settings."""
    return {c for c in (doc["title"] and doc["title"].strip() and doc["title"], doc["file"], doc["path"]) if c}


def get_open_document(window: dict[str, Any]) -> dict[str, Any] | None:
    """Return the document in Okular's active tab, or None (no document, Okular not on the bus)."""
    bus_name = f"org.kde.okular-{window['pid']}"
    with open_dbus_connection(bus="SESSION") as conn:
        docs = [doc for path in _parts(conn, bus_name) if (doc := _document(conn, bus_name, path))]
    if not docs:
        return None

    caption = (window.get("caption") or "").removesuffix(SUFFIX)
    # a modified document (e.g. unsaved annotations) gets a " *" marker from KXmlGui
    matches = ([d for d in docs if caption in _captions(d)]
               or [d for d in docs if caption.removesuffix("*").rstrip() in _captions(d)])
    if not matches:
        if len(docs) > 1:
            return None
        matches, match = docs, "only"  # single tab whose caption we could not predict
    else:
        match = "exact" if len(matches) == 1 else "ambiguous"
    doc = matches[0]
    doc["tabs"] = len(docs)
    doc["match"] = match
    if match == "ambiguous":
        doc["candidates"] = [d["path"] for d in matches]
    doc["source"] = "okular-dbus"
    return doc


if __name__ == "__main__":
    import json

    from linux_recall.kwin import get_kwin_state

    w = get_kwin_state()["window"]
    print(json.dumps(get_open_document(w) if w and is_okular(w) else None, indent=2, ensure_ascii=False))
