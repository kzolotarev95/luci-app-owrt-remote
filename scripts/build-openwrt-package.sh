#!/bin/sh
set -eu
SOURCE_DIR="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
exec python3 "$SOURCE_DIR/scripts/openwrt_package.py" build "$@"
