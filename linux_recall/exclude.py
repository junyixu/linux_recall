"""Windows that are never captured: password managers, auth prompts, private browsing, banking sites.

Checked in ``take()`` right after the KWin query, before Spectacle runs, so
nothing of an excluded window ever touches the disk (not even the cache).
Only the active window is checked: a ``fullscreen`` capture still shows
whatever else is on screen.
"""

from typing import Any
from urllib.parse import urlsplit

# KWin desktopFileName (the .desktop file id without the suffix)
APPS = {
    "org.keepassxc.KeePassXC", "bitwarden", "1password", "com.bitwarden.desktop",
    "org.kde.kwalletmanager", "org.kde.kwalletmanager5", "org.gnome.seahorse.Application",
    "org.kde.polkit-kde-authentication-agent-1", "org.kde.ksshaskpass", "org.gnupg.pinentry-qt",
    "org.kde.spectacle",  # its region-select overlay is a frozen image of the whole desktop
}

# substrings of the window caption
CAPTIONS = ("Private Browsing", "(Incognito)", "(Private)", "[InPrivate]")

# domains of the active tab's URL, subdomains included; needs Plasma Browser Integration
# (no URL -> not excluded)
SITES = ("boc.cn",)


def excluded(window: dict[str, Any] | None, browser: dict[str, Any] | None = None) -> str | None:
    """Why ``window`` must not be captured, or None if it may be."""
    if not window:
        return None
    if window.get("desktop_file") in APPS:
        return window["desktop_file"]
    caption = window.get("caption") or ""
    if hit := next((c for c in CAPTIONS if c in caption), None):
        return hit
    # an ambiguous title match excludes if any of the candidate tabs is excluded
    urls = (browser["candidates"] or [browser["url"]]) if browser else []
    return next((site for url in urls for site in SITES if _on_site(url, site)), None)


def _on_site(url: str, site: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == site or host.endswith("." + site)
