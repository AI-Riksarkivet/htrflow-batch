# `htrflow-campaigns retry`: re-run a campaign's failed volumes

Status: design, 2026-11-06. Not yet planned.

## Why

A production campaign of about 3000 volumes finished with about 2000 failed
volumes (the node's scratch space ran out). Every failed volume had spent its
retries, so nothing will run it again in that campaign, and the documented
way back is to fix the cause and list the volumes in a **new** campaign file
([Troubleshooting → Re-run a failed volume](../../getting-started/troubleshooting.md#re-run-a-failed-volume)).

At that scale, "list the volumes" is the problem: working out which 2000 of
3000 have no result, and typing them into a campaign file, is slow and easy
to get wrong. The operator wants one command that takes a campaign name and
starts a new campaign containing only the volumes that are not done --
directly against the cluster, without writing or committing a campaign file.

## Goals

- One command: `htrflow-campaigns retry <campaign> --namespace <ns>`.
- It finds the volumes of `<campaign>` that have no finished result, and
  starts a new campaign Job on the **same pipeline and the same image digest**
  with only those volumes.
- It works after the original Job has been reaped by its TTL.
- It says, before starting anything, how many volumes it found and why they
  failed, grouped; `--dry-run` stops there.
- The retry campaign survives every later `apply --prune` for as long as the
  original campaign's file is in git, and is pruned together with it.
- It can be run again on its own result (`-retry-2` from what `-retry-1`
  still did not finish).

## Non-goals

- It does not fix the cause. Raising `workdir`, freeing a node's disk or
  correcting a manifest URL is the operator's step before running it.
- It does not change the pipeline, the image, the size or any setting. A
  retry with different steps is a new pipeline and a new campaign file, as
  today.
- It does not write to git and does not need a campaigns-repo commit.
- It does not touch results in the bucket.
- No web-page button in this round.

## Anti-goals (would make it a failure even if it works)

- A retry campaign that the next GitOps apply prunes half-way through.
- A way to run anything the campaigns repo did not already approve: other
  volumes, another image, another pipeline.
- Two Jobs writing the same volume's results at the same time.
- A volume silently left out: every volume of the original campaign is
  either done, in the retry, or named in the summary as skipped and why.

## Constraints from the existing system

- **The status ConfigMap is not the source of failed volumes.**
  `campaign-<name>-status`'s `failedVolumes` keeps at most 50 entries
  (`packages/web/src/htrflow_web/projection.py`, `_MAX_FAILURES`) and is only
  written when the campaign's detail page is opened.
- **The campaign ConfigMap is.** `campaign-<name>` holds the full
  `volumes.txt` and the image digest, has no TTL, and lives as long as the
  campaign file is in git (B76).
- **"Done" means `manifest.json`.** A volume is finished exactly when
  `<namespace>/<pipeline>/<volume>/manifest.json` exists; nothing else is a
  completion marker. `export` already reads the results proxy with a store
  login and checks `manifest.json` (`export.py`, `export_http.py`).
- **Why a volume failed** is in its run log,
  `status/logs/<pipeline>/<volume>.txt`, whose terminal line starts
  `permanent failure in …` or `transient failure in … on the last attempt`
  (`wrapper/main.py`, `_transient`, `_advice`). The live Job's pod
  termination messages carry the same, with a `permanent` flag, while the
  pods exist.
- **A campaign file may render several Jobs** (`<stem>-part1`, `-part2`, …)
  sharing one `htrflow.riksarkivet.se/campaign` label value. `retry` takes the
  campaign file's name and covers every part.
- **Prune** deletes every `managed-by=converter` Job/ConfigMap the render did
  not produce, except a status ConfigMap whose campaign ConfigMap is rendered
  (`Cluster._kept_status`). The Argo CD hook always prunes.
- **One apply at a time** is enforced with a Lease (`Cluster.lease`).
- **The same-volume hold** (`cli._claim_volumes`) blocks a campaign whose
  volumes a running campaign on the same pipeline is writing.
- **Admission policies** (job shape, pinned and allowed images, model
  revision) apply to any Job in the namespace; the human running `retry`
  needs create on Jobs and ConfigMaps in the namespace, nothing more.

## Decided

- **Against the cluster, not via git.** The operator's call: the command
  creates the Job and ConfigMap directly. What keeps this inside the
  governance the repo provides is that the retry adds nothing the repo did
  not already approve: same pipeline, same digest, a subset of the original
  campaign's volumes. `retry` refuses if the pipeline's warm-up Job (which
  carries the image; the pipeline ConfigMap holds only the steps) no longer
  matches the digest recorded on the original campaign *(builder's reading,
  settled in review)*.
- **Which volumes.** All volumes of the original campaign (every part) minus
  those with `manifest.json`. The bucket is the truth, not the Job's
  `failedIndexes` and not the status record. A volume that never started (no
  run log) is included like a failed one.
- **Failure grouping.** For each not-done volume, the reason from its run
  log's terminal line (or the live pod's termination message where the Job
  still exists), grouped by reason for the summary.
- **Permanent failures are skipped by default** *(my call, overridable)*:
  volumes whose run log says `permanent failure` (a retry changes nothing:
  bad manifest URL, region/line mismatch) are listed in the summary and left
  out; `--include-permanent` takes them too.
- **Prune keeps a retry with its original.** Retry objects carry the
  converter label (so the same-volume hold, the read API and admission see
  them) plus a label naming the original campaign file. Prune keeps a
  labelled retry Job/ConfigMap — and its status ConfigMap — while that
  original's campaign ConfigMap is in the render, and prunes it with it.
- **Naming.** `<campaign>-retry-<n>`, `n` one higher than the highest retry
  of that campaign in the cluster; a retry of a retry names the **original**
  campaign, so the chain prunes as one. The name must fit the same DNS-label
  limits as split campaigns (`render.split_stem`).
- **Safety checks before creating anything**, refusing with one sentence:
  the original campaign (any part) or an earlier retry of it is still
  running; another running campaign on the same pipeline holds any of these
  volumes; the pipeline's warm-up is not complete; nothing is left to retry.
- **Record.** The retry's ConfigMap records the original campaign, which
  volumes were taken and which were skipped as permanent, who ran it and when
  (the same provenance keys `apply` stamps).
- **Who runs it** *(my call)*: an operator with kubectl access to the
  namespace. It takes the apply Lease for the duration.
- **Output** follows `apply`: one line per object, exit 0 on success, 1 on a
  refusal, `--dry-run` prints the summary and the volume list without
  touching the cluster.

## Left to the builder

- Which Job fields are reset when copying (name, uid, selector, labels the
  Job controller adds, `completions`, `maxFailedIndexes`, the volume
  ConfigMap reference, status).
- The exact label/annotation names, following the existing
  `htrflow.riksarkivet.se/` scheme.
- Concurrency and paging when checking ~3000 `manifest.json`s and reading
  run logs through the results proxy (reuse `export`'s client and session
  handling).
- Splitting a large retry into parts the same way `render` splits a
  campaign.
- How the campaign page shows a retry (it should at least appear, as any
  campaign Job does); no new UI work is required.
- Docs: a "Retry failed volumes" section in Troubleshooting replacing the
  manual steps, and the command in the CLI reference.

- **Same settings as the original, copied.** The operator's call: the retry
  is the original campaign's Job with a new name and a shorter volume list,
  nothing else changed. Its spec is copied from the live original Job; for a
  Job already reaped by its TTL, from a copy of the rendered Job spec that
  `apply` stamps on the campaign ConfigMap (new; campaigns applied before
  this ships can be retried only while their Job exists). No campaigns-repo
  checkout is needed. A consequence the summary states: a cause that lives
  in the settings (a too-small `workdir`) is not fixed by a retry.
- **The original campaign is kept** as it is: its Job, ConfigMap, status
  record and results are not touched.

- **An older prune cannot delete a retry** *(my call)*. The prune exception
  runs wherever `apply --prune` runs, including the Argo CD hook, whose image
  each campaigns repo pins. So `apply` records its converter version on the
  render's sync ConfigMap, and `retry` refuses, naming the version, when the
  last apply in the namespace ran a converter that does not know retries.
  The release notes and the CLI reference say: upgrade the hook first.
