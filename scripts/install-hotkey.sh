#!/usr/bin/env bash
# Register a KDE global shortcut (default Meta+Alt+R) that runs linux-recall-capture.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
id=net.local.linux-recall-capture.desktop
desktop=${XDG_DATA_HOME:-$HOME/.local/share}/applications/$id
# Qt key code: Meta=0x10000000, Alt=0x08000000, 'R'=0x52; override e.g. KEY=$((0x10000000|0x08000000|0x53))
key=${KEY:-$((0x10000000 | 0x08000000 | 0x52))}

cat >"$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=linux_recall capture
Exec=$repo/.venv/bin/linux-recall-capture
Icon=camera-photo
NoDisplay=true
StartupNotify=false
X-KDE-GlobalAccel-CommandShortcut=true
DESKTOP
kbuildsycoca6 >/dev/null 2>&1

action="['$id', '_launch', 'linux_recall capture', 'linux_recall capture']"
gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel -m org.kde.KGlobalAccel.doRegister "$action" >/dev/null
gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel -m org.kde.KGlobalAccel.setForeignShortcutKeys \
    "$action" "[([$key, 0, 0, 0],)]" >/dev/null
gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel -m org.kde.KGlobalAccel.shortcutKeys "$action"
echo "installed $desktop"
