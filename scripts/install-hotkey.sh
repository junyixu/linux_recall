#!/usr/bin/env bash
# Register KDE global shortcuts:
#   Meta+Alt+R  linux-recall-capture  (save a capture)
#   Meta+Alt+C  linux-recall-click    (Click to Do: select text in the active window)
# Override the keys with Qt key codes, e.g. CLICK_KEY=$((0x10000000 | 0x08000000 | 0x58)) for Meta+Alt+X
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
META=0x10000000 ALT=0x08000000

# install_hotkey <command> <name> <icon> <qt key code>
install_hotkey() {
    local cmd=$1 name=$2 icon=$3 key=$4
    local id=net.local.$cmd.desktop
    cat >"$apps/$id" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$name
Exec=$repo/.venv/bin/$cmd
Icon=$icon
NoDisplay=true
StartupNotify=false
X-KDE-GlobalAccel-CommandShortcut=true
DESKTOP
    kbuildsycoca6 >/dev/null 2>&1

    local action="['$id', '_launch', '$name', '$name']"
    gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel -m org.kde.KGlobalAccel.doRegister "$action" >/dev/null
    gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel -m org.kde.KGlobalAccel.setForeignShortcutKeys \
        "$action" "[([$key, 0, 0, 0],)]" >/dev/null
    echo "$id: $(gdbus call --session -d org.kde.kglobalaccel -o /kglobalaccel \
        -m org.kde.KGlobalAccel.shortcutKeys "$action")"
}

install_hotkey linux-recall-capture "linux_recall capture" camera-photo "${CAPTURE_KEY:-$((META | ALT | 0x52))}"  # R
install_hotkey linux-recall-click "linux_recall Click to Do" edit-select-text "${CLICK_KEY:-$((META | ALT | 0x43))}"  # C
