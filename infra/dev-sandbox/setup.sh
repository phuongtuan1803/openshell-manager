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
    --agent|--model|--auth-mode)
      (($# >= 2)) || { echo 'Missing option value' >&2; exit 2; }
      if [[ "$1" == --agent && "$2" != codex ]]; then offline=true; fi
      args+=("$1" "$2"); shift 2 ;;
    *) echo 'Usage: setup.sh [--agent codex|claude|opencode] [--check|--dry-run]' >&2; exit 2 ;;
  esac
done
if ! command -v python3 >/dev/null; then
  if [[ "$action" != apply || "$offline" == true ]]; then
    echo 'FAIL | Python | Run setup.sh to install host utilities.'
    exit 1
  fi
  sudo apt-get update
  sudo apt-get install -y python3 python3-yaml ca-certificates
fi
if [[ "$action" == apply && "$offline" == false ]] && ! python3 -c 'import yaml' >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-yaml
fi
if ! python3 -c 'import tomllib' >/dev/null 2>&1; then
  echo 'FAIL | Python | Use Linux Lite 7 / Ubuntu 24.04 with Python 3.11 or later.'
  exit 1
fi
exec python3 "$ROOT/control.py" setup "$action" "${args[@]}"
