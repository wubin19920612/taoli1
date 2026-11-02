#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Only standard-library Python and temporary files; never invokes Docker.
exec "${PYTHON:-python3}" "$SCRIPT_DIR/backup_tools.py" restore-drill "$@"
