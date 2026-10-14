#!/usr/bin/env bash
# Host acceptance without inference, publication, resource replacement or credential output.
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  '') exec "$ROOT/setup.sh" --check ;;
  --reports)
    echo 'OMA report rendering is not imported. See docs/migration/pr1-import-inventory.md.' >&2
    exit 2 ;;
  *) echo 'Usage: verify.sh (host readiness; no model calls)' >&2; exit 2 ;;
esac
