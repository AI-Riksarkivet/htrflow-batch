#!/usr/bin/env bash
# Non-test line budgets from the spec (§1). Fails the build when exceeded.
set -euo pipefail
cd "$(dirname "$0")/.."
count() { find "$1" -type f \( "${@:2}" \) -not -path '*/tests/*' -not -name '*.test.ts' -not -path '*/node_modules/*' -print0 | xargs -0 cat 2>/dev/null | wc -l; }
check() { local name=$1 got=$2 max=$3; printf '%-10s %6d / %d\n' "$name" "$got" "$max"; [ "$got" -le "$max" ] || { echo "::error::$name over budget ($got > $max)"; fail=1; }; }
fail=0
check wrapper   "$(count packages/wrapper/src -name '*.py')" 1850
check converter "$(count packages/converter/src -name '*.py')" 850
check api       "$(count packages/api/src -name '*.py')" 400
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 2500
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 700
exit $fail
