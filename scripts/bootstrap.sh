#!/usr/bin/env bash
set -euo pipefail
IGAN_PROJECT=$(cd "$(dirname "$0")/.." && pwd)
IGAN_CACHE=${IGAN_CACHE:-"$HOME/.cache/igan-nextflow"}
IGAN_PYTHON=${IGAN_PYTHON:-python3.11}
mkdir -p "$IGAN_CACHE/tools" "$IGAN_PROJECT/results/setup"
exec > >(tee -a "$IGAN_PROJECT/results/setup/bootstrap.log") 2>&1
"$IGAN_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else "Python 3.11 required")'
"$IGAN_PYTHON" "$IGAN_PROJECT/scripts/bootstrap_tools.py" "$IGAN_PROJECT" "$IGAN_CACHE"
if [[ ! -x "$IGAN_CACHE/tools/python/bin/python" ]]; then
  "$IGAN_PYTHON" -m venv "$IGAN_CACHE/tools/python"
fi
"$IGAN_CACHE/tools/python/bin/python" -m pip install 'pip==25.2'
"$IGAN_CACHE/tools/python/bin/python" -m pip install --only-binary=:all: -r "$IGAN_PROJECT/requirements.lock" -r "$IGAN_PROJECT/requirements-dev.lock"
"$IGAN_CACHE/tools/python/bin/python" -m pip check
source "$IGAN_PROJECT/scripts/env.sh"
python "$IGAN_PROJECT/scripts/versions.py" > "$IGAN_PROJECT/results/setup/software_versions.json"
