#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
agent="${1:-codex}"
if (($#)); then shift; fi
if [[ "${1:-}" == --autonomous ]]; then
  shift
  exec python3 "$ROOT/control.py" run "$agent" "$@"
fi
exec python3 "$ROOT/control.py" start "$agent" "$@"
