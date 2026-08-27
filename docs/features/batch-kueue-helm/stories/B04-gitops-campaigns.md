---
type: Product Backlog Item
id: 2845
parent: 2800
title: Declare campaigns in git and let the system run them (GitOps)
---

# B04 · Declare campaigns in git and let the system run them (GitOps)

**Story.** As someone planning transcription work, I want to write down
*which volumes* with *which model pipeline* in a plain text file, open a
pull request, have a colleague approve it, and then have the cluster work
through the list by itself — so that starting a campaign of a thousand
volumes is a reviewed change with a name on it, not a thousand commands.

## Why it matters

Without this, someone has to hand-submit each volume and keep their own
spreadsheet of what is done. With it, the git history *is* the record of
what was asked for, and the bucket *is* the record of what was delivered.
There is no database to back up and no service to keep alive.

## GitOps, in plain words

"GitOps" is the practice of running a system the way we run source code.
Four principles, and what each means here:

| Principle | What it means for campaigns |
|---|---|
| **Git is the single source of truth.** The desired state of the system is a set of files in a repository — nothing else counts. | The *campaigns repo* holds `campaigns/*.yaml` (which volumes, which pipeline) and `pipelines/*.yaml` (which model, which container image). If a volume is not in a file on `main`, the system will not transcribe it. |
| **Changes are declarative.** You describe *what you want*, not the steps to get there. | A campaign file says "these 300 volumes with pipeline `v1`". It does not say "start job 17". The reconciler works out what is missing and submits it. |
| **The system pulls; nobody pushes.** A component in the cluster reads the repo and makes reality match. No person or CI job pushes changes into the cluster. | The reconciler clones the repo every five minutes with a *read-only* token. It is the only component with cluster credentials, and it only ever reads git. The status page never writes anything. |
| **Continuous reconciliation.** The loop runs forever; if reality drifts, it is corrected on the next pass. | Every tick the reconciler compares git (wanted), S3 (done) and the cluster (running), and submits the difference — within a bounded window, so it can never flood the queue. |

What this buys us, in practice:

- **Every change is a pull request.** A new campaign, a new pipeline, or
  removing volumes goes through review. The reviewer sees exactly the diff:
  "add 300 volumes to campaign X", "new pipeline `v2` pinned to image
  `sha256:…`". A CI check on the PR refuses edits to an existing pipeline
  file, because results are keyed by pipeline id and a changed pipeline
  would silently mix two models' output under one name.
- **`main` is protected.** Nobody — including the people who built the
  system — can push straight to `main`; the PR check and the review are the
  only way in. This matters more than usual here: a pipeline file names the
  container image that runs on the GPU with write access to the results
  bucket, so *write access to the repo is cluster-operator access*. Branch
  protection is the control that turns "anyone with a laptop" into "two
  people agreed".
- **The history is the audit trail.** Who asked for which volumes, when,
  approved by whom — `git log` answers it, permanently, with no extra
  bookkeeping.
- **Rollback is `git revert`.** Removing a volume from a campaign stops
  future work on it and never deletes results (it shows as an *orphan* on
  the status page). Reverting a bad pipeline commit stops it being used.
- **Separate repo from the code.** Operations (new campaign) and
  engineering (new feature) change at different rhythms and have different
  reviewers; keeping the campaigns repo separate keeps each PR legible.

The governance side — enabling branch protection, required reviewers and
the PR check on the *real* campaigns repo — is a set-up step on whichever
git host we use, not code; it is its own story ([B11](B11-campaigns-repo-governance.md)).

## What this delivers

- **A campaigns repository layout** with two kinds of file: `campaigns/*.yaml`
  and `pipelines/*.yaml`, each with a documented, validated format.
- **A reconciler** that runs every five minutes, reads the repo, checks
  which volumes are already complete in S3 and which jobs already exist, and
  submits what is missing — never more than a configurable window at a time.
- **Three layered guards** on pipeline immutability: the PR check, a
  per-tick comparison against the live pipeline config, and a comparison
  against results already published under that pipeline id.
- **Automatic model warm-up** the first time a new pipeline appears.
- **A status file** (`status.json`) in S3 describing every campaign and
  volume: pending / queued / running / done / failed, page counts, attempt
  counts, links to the log and the viewer, and a thumbnail of the first
  page.
- Retrying a failed volume is deleting one small record from the bucket;
  the reconciler does the rest.

## Done when

- [ ] Merging a campaign file to `main` results in jobs for every listed
      volume, within the submission window, with no manual step.
- [ ] Volumes already complete in S3 are never resubmitted; removing a
      volume never deletes results.
- [ ] A changed pipeline file is refused by the reconciler when results
      already exist under that id (guards 2 and 3), and by CI on the PR
      (guard 1).
- [ ] A new pipeline triggers exactly one warm-up job before its volumes run.
- [ ] `status.json` is rewritten every tick and reflects reality; failed
      jobs are cleaned up; reconciler runs never overlap.
