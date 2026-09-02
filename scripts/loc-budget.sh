#!/usr/bin/env bash
# Non-test line budgets from the spec (§1). Fails the build when exceeded.
set -euo pipefail
cd "$(dirname "$0")/.."
count() { find "$1" -type f \( "${@:2}" \) -not -path '*/tests/*' -not -name '*.test.ts' -not -path '*/node_modules/*' -print0 | xargs -0 cat 2>/dev/null | wc -l; }
check() { local name=$1 got=$2 max=$3; printf '%-10s %6d / %d\n' "$name" "$got" "$max"; [ "$got" -le "$max" ] || { echo "::error::$name over budget ($got > $max)"; fail=1; }; }
fail=0
# raised for the Task 11 stage split + publish.py, then again for the
# 2026-09-02 wrapper audit's fixes (items 1-7: redacted resume compare,
# per-page failure causes, HF-cache classification, malformed-canvas and
# scheme guards, buffer-level redaction, _Tee.buffer) (B63).
# 2000 -> 2050: the audit's simplifications came out LOC-neutral (they trade
# duplicated call sites for named seams -- ResultStore._put, Config.root_key,
# the from_env env table, driver.build_pipeline), and Task 24 then restored
# ~35 lines of rationale that the 1950 budget had squeezed out of the audit
# fixes. Comments that state WHY are not what this budget is meant to squeeze;
# duplication is. (B63)
check wrapper   "$(count packages/wrapper/src -name '*.py')" 2050
check converter "$(count packages/converter/src -name '*.py')" 1000
# 400 -> 420: Task 25 moved the per-volume budget to the pod's
# activeDeadlineSeconds, and only the pod's status.reason can then tell a
# deadline kill from a node drain -- projection._name_the_deadline is where
# that distinction is made, with the rationale that stops it being deleted
# again as "a pointless string swap". (B63)
check api       "$(count packages/api/src -name '*.py')" 420
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 2500
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 700
exit $fail
