#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/../src${PYTHONPATH:+:$PYTHONPATH}"
action="${1:-status}"
if (($#)); then shift; fi
exec python3 -m openshell_manager rules "$action" "$@"
