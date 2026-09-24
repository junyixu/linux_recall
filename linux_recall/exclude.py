"""Windows that are never captured: password managers, auth prompts, private browsing.

Checked in ``take()`` right after the KWin query, before Spectacle runs, so
nothing of an excluded window ever touches the disk (not even the cache).
Only the active window is checked: a ``fullscreen`` capture still shows
whatever else is on screen.
"""

from typing import Any

# KWin desktopFileName (the .desktop file id without the suffix)
APPS = {
    "org.keepassxc.KeePassXC", "bitwarden", "1password", "com.bitwarden.desktop",
    "org.kde.kwalletmanager", "org.kde.kwalletmanager5", "org.gnome.seahorse.Application",
    "org.kde.polkit-kde-authentication-agent-1", "org.kde.ksshaskpass", "org.gnupg.pinentry-qt",
    "org.kde.spectacle",  # its region-select overlay is a frozen image of the whole desktop
}

# substrings of the window caption
CAPTIONS = ("Private Browsing", "(Incognito)", "(Private)", "[InPrivate]")


def excluded(window: dict[str, Any] | None) -> str | None:
    """Why ``window`` must not be captured, or None if it may be."""
    if not window:
        return None
    if window.get("desktop_file") in APPS:
        return window["desktop_file"]
    caption = window.get("caption") or ""
    return next((c for c in CAPTIONS if c in caption), None)
