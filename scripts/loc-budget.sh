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
# 2010 -> 2028 in Task 28 fix round item 1: a bad HF repo id/revision
# (RepositoryNotFoundError/RevisionNotFoundError) joins PERMANENT_ERRORS, and
# LocalEntryNotFoundError (a ValueError by MRO, but a cache miss) is carved
# back out via TRANSIENT_FIRST -- the import, the two tuples and their
# rationale comments, and the collapsed except block that classifies both.
# (B63)
# 2028 -> 2038 in Task 28 fix round item 2: the offline-Hub and missing/
# unreadable PIPELINE_PATH guards were the only two warm-up failure paths
# that wrote no termination message; a `_fail` helper gives both the same
# {stage, permanent, error} shape the try/except writes. (B63)
check wrapper   "$(count packages/wrapper/src -name '*.py')" 2038
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
# the Kueue pause patch) is ~150 lines -- ~235 with the error boundary
# the review asked for -- where the argv-building it replaces
# was ~80. The difference is work kubectl used to do and this repo now owns
# -- the prune is a list-by-label and a delete instead of one deprecated
# `--prune` flag, and each object is applied (and printed) individually
# instead of a directory at a time. Not sprawl: it is the price of not
# shelling out. (B63)
# 1200 -> 1293 in Task 21 fix: cluster error mapping. 1293 -> 1284 in Task
# 22, which paid that back: the image allow-list and the model-revision
# requirement left this package for Kyverno ClusterPolicies the chart ships
# (~45 lines of rules, config fields and validation context), and ~35 came
# back as English -- the sentence that tells an author whose converter.yaml
# still carries one of those keys where the rule went, and the `validate`
# help that says what this command no longer checks. A rule that moved out
# of the tool has to leave a signpost behind, or its author reads
# "not a setting this file has" and goes looking for a typo. 1284 -> 1287
# in the same task: an admission webhook's rejection arrives as a paragraph
# with blank lines in it, and _api_error now reflows it, since every other
# problem this package prints is one sentence. 1287 -> 1283 in Task 22 fix
# round 2: the moved-key check now collects every offending key and raises
# once instead of stopping at the first (a few lines longer), but that is
# more than paid back by deleting `Pipeline.model_revision` -- a field
# nothing read, whose only consumer was its own validator and whose only
# test this task had already removed. `Pipeline` now forbids unknown keys,
# so a stale `model_revision:` gets the same one-line sentence as any other
# typo. (B63)
check converter "$(count packages/converter/src -name '*.py')" 1283
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
# 600 -> 650 for Task 28: warm-up status on the campaign card. kube.Reader
# gained list_warmups (one more list call, sharing _list_jobs with list_jobs);
# projection gained match_warmup/warmup_phase/warmup_reason and summarize's
# warmup field; app.py's _warmup_status wires them together, matching by
# namespace + pipeline label and reading the warm-up Job's own pods only for
# a failed match (ruling 2's one extra list_pods; nothing cached). (B63)
# 650 -> 660 in Task 28 fix round item 3: _warmup_status now memoizes the
# failed-match reason by warm-up Job name for the request, so two campaigns
# sharing one failed warm-up cost one list_pods call, not one per campaign.
# (B63)
check web       "$(count packages/web/src -name '*.py')" 660
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
# 3000 -> 3068 for Task 28: api.ts's warmupPhaseSchema/warmupSchema (and the
# reason schema moved up to sit above them), reasons.ts's warmup stage
# phrase, and CampaignCard.svelte's chip (health, label, tooltip, the
# open-card reason line, four CSS rules). (B63)
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 3068
# 700 -> 730 in Task 22, which moved three cluster rules out of the
# converter and into `templates/policies/`: digest pinning, the image
# allow-list and the model-revision requirement, as Kyverno ClusterPolicies
# the API server enforces on everything the namespace admits (the converter
# only ever saw what the converter rendered). ~195 lines for three policies,
# a third of it the comments that say why each is written with `context` +
# `deny` rather than `foreach` -- a rule whose message cannot name the
# offending image is a rule its reader has to guess at.
# 730 -> 738 Task 22 fix (B63): the model-revision policy learned TrOCR's
# real placement (model_settings.model_kwargs.revision, not top-level like
# YOLO) -- one more JMESPath OR clause, a longer message, and the comment
# that explains why two placements exist at all.
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 738
exit $fail
