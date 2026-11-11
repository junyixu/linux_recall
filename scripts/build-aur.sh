#!/usr/bin/env bash
# Build the AUR package from this checkout instead of from GitHub.
#
# usage: scripts/build-aur.sh <aur-checkout> [outdir]
#
# The AUR PKGBUILD is a VCS (-git) package: source=("git+<github url>") tracks
# the repo tip and pkgver() derives the version from the commit count.  That
# makes it impossible to build a package for one specific commit, so this
# script rewrites the source line to a local copy of the working tree and
# freezes pkgver to the value the VCS pkgver() would have produced.
#
# Only the source= line and pkgver= are touched; depends, provides, optdepends
# and package() stay verbatim from the AUR PKGBUILD, so the result is the same
# package the AUR would build (modulo the pinned commit).
#
# makepkg insists on a clone of the source URL and a history long enough for
# pkgver(), so the local copy gets a stand-in history: `rev-list --count`
# empty commits ending at the current tree.  Git strips empty commits when
# cloning, so they cost no disk space.  The tip commit also gets a different
# hash, which is why pkgver is pinned here instead of recomputed there.
set -euo pipefail

aur_dir=${1:?usage: build-aur.sh <aur-checkout> [outdir]}
out=${2:-$PWD/dist}

root=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
cd "$root"

# read from the AUR PKGBUILD without executing it
_pkgname=$(sed -n 's/^_pkgname=//p' "$aur_dir/PKGBUILD")
pkgname=$(sed -n 's/^pkgname=//p' "$aur_dir/PKGBUILD")
[[ -n $_pkgname && -n $pkgname ]] || { echo "no pkgname/_pkgname in $aur_dir/PKGBUILD" >&2; exit 1; }

count=$(git rev-list --count HEAD)
pkgver="r$count.$(git rev-parse --short=7 HEAD)"

work=$(mktemp -d)
[[ ${KEEP:-} ]] || trap 'rm -rf "$work"' EXIT
echo "work dir: $work" >&2

mkdir -p "$work/source" "$work/pkgsrc" "$out"
git archive --format=tar HEAD | tar -x -C "$work/source"
git -C "$work/source" init -q

# one commit holding the tree, then padding so the count matches
git -C "$work/source" add -A
git -C "$work/source" -c user.name=makepkg -c user.email=makepkg@localhost \
  commit -q -m "source" --date=@0
for _ in $(seq 2 "$count"); do
  git -C "$work/source" -c user.name=makepkg -c user.email=makepkg@localhost \
    commit -q --allow-empty -m "padding" --date=@0
done

# makepkg clones a VCS source into $srcdir/<basename of the URL>, so the URL's
# last path segment decides where build() finds the sources
mv "$work/source" "$work/$_pkgname"

# only the source= line and pkgver= change; depends, optdepends, provides and
# package() stay verbatim, so this is the package the AUR would build
sed -E \
  -e 's/^pkgver=.*/pkgver='"$pkgver"'/' \
  -e '/^source=\(/s#^source=\(.*\)$#source=("git+file://'"$work"'/'"$_pkgname"'")#' \
  "$aur_dir/PKGBUILD" > "$work/pkgsrc/PKGBUILD"

# --nodeps: the builder is a throwaway container without the KDE stack, and
# makepkg writes a working package without it
# --holdver: pkgver() in a VCS source would rewrite the pinned version
(cd "$work/pkgsrc" && PKGDEST=$out SRCDEST=$work makepkg --noconfirm -f --nodeps --holdver)
