"""Query the active window from KWin (Plasma 6, Wayland) over D-Bus.

Wayland has no global "active window" API, so we do what kdotool does:
load a throw-away KWin script via ``org.kde.KWin /Scripting``, let it read
``workspace.activeWindow`` and ``callDBus`` the result back to our unique
bus name, then unload it.
"""

import json
import os
import tempfile
import uuid
from typing import Any

from jeepney import DBusAddress, MatchRule, MessageType, new_method_call, new_method_return
from jeepney.io.blocking import DBusConnection, open_dbus_connection

from linux_recall.paths import CACHE_DIR

KWIN = DBusAddress("/Scripting", bus_name="org.kde.KWin", interface="org.kde.kwin.Scripting")
CALLBACK_IFACE = "net.local.LinuxRecall"
CALLBACK_MEMBER = "state"

SCRIPT_TEMPLATE = """
const w = workspace.activeWindow;
let info = null;
if (w) {
    const g = w.frameGeometry;
    info = {
        caption: w.caption,
        resource_class: w.resourceClass,
        resource_name: w.resourceName,
        desktop_file: w.desktopFileName,
        pid: w.pid,
        internal_id: w.internalId.toString(),
        output: w.output ? w.output.name : null,
        geometry: {x: g.x, y: g.y, width: g.width, height: g.height},
        fullscreen: w.fullScreen,
    };
}
const screens = workspace.screens.map((s) => ({
    name: s.name,
    geometry: {x: s.geometry.x, y: s.geometry.y, width: s.geometry.width, height: s.geometry.height},
    scale: s.devicePixelRatio,
}));
callDBus(%(service)s, "/", %(iface)s, %(member)s, JSON.stringify({window: info, screens: screens}));
"""


def _call(conn: DBusConnection, addr: DBusAddress, method: str, sig: str = "", body: tuple = ()) -> tuple:
    reply = conn.send_and_get_reply(new_method_call(addr, method, sig, body), timeout=5)
    if reply.header.message_type == MessageType.error:
        raise RuntimeError(f"{addr.bus_name} {method}: {reply.body}")
    return reply.body


def get_kwin_state(timeout: float = 3.0) -> dict[str, Any]:
    """Return ``{"window": <focused window or None>, "screens": [...]}``.

    All geometry is in KWin's logical (scale-independent) coordinates.
    """
    plugin = f"linux_recall-{uuid.uuid4().hex[:8]}"
    with open_dbus_connection(bus="SESSION") as conn:
        script = SCRIPT_TEMPLATE % {
            "service": json.dumps(conn.unique_name),
            "iface": json.dumps(CALLBACK_IFACE),
            "member": json.dumps(CALLBACK_MEMBER),
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=plugin, suffix=".js", dir=CACHE_DIR)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(script)
            (script_id,) = _call(conn, KWIN, "loadScript", "ss", (path, plugin))
            if script_id < 0:
                raise RuntimeError(f"KWin refused to load script {path}")

            rule = MatchRule(type="method_call", interface=CALLBACK_IFACE, member=CALLBACK_MEMBER)
            with conn.filter(rule) as queue:
                script_addr = DBusAddress(f"/Scripting/Script{script_id}", bus_name="org.kde.KWin",
                                          interface="org.kde.kwin.Script")
                _call(conn, script_addr, "run")
                msg = conn.recv_until_filtered(queue, timeout=timeout)
                conn.send(new_method_return(msg))
        finally:
            try:
                _call(conn, KWIN, "unloadScript", "s", (plugin,))
            finally:
                os.unlink(path)

    state = json.loads(msg.body[0])
    if (window := state["window"]) is not None:
        window.update(_process_info(window.get("pid")))
        window["app_name"] = _desktop_entry_name(window.get("desktop_file")) or window.get("resource_class")
    return state


def _process_info(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"process_name": None, "exe": None}
    try:
        with open(f"/proc/{pid}/comm") as f:
            comm = f.read().strip()
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return {"process_name": None, "exe": None}
    return {"process_name": comm, "exe": exe}


def _desktop_entry_name(desktop_file: str | None) -> str | None:
    """Resolve a desktop file id (e.g. ``firefox``) to its ``Name=`` entry."""
    if not desktop_file:
        return None
    data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    for base in [data_home, *data_dirs]:
        path = os.path.join(base, "applications", f"{desktop_file}.desktop")
        try:
            with open(path, encoding="utf-8") as f:
                in_entry = False
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        in_entry = line == "[Desktop Entry]"
                    elif in_entry and line.startswith("Name="):
                        return line.removeprefix("Name=")
        except OSError:
            continue
    return None


if __name__ == "__main__":
    print(json.dumps(get_kwin_state(), indent=2, ensure_ascii=False))
