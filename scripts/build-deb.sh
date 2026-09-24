#!/usr/bin/env bash
# Build dist/linux-recall_<version>_amd64.deb for Plasma 6 distros: Debian 13, Kubuntu 25.04+, KDE neon.
# onnxruntime and rapidocr are not packaged by Debian, so the .deb carries its own Python and
# a venv built from uv.lock under /usr/lib/linux-recall; the venv's absolute paths are baked in
# at build time, so this builds in place there (needs sudo; run it in CI or a throwaway container).
# Usage: scripts/build-deb.sh [version]   (default: the version in pyproject.toml)
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
version=${1:-$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml")}
python=${PYTHON_VERSION:-3.13}
prefix=/usr/lib/linux-recall
stage=$repo/dist/deb
deb=$repo/dist/linux-recall_${version}_amd64.deb

sudo rm -rf "$prefix" "$stage"
sudo install -d -o "$(id -u)" -g "$(id -g)" "$prefix"

export UV_PYTHON_INSTALL_DIR=$prefix/python UV_PROJECT_ENVIRONMENT=$prefix/venv
uv python install "$python"
(cd "$repo" && uv sync --frozen --no-dev --no-editable --compile-bytecode \
    --python "$python" --python-preference only-managed)
# RapidOCR downloads its models into site-packages on first use, which is read-only once installed
"$prefix/venv/bin/python" -c 'from rapidocr import RapidOCR; RapidOCR()'
rm -rf "$prefix"/python/*/lib/python3.*/{test,idlelib,tkinter,turtledemo}
"$prefix/venv/bin/python" -m compileall -q "$prefix/venv/lib"

mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/lib"
cp -a "$prefix" "$stage/usr/lib/"
for cmd in linux-recall-capture linux-recall-daemon linux-recall-click; do
    ln -s "$prefix/venv/bin/$cmd" "$stage/usr/bin/$cmd"
done
install -Dm644 "$repo/systemd/linux-recall.service" "$stage/usr/lib/systemd/user/linux-recall.service"
install -Dm755 "$repo/scripts/install-hotkey.sh" "$stage/usr/bin/linux-recall-install-hotkey"
# lr.zsh aliases lrf from its own directory
install -Dm755 "$repo/scripts/lrf" "$stage/usr/share/linux_recall/lrf"
install -Dm644 "$repo/scripts/lr.zsh" "$stage/usr/share/linux_recall/lr.zsh"
ln -s /usr/share/linux_recall/lrf "$stage/usr/bin/lrf"
install -Dm644 -t "$stage/usr/share/doc/linux-recall" "$repo/README.md" "$repo/README.zh-CN.md"
install -Dm644 "$repo/LICENSE" "$stage/usr/share/doc/linux-recall/copyright"

cat >"$stage/DEBIAN/control" <<CONTROL
Package: linux-recall
Version: $version
Architecture: amd64
Maintainer: Junyi Xu <junyixu0@gmail.com>
Installed-Size: $(du -sk "$stage/usr" | cut -f1)
Depends: bash, spectacle, libglib2.0-bin, libgl1
Recommends: libnotify-bin, plasma-browser-integration, jq, fzf, imagemagick, kitty
Section: utils
Priority: optional
Homepage: https://github.com/junyixu/linux_recall
Description: Windows Recall-style screen memory for KDE Plasma 6 on Wayland
 Screenshots the active window on a hotkey or every minute, and records the
 window, browser URL, app context and OCR text as a WebP plus a JSON file,
 searchable from the command line.
CONTROL

dpkg-deb --root-owner-group -Zxz --build "$stage" "$deb"
echo "$deb"
