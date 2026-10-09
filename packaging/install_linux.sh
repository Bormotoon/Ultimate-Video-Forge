#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || ! -f $1 ]]; then
  printf 'Usage: bash packaging/install_linux.sh /path/to/ultimate-video-forge\n' >&2
  exit 2
fi
binary=$1
prefix=${UVF_INSTALL_PREFIX:-"$HOME/.local"}
mkdir -p "$prefix/bin" "$prefix/share/applications"
install -m 755 "$binary" "$prefix/bin/ultimate-video-forge"
ln -sfn ultimate-video-forge "$prefix/bin/uvf"
desktop="$prefix/share/applications/ultimate-video-forge.desktop"
printf '[Desktop Entry]\nType=Application\nName=Ultimate Video Forge\nExec="%s/bin/ultimate-video-forge" --gui\nTerminal=false\nCategories=AudioVideo;Video;\n' "$prefix" > "$desktop"
"$prefix/bin/uvf" --check-resources
printf 'Installed to %s/bin; add this directory to PATH if needed.\n' "$prefix"
