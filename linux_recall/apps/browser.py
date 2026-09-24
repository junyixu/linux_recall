"""Resolve the URL of the active browser tab via Plasma Browser Integration.

The browser extension exports every open tab through a KRunner D-Bus
interface (``org.kde.plasma.browser_integration /TabsRunner``). It does not
mark the active tab, so we strip the browser suffix from the window caption
and look for a tab whose title matches it exactly.

TabsRunner caches the tab list on the first ``Match`` until ``Teardown``, so
we tear down before and after each query to always see fresh titles.
"""

from typing import Any

from jeepney import DBusAddress, MessageType, new_method_call
from jeepney.io.blocking import open_dbus_connection

TABS_RUNNER = DBusAddress("/TabsRunner", bus_name="org.kde.plasma.browser_integration",
                          interface="org.kde.krunner1")

# desktop file id -> window caption suffix
BROWSERS = {
    "firefox": " — Mozilla Firefox",
    "firefox-developer-edition": " — Firefox Developer Edition",
    "librewolf": " — LibreWolf",
    "google-chrome": " - Google Chrome",
    "chromium": " - Chromium",
    "brave-browser": " - Brave",
    "microsoft-edge": " - Microsoft Edge",
    "vivaldi-stable": " - Vivaldi",
}


def is_browser(window: dict[str, Any] | None) -> bool:
    return bool(window) and window.get("desktop_file") in BROWSERS


def get_active_tab(window: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``{"url", "title", "match", "source"}`` for the browser window, or None."""
    title = window["caption"].removesuffix(BROWSERS[window["desktop_file"]])
    if "Private Browsing" in title or len(title) < 3:  # TabsRunner rejects short queries
        return None

    with open_dbus_connection(bus="SESSION") as conn:
        conn.send_and_get_reply(new_method_call(TABS_RUNNER, "Teardown"), timeout=2)
        reply = conn.send_and_get_reply(new_method_call(TABS_RUNNER, "Match", "s", (title,)), timeout=2)
        conn.send_and_get_reply(new_method_call(TABS_RUNNER, "Teardown"), timeout=2)
    if reply.header.message_type == MessageType.error:
        return None  # extension not installed / browser not connected

    # Match -> a(sssuda{sv}): (id, text, icon, category, relevance, properties);
    # relevance == 1 means the title (or url) equals the query case-insensitively
    urls = [props["urls"][1][0] for _, text, _, _, relevance, props in reply.body[0]
            if relevance == 1 and text == title and props.get("urls", ("as", []))[1]]
    if not urls:
        return None
    return {
        "url": urls[0],
        "title": title,
        # several tabs can share a title (e.g. two "New Tab"s); keep the doubt explicit
        "match": "exact" if len(set(urls)) == 1 else "ambiguous",
        "candidates": sorted(set(urls)) if len(set(urls)) > 1 else None,
        "source": "plasma-browser-integration",
    }


if __name__ == "__main__":
    import json

    from linux_recall.kwin import get_kwin_state

    w = get_kwin_state()["window"]
    print(json.dumps(get_active_tab(w) if w and is_browser(w) else None, indent=2, ensure_ascii=False))
