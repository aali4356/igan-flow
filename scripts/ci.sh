#!/usr/bin/env bash
set -euo pipefail
IGAN_PROJECT=$(cd "$(dirname "$0")/.." && pwd)
source "$IGAN_PROJECT/scripts/env.sh"
cd "$IGAN_PROJECT"
mkdir -p results/ci
ruff check lib scripts tests
ruff format --check lib scripts tests
python -m pytest -q tests --junitxml=results/ci/pytest.xml
python scripts/check_regressions.py
bash scripts/run.sh test
bash scripts/verify.sh --results results/test/main/current
node scripts/qa_report.cjs results/test/main/current/report.html results/ci/browser
python scripts/record_ci.py
