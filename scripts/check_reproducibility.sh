#!/usr/bin/env bash
set -euo pipefail
IGAN_PROJECT=$(cd "$(dirname "$0")/.." && pwd)
source "$IGAN_PROJECT/scripts/env.sh"
python "$IGAN_PROJECT/scripts/check_cache.py"
python "$IGAN_PROJECT/scripts/check_regressions.py"
