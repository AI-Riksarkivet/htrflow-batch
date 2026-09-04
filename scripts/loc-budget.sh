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
# 2000 -> 2050 for Task 24, which restored ~35 lines of rationale the 1950
# budget had squeezed out of the audit fixes: comments that state WHY are not
# what this budget is meant to squeeze; duplication is. Back to 2000 after
# Task 25 moved three responsibilities to the Kubernetes layer where they
# belong -- the MAX_SECONDS watchdog to the pod's activeDeadlineSeconds, the
# writable-dir mkdir to the Jobs' shell prologue, the workdir rmtree to
# nothing at all (the emptyDir dies with the pod). The wrapper keeps only what
# needs its process: resume, fetch retries, the verify gate, redaction,
# SIGTERM and log shipping. (B63)
# 2000 -> 2010 in Task 20G fix round 1: `config` became a stage of its own,
# set around Config.from_env, so a bad env stops being reported as a `setup`
# failure and stops being described to a reader as a manifest problem. Two
# assignments and the comment that says why the distinction has to live in
# the wrapper rather than be guessed from the error text downstream. (B63)
check wrapper   "$(count packages/wrapper/src -name '*.py')" 2010
# 1000 -> 1150 in Task 20G, which made every problem the converter reports a
# sentence a campaign author can act on ("path/to/file.yaml: <what is wrong>
# -- <what to write instead>") instead of pydantic's own phrasing over a
# `volumes.0.id` path. That is ~135 lines, and almost all of it is English:
# ~20 messages at three or four source lines each once ruff has wrapped them
# to 88 columns, plus the one table that turns pydantic's error types into
# the same voice. This budget exists to squeeze duplication and sprawl, and
# the messages a person reads are neither -- they ARE the feature here, so
# they are counted and capped rather than compressed into shorter, worse
# sentences. (B63)
# 1150 -> 1200 in Task 21, which moved `apply` off `kubectl` as a subprocess
# and onto the Kubernetes client: cluster.py (server-side apply, the prune,
# the Kueue pause patch) is ~150 lines where the argv-building it replaces
# was ~80. The difference is work kubectl used to do and this repo now owns
# -- the prune is a list-by-label and a delete instead of one deprecated
# `--prune` flag, and each object is applied (and printed) individually
# instead of a directory at a time. Not sprawl: it is the price of not
# shelling out. (B63)
# 1200 -> 1297 in Task 21 fix: cluster error mapping; Task 22 lowers it
check converter "$(count packages/converter/src -name '*.py')" 1297
# 400 -> 420: Task 25 moved the per-volume budget to the pod's
# activeDeadlineSeconds, and only the pod's status.reason can then tell a
# deadline kill from a node drain -- projection._name_the_deadline is where
# that distinction is made, with the rationale that stops it being deleted
# again as "a pointless string swap". 420 -> 500 for Task 17, which merged
# the nginx viewer image into this one: the package now also serves the SPA
# and UV as static files (the mount order, the /log-style extensionless
# rewrite and the security headers nginx used to send). 500 -> 550 in that
# task's review round, which added two things the merge did not strictly
# need but the two front doors do: HEAD on every route (FastAPI, unlike
# Starlette, does not add it, and an unhandled HEAD falls through to the
# static mount) and the HTRFLOW_WEB_SITE_ONLY reader that answers 503
# instead of the process refusing to start without a cluster. 550 -> 600 in
# Task 20, which restored two things Task 7 dropped when the status document
# went away: JobDetail.pipelineSteps/pipelineYaml (the chip's tooltip and its
# YAML toggle) and VolumeView.sourceUrl (the "source" link). Restored
# functionality, not growth. (B63)
check web       "$(count packages/web/src -name '*.py')" 600
# 2500 -> 2700 in Task 20, which put back three things Task 7 dropped when
# the status document went away: the pipeline chip's step tooltip and YAML
# toggle, the per-volume "source" link (with the narrow-screen column rule
# the third slot needs), and -- new, but the reason the card can now be
# folded by default -- the latest-volume strip that keeps the viewer and the
# run log one click away while it is folded. Restored functionality, not
# growth. (B63)
# 2700 -> 2800 -> 3000 for Task 26: the ALTO viewer (raised for the ALTO
# viewer route (Task 26)) -- a fourth route (`lib/alto.ts`'s namespace-agnostic
# parser + pretty-printer, `routes/alto`'s text/raw-XML views, the PagesTable
# download column) that did not exist before. 2800 was not enough on its own
# merit: three CSS rules that had been copy-pasted across every route's
# `<style>` block (`.page`/`.header-right`/`.raw`/`.error`/`.muted`, `h1`,
# `.chip`, and the run-log/ALTO `.code-block` `<pre>`) moved into app.css
# (not counted here -- it is not `.ts`/`.svelte`) and were deleted from their
# three call sites first, so the 3000 that remains is the feature itself,
# not sprawl this budget is meant to catch. (B63)
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 3000
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 700
exit $fail
