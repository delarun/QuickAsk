#!/bin/sh
# Сборка gtk4-layer-shell из исходников — для Ubuntu 24.04, где его нет в пакетах.
# Ставит библиотеку и typelib в /usr, откуда их увидят PyGObject и хуки PyInstaller.
#
#   sudo packaging/build-gtk4-layer-shell.sh            # версия по умолчанию
#   sudo packaging/build-gtk4-layer-shell.sh 1.3.0
set -eu

VERSION="${1:-1.3.0}"
SRC="${TMPDIR:-/tmp}/gtk4-layer-shell-$VERSION"

export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
    ca-certificates curl meson ninja-build pkg-config \
    libgtk-4-dev libwayland-dev wayland-protocols \
    gobject-introspection libgirepository1.0-dev

rm -rf "$SRC"
mkdir -p "$SRC"
curl -fsSL "https://github.com/wmww/gtk4-layer-shell/archive/refs/tags/v$VERSION.tar.gz" \
    | tar -xz -C "$SRC" --strip-components=1

cd "$SRC"
# без примеров, тестов и vapi — нужны только .so и typelib для Python
meson setup build --prefix=/usr --buildtype=release \
    -Dexamples=false -Ddocs=false -Dtests=false -Dsmoke-tests=false \
    -Dintrospection=true -Dvapi=false
ninja -C build
ninja -C build install
ldconfig
