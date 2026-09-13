#!/usr/bin/env bash
# Linux controller preflight. Mac callers inspect Omarchy through the routing helper.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(hostname)" != omarchy ]; then
  exec scripts/omarchy.sh preflight
fi
exec .venv/bin/python scripts/preflight.py
