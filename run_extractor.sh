#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS_MARKER="$VENV_DIR/.paper-notes-requirements"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Preparing the extractor for first use…"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
fi

if [ ! -f "$REQUIREMENTS_MARKER" ] || ! cmp -s "$SCRIPT_DIR/requirements.txt" "$REQUIREMENTS_MARKER"; then
  echo "Installing or updating local components…"
  "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
  cp "$SCRIPT_DIR/requirements.txt" "$REQUIREMENTS_MARKER"
fi

cd "$SCRIPT_DIR"
exec "$VENV_DIR/bin/python" -m streamlit run "$SCRIPT_DIR/extractor_app.py" --server.port 8502
