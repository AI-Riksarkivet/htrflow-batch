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
# 2028 -> 2035 in Task 28 fix round item 2: the offline-Hub and missing/
# unreadable PIPELINE_PATH guards were the only two warm-up failure paths
# that wrote no termination message; a `_fail` helper gives both the same
# {stage, permanent, error} shape the try/except writes. (B63)
# 2035 -> 2117 (2026-09-07): provenance.py stamps an htrflow-batch
# <Processing> block (image digest, htrflow base revision, wrapper version)
# into every ALTO; IMAGE_DIGEST/HTRFLOW_BASE_REVISION became Config fields.
# 2117 -> 2155 (2026-09-07, B73/audit X2): the memory fixes. stream._discard
# is the rolling delete the workdir always needed -- the image AND both output
# files, since the workdir is a memory-backed emptyDir and the outputs were
# ~300 KB per page (+11 net, after the inlined try/except it replaces).
# driver.release_document (+27) drops a finished page from htrflow's
# module-global progress registries, which nothing else pops; most of it is
# the comment that says why a wrapper reaches into another package's
# underscore names at all, and why it releases two Document objects per page.
# 2155 -> 2194 (2026-09-07, B73 review round): driver +24 -- _outputs is
# lifted out of process_page so the except branch can discard what a FAILED
# page wrote (consume's rolling delete reaches only the files a page
# RETURNS), and release_documents empties htrflow's registries outright
# rather than naming Document objects it cannot enumerate, since only
# ProcessImages steps return a new one and a pipeline may have several.
# store +11 / viewer +3 / publish -2: the ALTO WIDTH/HEIGHT are kept from
# the parse upload_page already does, so publishing a 2 000-page volume
# stops making 2 000 sequential S3 GETs of full ALTO bodies; the dead
# local-file branch in alto_dims pays part of it back. stream +3: keep_images
# says what it keeps.
# 2194 -> 2197 (2026-09-07, B73 re-review nit): the failure-path cleanup is
# guarded so an OSError there cannot replace the exception being raised.
# 2197 -> 2207 (2026-09-07, B75/audit X4): the warm-up marker stopped being
# best-effort. `_write_marker` returns the sentence that names the file it
# could not write instead of logging a warning, and `main` routes it through
# `_fail` before the success log -- a warm-up that exits 0 with no marker is a
# green Job whose campaigns then hold a GPU in their init container until the
# deadline, with nothing anywhere saying why.
# 2207 -> 2238 (2026-09-07, B75/audit X4): the warm-up gets the batch
# wrapper's SIGTERM handler -- the Job's 1 h activeDeadlineSeconds is terminal,
# so a slow first download was ending in an empty termination message. `main`
# installs it and `_warmup` is the body it wraps (the split, the handler, the
# except/finally and the widened `.main` import, which ruff wraps one name per
# line).
# 2238 -> 2327 (2026-09-08, B88): the dead-inference-thread guard. htrflow's
# Inference steps wait on a future their daemon thread completes, so an
# exception in that thread leaves `pipeline.run` blocked forever and the pod
# holding its GPU to the deadline (R0001203, 43 pages in). driver gains
# PipelineDead, `_dead_step`/`_dead` (the sentence naming step and model) and
# `_run_guarded`, which runs the page in a helper thread and checks liveness
# before, during and after -- +79, over half of it the paragraph saying why a
# wrapper runs another package's `run` in a thread of its own and what the
# stuck daemon costs. main +10: the factory drops the dead pipeline and
# rebuilds it before the next page.
# 2327 -> 2368 (2026-09-08, B88 review round): two holes in the guard.
# `release_pipeline` drops the dead pipeline's weights before its replacement
# loads its own -- the thread parked in run() holds the steps, so dropping the
# pipeline reference freed nothing and a recurring model bug would have OOMed
# the GPU one rebuild at a time (+26, most of it that paragraph). `_dead_step`
# also watches the BatchedQueue's own daemon, whose death hangs the run
# identically while `step._thread` is still alive (+11), main +5 for the
# release call, and the post-run liveness check goes: a thread dies before it
# completes the batch's futures, so a run that returned kept complete outputs.
# 2368 -> 2546 (2026-09-08, C13/C11): the volume says how far it has got
# while it is still running. progress.py (+112) owns both writes -- progress.json
# after every page outcome and at every stage change, and the incremental
# iiif.json every PUBLISH_EVERY_PAGES pages, so a 638-page volume opens in the
# viewer at page 10 instead of at page 638; over half of it is the paragraphs
# saying why a status write is best-effort, why a skipped page counts as done,
# and why the cadence is pages rather than the log-ship clock. main +26:
# RunState.stage becomes a property whose setter publishes the stage (eight
# assignments, one hook), the resumed pages seed StreamStats BEFORE the loop
# instead of being patched in after it, and a `done` stage is set after
# publish. store +14 put_progress (the run log's short-timeout client: a
# status write must not pin the page loop), stream +14 the `stats`/`on_page`
# hooks the loop had no way to expose, publish +6 known_dims -- the one line
# both manifests start from, lifted out of alto_dims rather than copied.
# 2546 -> 2604 (2026-09-08, the product owner's addition to C13): "when we
# have an exception in the log it would be nice to see some notice on the
# front page". progress.py +32: `last_error` -- the most recent failed page
# and the sentence the wrapper already recorded for it, redacted like every
# other error that reaches the public bucket and capped at LAST_ERROR_CHARS
# (a chip, not a traceback) -- and the WARNING count beside it. logship +25:
# WarningCounter, a logging.Handler installed and removed with the rest of the
# run's logging; counted at the logging call because the shipped log is
# truncated in the middle and grepping it back would count a warning twice or
# not at all -- most of those lines say exactly that. main +1 hands the
# capture to the tracker.
# 2604 -> 2661 (2026-09-08, page-progress review round, items 2/3/4/6):
# logship's WarningCounter becomes ErrorCounter at logging.ERROR -- the
# wrapper's own benign WARNINGs (a pipeline rebuild, "manifest covers n/m
# pages") were lighting the chip on every healthy run -- and progress.json's
# field is `errors`, not `warnings`, everywhere it is named (+8, mostly the
# docstring saying why ERROR and not WARNING, and that an uncaught thread
# exception is not a log record at all -- B88 turns that into last_error
# instead). progress.py +19: `viewer_published`, true only once an iiif.json
# PUT has actually succeeded (interim or final) -- the frontend's "open" link
# switches on this now, never on a page count, so a volume under
# PUBLISH_EVERY_PAGES pages does not link to a manifest that is not there
# yet; the interim publish is also skipped whenever the dims this run holds
# cover fewer pages than `done` says are finished, since store.page_dims only
# ever holds this run's own pages and a resume would otherwise overwrite a
# complete iiif.json with one naming only the pages since resume. publish.py
# +4: `run` returns whether it wrote iiif.json, so main.py can tell the
# tracker the final publish covered it too. main.py +19: `RunState.tracker`
# and `main`'s finally write one terminal "failed" stage on every exit path
# that is not the successful one -- before this the file stayed at whatever
# stage the run was doing when it stopped, "stream" forever, on a volume
# that had in fact failed or been SIGTERMed.
# 2661 -> 2683 (2026-09-08, first live run of page-progress): fetch.describe --
# a failed page records a sentence, never repr(e). PipelineDead("page 0044:
# ...") had reached the notice chip verbatim; the wrapper's own exceptions
# are shown as they are, a foreign one keeps its type in front.
check wrapper   "$(count packages/wrapper/src -name '*.py')" 2683
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
# 1283 -> 1340 (2026-09-07, B72): the campaign split stopped being a volume
# count. `split` carries a byte budget as well (an `images:` volume is one
# line of joined URLs, so 45 of them can pass the count and still blow the
# 1 MiB ConfigMap limit), `campaign_names` cuts the campaign name to what an
# Indexed Job's own name may be, `cli` looks an earlier render up under that
# cut name, and `validate` refuses a campaign file named like a part. Half
# of the 57 lines is the three API-server rules written down where the next
# reader will look for them -- the exact messages the server answers with,
# so nobody has to rediscover them from a failed apply.
# 1340 -> 1382 in B72's review round (2026-09-07): the split had two ways to
# rename a campaign that is already applied, and a rename is a delete plus a
# restart of every volume once `apply --prune` sees it. `render` now refuses
# same-volumes-different-object-names (naming both shapes) and two campaigns
# whose cut names collide (naming both files), and the stem is reserved from
# constants so a campaign crossing 9 parts to 10 keeps the name its Jobs
# already carry. The rest is the 1 MiB rule written down as the API server
# actually applies it -- it sums data values, and the margin is a margin.
# 1382 -> 1395 (2026-09-07, B72 re-review): the colliding-stem check moved
# ahead of the earlier-render comparison and became `_colliding_names` over
# every campaign -- two campaigns sharing a stem but not their volume lists
# were being told the first one's parts made them append-only. A pre-pass
# costs one `split` per campaign and saves the wrong sentence.
# 1395 -> 1425 (2026-09-07, B74/audit X3+X5): the warm-up gate stopped being
# a wait with no end. `_WARMUP_WAIT` is the bounded loop the init container
# runs -- a counter, the sentence it prints on stderr and the exit 13 the
# Job's podFailurePolicy turns into FailIndex -- and most of its lines are
# the comment saying why a wait costs GPU-hours at all (the pod reserves its
# GPU through its init containers, and Kueue holds the quota through them).
# `_scheduling` lifts runtimeClassName/nodeSelector/tolerations out of
# `_campaign_job` so the warm-up Job gets the same three from the same cfg;
# it is a net +3 lines of code over the block it replaces, the rest being
# the paragraph on what a warm-up on the wrong node does to a ReadWriteOnce
# cache PV. Plus `warmup_wait_seconds` in ConverterConfig.
# 1425 -> 1427 (2026-09-07, B74 fix round): the gate compares with `-le`,
# not `-lt` -- the check runs before each sleep, so `-lt` gave up one step
# early while printing the limit it had not reached. Two lines saying so.
# 1427 -> 1430 (2026-09-07, B74 fix round): the bound is clamped to the
# pod's own activeDeadlineSeconds. A pipeline with a `max_seconds:` under
# `warmup_wait_seconds` was killed by the kubelet at 143 -- which no
# FailIndex rule matches -- before the gate could give up, so it went
# straight back to retrying with a held GPU. `deadline` becomes a local
# (-4) and the min() plus the four lines saying why cost 7.
# 1430 -> 1434 (2026-09-07, B74 fix round 2): the clamp subtracts one sleep
# step and floors at one, so the gate expires STRICTLY before the pod. `min`
# alone left the two expiring together, and a tie goes to the kubelet (the
# pod's clock starts at pod start, the gate's when the init container runs)
# -- which gives 143, no FailIndex and no sentence. Four lines are the
# comment saying which clock wins and why that matters.
check converter "$(count packages/converter/src -name '*.py')" 1434
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
# a failed match, one list_pods per failed warm-up per request. (B63)
# 650 -> 667 in the Task 28 fix round: the failed-warm-up reason is
# memoized per (namespace, warm-up Job) for the request, `warmup` is a
# required argument of summarize/detail, match_warmup refuses an empty
# pipeline label, and app.py calls wrapper_reason/newest directly. (B63)
# 667 -> 690 (2026-09-07, B74 fix round): a pod that failed in an INIT
# container was read as having no reason at all -- `wrapper_reason` looked
# only at `containerStatuses`, and a sentence on the card is the whole point
# of B74's bounded warm-up gate. The per-container lookup becomes
# `_terminated_message`, called twice (the named main container, then any
# init container that exited non-zero), and most of the 23 lines is the two
# docstrings: why a SUCCEEDED init container explains nothing, and why the
# message that arrives is plain stderr rather than the wrapper's JSON.
# 690 -> 705 (2026-09-08): GET /api/v1/version, so the page's header can say
# which build is answering it. importlib.metadata on this package's own
# distribution, read once at import; most of the 15 lines is the comment
# saying what that version is NOT -- not the release tag, not the wrapper
# image a campaign runs, neither of which this process can see.
# 705 -> 722 (2026-09-08): the version the header shows became the deployed
# image's tag. `HTRFLOW_BATCH_VERSION` is a kube.Config field like every other
# env this service reads, `__main__` hands it to `create_app` (site-only mode
# has no cfg on its reader to take it from, and app.py reads no environment of
# its own), and the route answers with it alongside the web package's own
# version -- which is reported beside the tag, never instead of it.
# 690 -> 855 (2026-09-08, C13/C11): "how many pages has it done?" -- the one
# question the Kubernetes API cannot answer, since the count lives in the pod.
# progress.py (+125) is the whole read side of the wrapper's progress.json:
# the two mappings onto this API's shape (the progress file, and manifest.json
# for a volume finished before that file existed), the few-second memo, and
# the rule that anything unreadable is no progress rather than a 500. Most of
# it is the paragraph on why this reader exists at all and why its cost is
# bounded by the response rather than by the archive. projection +25:
# `_attach_progress` over the rows the answer carries (the page, plus `latest`
# and the failures, which come from outside it) and the campaign's summed
# pagesDone/pagesTotal -- the fetch is injected, so the module stays pure.
# app +15: one reader per app (its client and cache are shared by every
# request), injectable so tests need no bucket.
# 855 -> 893 (2026-09-08, the product owner's addition to C13): the three
# notice fields. progress.py +21 maps `last_error`/`warnings` through and
# drops a last_error that is not the shape we write (the file is ours, but it
# arrives over the network like any other document); projection +17 turns the
# per-volume fields into what a campaign card can show -- the summed failed
# pages and warnings, and the most recent failure with the volume it happened
# in and that volume's run log, since the row it came from is usually outside
# the page the reader is on.
# 893 -> 955 (2026-09-08, page-progress review round, items 2/4/5/9):
# progress.py's field is `errors`, matching the wrapper's rename, plus
# `viewerPublished` (passed through from progress.json, true for the
# manifest.json fallback since a finished volume's iiif.json is written
# before it) and `ageSeconds` -- computed from the API's own clock
# (time.time() at fetch time) so a browser's clock skew cannot make a row
# read "0 s ago". projection.py's PROGRESS_FETCH_CAP (32) bounds
# _attach_progress to that many sequential GETs regardless of `limit`,
# running rows first, so a big page cannot turn into a thousand fetches
# through one client.
# 955 -> 962 (2026-09-08, page-progress review round, item 8): app.py builds
# no ProgressReader at all in site-only mode (reader.cfg is None) -- every
# /api/v1/... route 503s before ever reaching progress.fetch, so the HTTP
# client a ProgressReader opens would have had nothing to ask.
# 962 -> 989 (2026-09-08, page-progress review round, item 1 -- BLOCKING):
# HTRFLOW_INTERNAL_RESULTS_BASE (kube.Config, +5), the address THIS POD
# reaches the results bucket at, separate from HTRFLOW_PUBLIC_RESULTS_BASE
# (the browser-facing one) -- on the PoC the two are not the same URL, and
# the API pod's ProgressReader was silently resolving `localhost` to itself
# and returning no progress ever, on every campaign. projection.py +9:
# `_internal_results_base`, used ONLY for the progress fetch -- every
# browser-facing URL still comes from the public one.
# 989 -> 992 (2026-09-08, page-progress re-review nit): a naive `updated_at`
# is read as UTC rather than the API pod's local zone -- unreachable today
# (the wrapper writes tz-aware) but a wrong age is worse than no age.
# 992 -> 1025 (2026-09-08, page-progress merged with main): the two chains
# above met -- card-links' 690 -> 722 (+32) on top of page-progress, plus one
# line for create_app's signature now carrying both batch_version and
# progress.
check web       "$(count packages/web/src -name '*.py')" 1025
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
# 3068 -> 3063 in Task 28 fix round items 5+9: the four warmup chip CSS
# rules folded into the existing phase rules they duplicated (-7), and the
# health derivation grew one guard clause and a longer comment (+2) --
# `missing` only paints a finished campaign red when it is not Succeeded.
# (B63)
# 3063 -> 3100 (2026-09-07, B32 regression): fetchJobs reads the list row
# by row -- one campaign the page cannot read is left out, counted and
# logged once, instead of hiding every other campaign; the page shows the
# count in a banner (describeUnreadable). A wrong shape still fails hard.
# 3100 -> 3179 (2026-09-08): lib/pipeline.ts -- the models a campaign's
# pipeline names, read off JobDetail.pipelineYaml for the card's Models line.
# No YAML library for a document we render ourselves and want two keys from;
# over half the file is the paragraph saying so and naming the two revision
# placements it reads (the same two the Kyverno model-revision policy
# accepts), so the next reader does not have to rediscover them from a
# rejected apply.
# 3179 -> 3229 (2026-09-08): the card's Models line -- one link per model
# under the pipeline chip, `<name> @<short sha>` to the repo's tree at that
# commit (or `unpinned`, linking to main), so the weights that produced a
# campaign's results are one click away from the campaign. CampaignCard +47
# (the derived list, the wrapping line and its four CSS rules); pipeline.ts
# +3 for the strict-TypeScript regex idiom runlog.splitLogLine already uses.
# 3229 -> 3310 (2026-09-08): the status page's header says where the code is
# and which build is answering. +62 in +page.svelte, most of it the GitHub
# mark inlined as SVG (the page's CSP loads nothing from a third origin) and
# the four rules that keep it and the version legible in both themes; +16 in
# api.ts for fetchVersion and its schema, +3 for config.REPO_URL. The version
# is read once, and a version nobody could fetch is simply absent -- it is a
# footnote in the header, never a reason for an alert over the campaign list.
# 3310 -> 3311 (2026-09-08): the header shows the deployed tag rather than
# the web package's number -- `webVersion` is kept for the tooltip, and the
# span fits on one line again.
# 3100 -> 3187 (2026-09-08, C13/C11): the volume rows say how far they have
# got. api.ts's volumeProgressSchema (+ the two campaign totals on the detail),
# reasons.ts's describeProgress and its `ago` helper -- "137 / 638 pages ·
# processing pages · updated 12 s ago" is a sentence a person reads, so it is
# written where every other one is -- and CampaignCard's line under each state
# chip, the header's summed pages, and the open link that now goes live at the
# first published page instead of at the last.
# 3187 -> 3300 (2026-09-08, the product owner's addition to C13): the notice
# chip. api.ts's two extra progress fields and the campaign's three
# (+ CampaignNotice, the slice of JobDetail the chip needs), reasons.ts's
# describeNotice -- "1 page failed · 2 warnings · page 0044: htrflow's
# Segmentation worker thread died", counts first because they scan the same
# for every campaign -- and CampaignCard's chip: two branches (a link when the
# API sent a run log for the volume it happened in, a plain chip otherwise),
# the derived href, and the clipping rules that keep a long sentence from
# rewrapping the whole header.
# 3300 -> 3326 (2026-09-08, page-progress review round, items 2/4/7/9):
# api.ts's `ageSeconds`/`errors`/`viewerPublished` replace `warnings`;
# reasons.ts's `formatAge` takes a count of seconds from the API instead of
# computing one from `updatedAt` and the browser's Date.now() (`describeProgress`
# drops its `now` param entirely); CampaignCard's openHref switches on
# `progress.viewerPublished`, never a page count, and the notice chip carries
# its full sentence in a `.sr-only` node (not only `title`), reachable by
# keyboard and assistive tech even while the visible copy is CSS-clipped.
# 3326 -> 3338 (2026-09-08, product owner on the PoC card): the failures
# callout no longer repeats a failed row the open table already shows --
# `unseenFailures` (folded: all of them, open: only those off the loaded
# page) and a heading that says so, plus two tests.
# 3338 -> 3549 (2026-09-08, page-progress merged with main): card-links'
# 3100 -> 3311 (+211) on top of page-progress -- the two chains above met.
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 3549
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
# 738 -> 763 (2026-09-08, page-progress review round, item 1 -- BLOCKING):
# htr-web's NetworkPolicy gains an S3 egress rule -- the same shape as the
# batch Job's own (network.yaml's `$s3`, recomputed here since it is a
# separate template file), for HTRFLOW_INTERNAL_RESULTS_BASE's
# ProgressReader -- plus the env var itself, defaulted from
# web.internalResultsBase.
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 763
exit $fail
