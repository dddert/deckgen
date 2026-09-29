#!/usr/bin/env bash
# Портативный LibreOffice без root: официальный AppImage распаковывается в $DECKGEN_HOME/libreoffice.
set -euo pipefail
source "$(dirname "$0")/env.sh"
URL="${LO_APPIMAGE_URL:-https://appimages.libreitalia.org/LibreOffice-still.basic-x86_64.AppImage}"
cd "$DECKGEN_HOME"
curl -L -o lo.AppImage "$URL"
chmod +x lo.AppImage && ./lo.AppImage --appimage-extract >/dev/null
rm -rf libreoffice && mv squashfs-root/opt/libreoffice* libreoffice && rm -rf squashfs-root lo.AppImage
ln -sf "$DECKGEN_HOME/libreoffice/program/soffice" "$DECKGEN_HOME/bin/soffice"
soffice --version
