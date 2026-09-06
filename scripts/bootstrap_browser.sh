#!/usr/bin/env bash
set -euo pipefail
IGAN_PROJECT=$(cd "$(dirname "$0")/.." && pwd)
source "$IGAN_PROJECT/scripts/env.sh"
IGAN_BROWSER_TOOLS="$IGAN_CACHE/tools/browser-qa"
mkdir -p "$IGAN_BROWSER_TOOLS"
cp "$IGAN_PROJECT/package.json" "$IGAN_PROJECT/package-lock.json" "$IGAN_BROWSER_TOOLS/"
npm ci --prefix "$IGAN_BROWSER_TOOLS" --ignore-scripts --no-audit --no-fund
if [[ -z "${IGAN_BROWSER_PATH:-}" ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$IGAN_CACHE/tools/browsers"
  node "$IGAN_BROWSER_TOOLS/node_modules/playwright/cli.js" install chromium
fi
