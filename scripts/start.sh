#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/../src${PYTHONPATH:+:$PYTHONPATH}"
agent="${1:-codex}"
if (($#)); then shift; fi
if [[ "${1:-}" == --autonomous ]]; then
  shift
  exec python3 -m openshell_manager run "$agent" "$@"
fi
exec python3 -m openshell_manager start "$agent" "$@"
