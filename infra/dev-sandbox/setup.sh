#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
action=apply
offline=false
args=()
while (($#)); do
  case "$1" in
    --check) action=check; shift ;;
    --dry-run) action=dry-run; shift ;;
    --agent|--model|--auth-mode|--project|--host)
      (($# >= 2)) || { echo 'Missing option value' >&2; exit 2; }
      if [[ "$1" == --agent && "$2" != codex ]]; then offline=true; fi
      args+=("$1" "$2"); shift 2 ;;
    *) echo 'Usage: setup.sh [--agent codex|claude|opencode] [--check|--dry-run]' >&2; exit 2 ;;
  esac
done
# Configuration must be validated before any bootstrap/installation operation.
command -v python3 >/dev/null || { echo 'Python 3.11+ required; install explicitly.' >&2; exit 1; }
python3 -c 'import tomllib, yaml' || { echo 'Python 3.11+ and PyYAML required; install explicitly.' >&2; exit 1; }
exec python3 "$ROOT/control.py" setup "$action" "${args[@]}"
