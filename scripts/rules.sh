#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
action="${1:-status}"
if (($#)); then shift; fi
exec python3 "$ROOT/../src/openshell_manager/control.py" rules "$action" "$@"
