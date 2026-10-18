#!/bin/sh
set -eu
SISA_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python "$SISA_SCRIPT_DIR/package_release.py" "$@"
