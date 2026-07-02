#!/bin/sh
set -eu

APP="/opt/owrt-remote/owrt-remote-hub.py"
VENV_PY="/opt/owrt-remote/venv/bin/python"

if [ -x "$VENV_PY" ]; then
	exec "$VENV_PY" "$APP" "$@"
fi

exec "$APP" "$@"
