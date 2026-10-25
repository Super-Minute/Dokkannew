#!/usr/bin/env bash
# Build the standalone `dokkan` executables.
#   ./build.sh linux    — native Linux binary (PyInstaller onefile) -> dist/dokkan
#   ./build.sh windows  — Windows .exe; see the instructions it prints
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-linux}" in
  linux)
    python3 -m venv .build-venv
    .build-venv/bin/pip install --quiet --upgrade pyinstaller
    .build-venv/bin/pyinstaller --onefile --name dokkan \
      --paths src --clean --noconfirm \
      --exclude-module pytest --exclude-module tkinter \
      src/dokkan/cli/main.py
    echo "built: dist/dokkan"
    ;;
  windows)
    echo "PyInstaller cannot cross-build a Windows .exe from Linux."
    echo "Pick one:"
    echo "  1. On a Windows machine with Python 3.10+:  run build.bat  ->  dist\\dokkan.exe"
    echo "  2. Here with Wine installed:                  wine cmd /c build.bat"
    echo "Both produce the same onefile executable with the same commands,"
    echo "save files and bot_config.json as the Linux build."
    exit 1
    ;;
  *)
    echo "usage: ./build.sh [linux|windows]" >&2
    exit 2
    ;;
esac
