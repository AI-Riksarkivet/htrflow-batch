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
# 2683 -> 2706 (2026-09-14, verify accounts for failed pages): _verify now
# splits "missing" (an upload that never landed -- transient) from "failed"
# (a page accounted for, with a reason), logs the failed pages' causes where
# the termination message used to be the only copy, and keeps one guard for
# the run where every processed page failed. The lines are the rationale for
# a rule an operator will meet on a live volume.
# 2706 -> 2716 (2026-09-14, manifest.json says how the volume came out):
# pages_ok / pages_failed next to `pages`, so a reader of the completion
# marker can tell a clean volume from one completed with failed pages
# without walking `results`.
# 2716 -> 2721 (2026-09-14, the all-failed guard spares a resume): a run
# that resumed pages is a volume coming out, never a broken model, so the
# guard now also requires that nothing was skipped -- the four lines say why,
# since the case it saves (a SIGTERM at page 637 of 638 whose last page is
# the dead one) is the one this whole rule exists to remove.
# 2721 -> 2748 (2026-09-14, images separator): IMAGES splits on whitespace,
# because a comma is legal inside a URL -- the IIIF size segment
# `/full/2500,/0/default.jpg` was split in half live. `Config.image_urls`
# (+27) is the split, the transition branch that still reads a comma-joined
# line from a pre-fix `rendered/` directory, and the paragraph saying when
# that branch may be deleted; main.py loses the one-line comma split.
# 2748 -> 2742 (2026-09-14, review): the transition rule is documented where
# it is defined -- the converter's `models.split_image_urls` -- so this copy
# of it says only why a copy exists (the GPU image must not carry the
# converter's Kubernetes client) and points at the tests that pin the two.
# 2742 -> 2750 (2026-09-14, hf token): the warm-up's one line saying a Hub
# token is present, and the comment fixing what that line may never say --
# the value, its length, or whose it is.
check wrapper   "$(count packages/wrapper/src -name '*.py')" 2750
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
# 1434 -> 1458 (2026-09-14, images separator): a comma is legal inside a URL
# (the IIIF size segment `/full/2500,/0/default.jpg`), so `images:` URLs are
# joined with a space instead. That only holds if no URL carries whitespace,
# which the models now refuse by name and position (+20), and `source_line`
# says which separator it writes and that both readers split on the first tab
# (+4) -- the contract the Job's shell and the wrapper are written against.
# 1458 -> 1498 (2026-09-14, review blocking 1): the append-only check
# compared volumes.txt byte for byte, so the separator change reported every
# already-rendered `images:` campaign as append-only and left no way to
# re-render it. `models.split_image_urls` + `parse_source_line` (+33) read a
# line back as what it MEANS, comma or space, and cli compares those (+7);
# the docstrings carry the transition rule the wrapper now only points at.
# 1498 -> 1515 (2026-09-14, review nits): `_shown_url` strips userinfo out
# of every URL a validation problem echoes back (+11 with the comment saying
# why a campaign file's own credentials must not reach a CI log), the
# whitespace and non-http sentences wrap over two lines each, and parse.py
# flattens tab and CR as well as newline so one problem stays one line (+3).
# 1515 -> 1517 (2026-09-14, images separator re-review): the transition
# rule's docstring names its one known limit (a query carrying a second URL).
# 1434 -> 1446 (2026-09-14, B76): the Job's `ttlSecondsAfterFinished` became a
# value. A day was short enough that a campaign finished on a Friday was reaped
# before anyone looked at it, and the next apply re-ran every volume.
# ConverterConfig gains the default (a week) and Pipeline the per-pipeline
# override, with the paragraph saying why the window is not the record; render
# sets the field the skeleton now leaves at 0.
# 1446 -> 1503 (2026-09-14, B76): the campaign ConfigMap became the durable
# record of a campaign (the product owner, 2026-09-08: a ConfigMap, not a
# database). render stamps the image digest, the one provenance field that is
# a pure function of the repo; cli._provenance adds the campaigns commit, the
# submitter and the time on the way to the API server, because `rendered/` has
# to stay byte-identical between two renders of the same repo (B78). Most of
# it is `_git_head`/`_submitter`/`_provenance` and the paragraph saying which
# half is rendered and why.
# 1503 -> 1517 (2026-09-14, B76): the read API writes a status ConfigMap the
# converter never renders, so `apply --prune` -- which deletes every
# converter-labelled object not in this render -- would delete it on sight.
# `Cluster._kept_status` keeps the one named after a campaign ConfigMap that
# IS rendered, and prunes it with that campaign when the file leaves git.
# 1517 -> 1586 (2026-09-14, B76): apply reads the record beside the
# append-only check. Past the Job's TTL there is no Job to compare against,
# and an apply that simply recreated it re-ran every volume (observed live on
# e2e-prog, 2026-09-08 -> 14). `Cluster.get` answers "is this object there",
# `_finished` turns the status ConfigMap plus an unchanged volumes.txt into
# the one sentence that is printed instead of the apply, and `_campaign_of`
# names which campaign a rendered object belongs to. Most of `_finished` is
# the paragraph saying why there is no override flag: a campaign that should
# run again is a new campaign.
# 1586 -> 1591 (2026-09-14, B76 review): the provenance key renamed to
# `applied-by`. `htrflow.riksarkivet.se/submitter` is reserved by the
# multi-tenant design (D10/B94) for a LABEL stamped at render time from an
# authenticated forge login; an apply knows only the account it ran under,
# and the added lines are the paragraph saying why the two are not the
# same claim.
# 1591 -> 1601 (2026-09-14, B76 review): "-status" joins "-part<number>" as a
# reserved campaign-file ending. A campaign called `x-status` renders a record
# ConfigMap named `campaign-x-status` -- the very name the read API writes the
# status of campaign `x` to, and which the prune then keeps or deletes on the
# wrong campaign's behalf. STATUS_SUFFIX moves to models.py, where the rule
# that reserves it lives and where render and cluster can both import it.
# 1601 -> 1685 (2026-09-14, B76 review): apply records the ending itself.
# The read API writes the record only while a person has the status page
# open, so a campaign that finished unwatched reached its TTL with no
# terminal record and the next apply ran every volume again. `status_configmap`
# builds the same object the API writes, from the live Job's terminal
# condition (counts from `status.succeeded` against `completions`: for a Job
# that is over, every index that did not succeed failed, and `status.failed`
# counts pods, not indexes), and `_apply` writes it before the skip check
# and hands it straight to `_finished` rather than reading it back.
# 1685 -> 1706 (2026-09-14, B76 re-review): recording how a campaign ended is
# an improvement on the apply, never a precondition for it. `get` on Jobs is a
# verb nothing needed before, so an identity whose Role predates B76 -- or a
# human on a restricted kubeconfig -- raised a ClusterError and the apply
# returned 1 with NOTHING applied. `_record_and_decide` is the observe and the
# skip decision as one step (the decision reads the record the same step just
# wrote), wrapped per campaign: one sentence on stderr, and that campaign
# applied as any other.
# 1706 -> 1715 (2026-09-14, B76 re-review): a refused record WRITE is not a
# refused decision. Skipping `_finished` over it re-applied a campaign the
# stored record already said was finished -- the whole GPU bill again, over a
# permission the decision never needed. The write is caught where it happens,
# the stored record still consulted, and only a refused READ falls through to
# applying the campaign as any other.
# 1517 + 1715 - 1434 = 1798 (2026-09-14, merge): the two chains above met.
# They forked at 1434 and touched different files -- the separator work is in
# models.py and _render's append-only compare, the record work in cli's apply
# path, render's status_configmap and cluster's get/prune -- so the merged
# count is both, with nothing double-counted.
# 1798 -> 1814 (2026-09-14, merge follow-up): the finished-and-unchanged
# check compares the two volume lists parsed, like the append-only check
# beside it. Byte for byte, a campaign applied before the `images:` separator
# changed carries the comma line while this render writes the space line --
# the same volumes, said twice -- so a finished campaign whose Job the TTL
# had reaped was applied again and re-ran every volume over a separator.
# 1814 -> 1842 (2026-09-14, hf token): `hf_token_secret` -- the field, the
# DNS-1123 subdomain a Secret name has to be (spelled out, because the
# shorthand character class accepts names the API server refuses), and the
# comment saying why only the warm-up ever gets the token.
# 1842 -> 1863 (2026-09-14, hf token): the warm-up Job's HF_TOKEN
# `secretKeyRef`, and the comment saying why the campaign Job gets nothing --
# that omission is the design, so it has to be readable at the place a later
# reader would otherwise "fix" it.
# 1863 -> 2125 (2026-09-14, B77): the three answers to "field is immutable",
# which is what a live apply said about EVERY warm-up Job the day a
# converter.yaml-wide setting was added, and about a running campaign's Job
# the day a pipeline's image moved. cluster (+79): `ImmutableField` reads the
# refused fields out of `details.causes` rather than out of a message that
# quotes a whole Go pod-template struct back, and `replace_job` is the
# delete-and-create a changed pod template has no other route to, with the
# paragraph saying why the create must follow the DELETION and not the delete
# call. cli (+183): the apply loop catches per object, counts what was
# refused and exits 3 rather than aborting at the first one and leaving every
# later campaign unapplied; `_apply_object` holds the rule that a warm-up Job
# is replaced and a campaign Job never is (its indexes are the campaign), and
# `_edited_pipeline` holds the one that a pipeline id is a permanent name for
# a recipe. render (+26): `recipe`, the two things a pipeline file alone
# decides. Most of the count is those paragraphs: each of the three is a rule
# whose OPPOSITE looks reasonable at the call site, so the reason lives next
# to the code rather than in a story nobody opens again.
# 2125 -> 2157 (2026-09-14, B77 review): four fixes, each of them the
# paragraph that keeps the next reader from undoing it. The recipe is
# compared PARSED, so reordering the keys of a step -- or a PyYAML that
# spells a mapping differently one release from now -- is not a changed
# recipe. `_render` takes the record directory apart from `--out`, because
# an apply with no `--out` renders into a temp directory that records
# nothing while the repo's committed `rendered/` is still the record. An
# unenforced pause outranks a refused object at the exit code. And a server
# message this package repeats is cut at MAX_MESSAGE: a 422 with no
# `details.causes` still carries the whole rejected pod template.
check converter "$(count packages/converter/src -name '*.py')" 2157
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
# 1025 -> 1031 (2026-09-14, a done row can carry failed pages): the
# manifest.json fallback's comments now say why a finished volume may have
# lost pages and why the reason for them is progress.json's to name, not
# this document's -- manifest.json records no order among its results.
# 1031 -> 1213 (2026-09-14, B76): the read API stopped being the only thing
# that knows how a campaign ended. projection gains `startedAt`/`finishedAt`,
# `campaign` (a split campaign's parts share one campaign FILE), and
# `status_record`/`status_configmap` -- the field names `htrflow-campaigns
# apply` parses back, so most of those lines are the paragraph saying they
# are a contract and why the list endpoint leaves `failedVolumes` alone.
# kube gains `list_configmaps` and `apply_configmap` (server-side apply, one
# request, no read-modify-write race); app gains `_record`/`_status_configmaps`
# and the wiring in both routes, with the reasoning for writing only a changed
# body and for never failing a request over a record it could not write.
# 1213 -> 1377 (2026-09-14, B76): a campaign whose Job the TTL reaped still
# shows. projection gains `record_summary` (the same row shape `summarize`
# returns, out of the two ConfigMaps, so the page needs no second case
# beyond the chip), `_record_failures` and `record_detail`; app gains
# `_reaped_detail` and the merge in the list route, plus `jobGone` on every
# row. The comments carry the two rules that are not obvious from the code:
# a live Job always wins over its record, and a campaign ConfigMap with no
# status ConfigMap beside it gets no row at all.
# 1377 -> 1384 (2026-09-14, B76 review): a reaped campaign whose record never
# reached a terminal phase reads `Unknown`, not `Running`. The Job is gone, so
# nothing is running, and a `Running` that can never change is the one answer
# that is certainly wrong; the comment says which Job deletions leave this
# behind now that apply writes the terminal record itself.
# 1384 -> 1412 (2026-09-14, B76 review): `merge_record` -- the record only
# ever gains. A campaign's pods are collected long before its record is, so a
# detail request an hour later sees no reasons at all, and writing that back
# erased the only place those sentences survive. A value that says nothing
# ("", "[]") never replaces one that says something, and `finishedAt` never
# moves backwards -- two writers, two clocks.
# 1412 -> 1434 (2026-09-14, B76 review): the write is on the request's
# critical path, so it is bounded and quiet. RECORD_WRITES_PER_REQUEST caps
# one page load at 20 server-side applies instead of one per campaign in the
# namespace (the rest are written by the next poll, and by the apply itself,
# which is what guarantees a terminal record exists at all); `refused`
# remembers the namespaces whose last write was denied, so an RBAC grant that
# was never renewed says so once instead of once per campaign per poll.
# 1434 -> 1474 (2026-09-14, audit) F1: a client error that is not a 404 --
# a 403 after an RBAC change, a 429, a connection that timed out -- escaped
# as a bare exception, and Starlette answers those OUTSIDE the header
# middleware: a plain-text 500 with no nosniff and no frame-ancestors on it.
# kube.py now raises one `ClusterUnavailable` for all of them and app.py
# answers it with a one-sentence 502 that carries the headers, and a pod
# whose completion-index label is not a number is skipped rather than taking
# the page down with it.
# 1474 -> 1513 (2026-09-14, audit) T1/T2: `Reader` was the one class in this
# package with no test of its own, and the fakes had nothing tying them to
# it. `ReaderLike` writes the duck type down (a test binds every double
# against it), and `apply_configmap` stops passing `force=True` -- `apply`
# writes the same record from the live Job once a campaign is over, and
# forcing took those terminal values back off it on every poll; a 409 while
# the other manager is mid-write is retried once instead.
# 1513 -> 1559 (2026-09-14, audit) F2: the campaign list pulled every campaign
# record ConfigMap WITH its `volumes.txt` on every poll -- one line per volume,
# megabytes for a real backfill, for labels and a date. The records are now
# listed as PartialObjectMetadata (the one call this adapter makes through
# `call_api`, since the generated methods overwrite `Accept`), and the status
# ConfigMaps -- whose `data` IS the reaped campaign's row -- as a second,
# label-separated list.
# 1559 -> 1571 (2026-09-14, audit) F4: the progress fan-out had a per-row cap
# but no overall budget, so an unreachable bucket held one worker for the cap
# times progress.py's timeout -- over a minute, inside a sync handler -- and
# enough such requests emptied the threadpool /healthz is answered from. The
# rows past a five-second deadline are answered with no progress.
# 1571 -> 1621 (2026-09-14, audit) F5: /uv.html is a third-party page with no
# <meta> CSP of its own, and the only thing the header forbade on it was
# framing -- while the page takes its manifest URL straight from the
# fragment. It gets a policy of its own, with the built viewer's one inline
# <script> and one inline <style> hashed into it at startup so nothing has
# to be allowed by 'unsafe-inline'.
# 1621 -> 1632 (2026-09-14, audit) F6: a done volume's row is cached for an
# hour because its counts never change again -- but its `ageSeconds` was
# computed at fetch and cached with them, so a card read "updated 8 s ago"
# all afternoon. The age is recomputed from the row's own timestamp on every
# cache hit; nothing is re-fetched.
# 1632 -> 1645 (2026-09-14, audit) F7: a refused namespace was remembered for
# logging only, so an unrenewed RBAC grant still cost twenty server-side
# applies on the critical path of every page load, for ever. The refusal now
# also stops the writes for a cooldown, which expires so a renewed grant
# starts working without a restart.
# 1645 -> 1666 (2026-09-14, audit) F8: the campaign detail route took any
# namespace and any name, put both into an API path and into the ConfigMap
# names built from them, and ignored the namespaces the service was actually
# given. Both halves are checked against one DNS-1123 label and against
# `cfg.namespaces` before any read happens.
# 1666 -> 1690 (2026-09-14, audit) F9: a volume's progress file was read
# whole, with no size cap and no cap on the strings inside it, and the URL it
# came from was followed wherever it redirected. It is now streamed and
# abandoned past 64 KB, redirects are refused, and the three strings a person
# reads off the card are clipped.
# 1690 -> 1695 (2026-09-14, audit) F15: a volume id went into the progress
# URL unencoded, so an id with `../` in it -- volumes.txt is a file people
# edit in a git repo -- was normalised into a request for another key.
# 1695 -> 1715 (2026-09-14, audit) F11: the campaign record's failedVolumes
# capped the number of entries but not their size, and a reason is whatever
# the wrapper's termination message said -- a traceback, a pod-template
# struct. Fifty of those is a ConfigMap the API server refuses, which is a
# campaign with no record at all, so each reason is clipped and the oldest
# entries are dropped until the field fits.
# 1715 -> 1738 (2026-09-14, audit) F19: the rule that a campaign's finishedAt
# never moves backwards was a string compare, and its two writers need not
# write the same offset -- `09:00Z` sorts before `10:00+02:00` while being an
# hour after it. The two are parsed and compared as moments, a value with no
# offset read as UTC.
# 1738 -> 1792 (2026-09-14, R1) a campaign whose Job the TTL reaped answered
# its detail route with no volume rows at all, so the page showed it as
# finished and nobody could open a volume in the viewer, read its ALTO or its
# run log -- while manifest.json, iiif.json, alto/ and the log were all still
# in the bucket. The rows are rebuilt from what has no TTL (the record's
# volumes.txt, the status record's failedVolumes, the campaign's own ending),
# through the same row builder a live campaign's rows come from, and their
# page counts come from the bucket like a live campaign's.
check web       "$(count packages/web/src -name '*.py')" 1792
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
# 3549 -> 3581 (2026-09-14, a volume completes with failed pages): a verify
# failure is now one of two things -- pages missing from the results, or a run
# in which every page processed failed -- and each gets its own sentence,
# naming only the pages the retry will actually redo. The `done` stage leaves
# the progress line so a finished volume reads as its pages and its failures.
# 3581 -> 3777 (2026-09-14, running motion): the product owner, watching a
# live run, could not tell a running campaign from a finished one -- nothing
# on the card moved. Three gestures, running only: a pulsing dot in the state
# and phase chips, a progress bar that eases to its new width on each poll
# (with a sheen crossing it between polls), and a one-second highlight on the
# progress line whose page count actually changed. Markup, aria and the
# reduced-motion rules are what cost lines; the keyframes are kept small.
# 3581 -> 3613 (2026-09-14, B76): the "job removed" chip and the finished
# date. `jobGone` and `finishedAt` join jobSummarySchema (defaulted, so an
# older API still parses), the card grows a neutral chip -- the Job's removal
# is housekeeping, not a verdict, and the phase chip beside it already
# carries the verdict -- and the meta line says when the campaign finished,
# which past the TTL is the only date left that means anything.
# 3613 -> 3624 (2026-09-14, B76 review): `Unknown` joins the phase enum and
# reads "outcome unknown" on the chip, styled with queued/paused rather than
# with failed -- nobody wrote down how the campaign ended, which is not the
# same as it having gone wrong.
# 3777 + 3624 - 3581 = 3820 (2026-09-14, merge): the two chains above met.
# The motion work and the reaped-campaign chip both grew CampaignCard, and
# both survive: a card can pulse while it runs and say "job removed" once its
# Job is gone, and the phase chip carries one of the two at a time.
# 3820 -> 3823 (2026-09-14, merge follow-up): the 404 sentence stopped saying
# a finished campaign is removed after 24 hours -- wrong twice, since the TTL
# is configurable and a reaped campaign is still served from its record. It
# names the one thing a 404 now means, with the comment saying why the Job's
# TTL is not it.
# 3823 -> 3847 (2026-09-14, card layout): the campaign card's created date
# and its Models line were each a row of their own -- the date wedged between
# the models and the volume table, the models a full-width row of links that
# read louder than the header above them. They become one small muted line at
# the foot of the header block. The markup costs more lines than the two it
# replaces (the line is one <p> with two spans, and either half can be
# absent) and so does the paragraph saying why there is no "Models (5)"
# expander: a real pipeline names two or three models, so the line fits and
# clipping it with a title is the whole of the narrow-screen case.
# 3847 -> 3857 (2026-09-14, card layout review): the quiet line's links pay
# for their own accessibility. The underline moves to the text colour (the
# hairline it had was 1.26:1 on the light card, 1.31:1 on the dark -- nothing
# marked a link at rest), the links take the focus ring every other control
# on this card wears, and the clipped line clips with a margin so that ring
# survives at its edge. Four declarations, a rule of its own for focus, and
# the comment saying which contrast the underline now carries.
# 3857 -> 3937 (2026-09-14, audit) F3/F14: every poll on this site -- the
# campaign list, each card's own volume table, the live run log -- had no
# in-flight guard, never passed its AbortController's signal into fetch (so
# an abandoned request ran to completion and only its answer was dropped),
# polled a tab nobody was looking at, and asked a dead API at full cadence
# for ever. $lib/poll is the one poller all three now share: one request in
# flight, paused while `document.hidden`, doubling the wait on consecutive
# failures up to five minutes, and polling at once on the way back.
# 3937 -> 3949 (2026-09-14, audit) F17: the page-progress bar took its width
# and its aria-valuemax straight from the wrapper's progress.json -- counts
# that arrive over the network and that a half-written run can make disagree
# with itself -- so a bar 900% wide, or one running backwards, was a page the
# numbers could ask for. Both the fill and the value a screen reader is told
# are clamped to the track.
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 3949
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
# 763 -> 769 (2026-09-14, B76): the web Role gains create/patch on ConfigMaps
# -- the read API writes one object now, the per-campaign status ConfigMap
# that still answers for a campaign once its Job is past the TTL. The added
# lines are the paragraph saying which object, why `create` is not a second
# privilege (a server-side apply of a missing object is a create) and what is
# still forbidden.
# 769 -> 774 (2026-09-14, B76 review): the apply identity's Role gains `get`
# on jobs and configmaps -- the command now reads each campaign's live Job to
# record how it ended, and reads the record back to leave a finished campaign
# alone. `list` does not authorize a read by name.
# 774 -> 776 (2026-09-14, audit) D17: the web Role granted `watch` on
# jobs, pods and configmaps and nothing in packages/web has ever opened one
# -- every response is computed from a get or a list on the request. Two
# lines of comment for three verbs removed.
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 776
exit $fail
