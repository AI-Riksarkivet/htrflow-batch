# Documentation audit — 2026-09-04 (B63 Task 12)

> **History.** This is the inventory that drove the B63 documentation pass:
> one row per page, the verdict, and every stale claim with the code fact
> that contradicts it. The pages themselves are the current record; this file
> records what was wrong on 2026-09-04 and is not maintained afterwards.

Method: each page was checked **against the code**, never against another
page — `make -n <target>` for every Make target a page names, env names
against `packages/wrapper/src/htrflow_batch/config.py` and
`packages/web/src/htrflow_web/kube.py`, chart values against
`charts/*/values.yaml`, API fields against
`packages/web/src/htrflow_web/projection.py`, converter fields against
`packages/converter/src/htrflow_converter/models.py`, rendered Job fields
against `packages/converter/src/htrflow_converter/manifests/` +
`render.py`, numbers against `scripts/loc-budget.sh`, versions against
`Chart.yaml` / `pyproject.toml`.

Ground truth on this branch (`b63-indexed`, HEAD `1a5b6f6`):

| Fact | Value |
|---|---|
| Packages | `packages/{wrapper,converter,web}` — no `packages/api` |
| Images | `riksarkivet/htrflow-batch` (wrapper), `riksarkivet/htrflow-web` |
| Versions | chart `htrflow-batch` 0.6.0 · `htrflow-devstack` 0.2.0 → 0.3.0 in this pass · wrapper 0.2.0 · web 0.1.0 · converter 0.1.0 |
| LOC budgets | wrapper 2035 · converter 1283 · web 667 · frontend 3063 · chart 738 |
| Policy | Kyverno ClusterPolicies from the chart (`security.policies.enabled`); the converter checks only the digest *shape* |
| `apply` | official Kubernetes client, server-side apply — no `kubectl` subprocess, no `--kubectl` flag |
| Per-volume budget | pod `activeDeadlineSeconds` (`max_seconds`); the wrapper has no `MAX_SECONDS` |
| Frontend routes | `/`, `/log`, `/alto` (three) |
| Only `status/` key written | `status/logs/<pipeline>/<volume>.txt` (`store.ResultStore.run_log_key`) |

## Inventory

### Root and package READMEs

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `README.md` | rewrite (small) | "the wrapper, converter and api unit tests" | the third package is `packages/web` (`htrflow_web`); `packages/api` does not exist |
| `packages/wrapper/README.md` | rewrite (small) | Module table omits `publish.py` | `packages/wrapper/src/htrflow_batch/publish.py` exists and owns the publish stage |
| `packages/converter/README.md` | rewrite (small) | `converter.yaml` renders an "image allow-list"; module table omits `cluster.py` | `ConverterConfig` has no allow-list field — it was moved to `security.allowedImageRepos` (Kyverno) in Task 22, and `_MOVED_TO_THE_CHART` rejects the old key; `cluster.py` is the Kubernetes-client apply |
| `packages/web/README.md` | rewrite (small) | Phase list `Succeeded`/`Failed`/`Queued`/`Paused`/`Running`; `JobDetail` described as summary + `volumes` + `failures`; route list omits `/alto` | `projection._phase` also returns `PartiallyFailed`; `projection.detail` also returns `pipelineSteps`, `pipelineYaml` and `latest`; `frontend/src/routes/alto/` is a built route the static mount serves |
| `charts/htrflow-batch/README.md` | rewrite | "applied outside this chart (`kubectl apply`, …)"; "not their 0.4.0 successors" (twice); a mangled sentence in *Adopting hand-applied resources* ("and the / the git daemon … is gone entirely") | `apply` uses the Kubernetes client (`cluster.py`); the current names are the 0.6.0 ones; the paragraph is ungrammatical and mixes two subjects |
| `charts/htrflow-devstack/README.md` | rewrite | "applied with `kubectl apply`"; the private-key list names `status/attempts.json`, `status/validation.json`, `status/volumes.json`, `status/failures/*`; changelog stops at 0.1.1 while `Chart.yaml` says 0.2.0 | `apply` uses the Kubernetes client; `packages/wrapper` (`store.py`, `publish.py`, `logship.py`) writes exactly one `status/` key, `status/logs/<pipeline>/<volume>.txt` — the other four are reconciler-era and unwritten |
| `examples/campaigns/README.md` | keep | — | checked against `models.py`, `render.py` and `.github/workflows/render.yml`; byte-identical to `packages/converter/.../template/README.md` (asserted by `test_packaging.py`) |
| `frontend/README.md` | rewrite | "Two routes"; "Unlike the old reconciler-written status document"; phase list without `PartiallyFailed`; `JobSummary` sample without `warmup`; `JobDetail` sample without `latest`/`pipelineSteps`/`pipelineYaml`/`sourceUrl`; `reason` shown as a string; layout table without `alto.ts` / `routes/alto/`; "both routes" | `frontend/src/routes/` holds `+page.svelte`, `log/`, `alto/`; `projection.summarize` returns `warmup`, `projection.detail` returns `latest`/`pipelineSteps`/`pipelineYaml` and each row's `sourceUrl`; `projection._reason` returns `{stage, permanent, error}` |

### `docs/` — index and getting started

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `docs/index.md` | rewrite (small) | "frontend migration onto the new read API is tracked as its own follow-up" | the frontend reads `GET /api/v1/jobs` today (`frontend/src/lib/api.ts`); Tasks 20/26/28 finished it, adding the pipeline chip, `/alto` and the warm-up chip |
| `docs/getting-started/index.md` | keep | — | host gotchas only; nothing code-derived |
| `docs/getting-started/deploy.md` | rewrite | "`charts/htrflow-batch` (0.4.0)"; "applied with `kubectl` or Argo CD" | `Chart.yaml` says `version: 0.6.0`; `apply` uses the Kubernetes client |
| `docs/getting-started/run-a-volume.md` | rewrite (small) | The Job-contract snippet shows `suspend: true` as what the converter renders | `render._campaign_job` **pops** `spec.suspend` unless the campaign says `suspend: true`; the field an unpaused campaign carries is set by Kueue's webhook, not by the render |
| `docs/getting-started/campaigns.md` | rewrite (small) | "Add `--dry-run` to see the `kubectl` commands without running them; every command it does run is echoed to stderr" | `cli._apply` under `--dry-run` prints `would apply: <Kind>/<name>` and `(--dry-run: nothing was sent to the API server)`; a real apply prints `applied: <Kind>/<name>` on stdout |
| `docs/getting-started/viewing.md` | keep | — | `/alto` behaviour matches `frontend/src/routes/alto/+page.svelte` and `lib/alto.ts`; the chart-0.3.0/0.4.0 `defaultManifest` sentence is an explicit "was" note |

### `docs/how-it-works/`

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `architecture.md` | rewrite (small) | "Argo CD / kubectl apply" in the diagram and the sequence participant | `apply` is `htrflow-campaigns apply` over the Kubernetes client |
| `campaigns.md` | rewrite (small) | `devStack.rustfs.publicLogs` | the value lives in the devstack chart as `rustfs.publicLogs` (`charts/htrflow-devstack/values.yaml`); there is no `devStack.rustfs` key any more |
| `failure-handling.md` | keep | — | exit codes, `podFailurePolicy`, `terminationGracePeriodSeconds: 120`, `activeDeadlineSeconds`, the `PartiallyFailed`/warm-up text all match `campaign-job.yaml`, `warmup-job.yaml`, `main.py` and `projection.py`. The `MAX_SECONDS` row is a **justified** grep hit: the sentence says "from a pre-Task-25 wrapper" and `frontend/src/lib/reasons.ts` still maps it |
| `live-run-log.md` | keep | — | matches `logship.py` (4 MiB cap, 1 MiB head + 2 MiB tail, claim-at-start, `finish()` on every exit) and `projection._log_url` |
| `memory-budget.md` | rewrite (small) | "pod memory **request** … (`jobspec.py`…)" | there is no `jobspec.py`; the requests are in `packages/converter/src/htrflow_converter/manifests/campaign-job.yaml` |
| `wrapper.md` | rewrite | "`metrics-failed-latest.json` to the volume prefix"; "`PIPELINE_ID` … is part of the Job-name hash"; "Jobs … start `suspend: true`" | nothing writes `metrics-failed-latest.json` — `test_main.py` asserts its absence on all three failure paths; a campaign Job's name is the campaign file's stem (`render.campaign_objects`), there is no hash; see `run-a-volume.md` above for `suspend` |
| `decision-log.md` | keep as history + one superseded note | rows D7/D10/D13/D14/D15/D17/D18/D20 describe the reconciler CronJob and `status.json` | none of it exists on this branch; per the task brief this page keeps its content and gains a single "superseded by B63" line |

### `docs/reference/`

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `index.md` | keep | — | the three packages, the frontend and the chart are exactly what the tree holds |
| `configuration.md` | regenerate (never hand-edited) | the "results bucket is public-read" sentence lists `status/attempts.json`, `status/validation.json`, `status/volumes.json`, `status/failures/*` | pruned from the bucket policy in this pass; the page is generated by `scripts/config_reference.py` from `scripts/config_reference.md` + the models + `values.yaml`, and `test_chart_agreement.py` asserts equality — the sidecar is what was edited |
| `campaign-yaml.md` | keep | — | every rule checked against `models.py`, `parse.py`, `cli.py` and `test_parse.py`'s `EXPECTED`; the `allowed_image_repos` hits are the **justified** migration note ("are gone", "moved to the htrflow-batch chart") |
| `chart.md` | rewrite (small) | "applied with `kubectl` or Argo CD" | as above. Version already says 0.6.0 (fixed in Task 27); the `allowed_image_repos` hits are the **justified** "Where these two rules have lived" note |
| `frontend.md` | rewrite (small) | "the way a reconciler-written document could"; `viewer_url` shown as `<public_results_base>/<volume>/iiif.json` | there is no reconciler; `publish.py` builds `viewer_url` from `cfg.volume_prefix`, i.e. `<namespace>/<pipeline>/<volume>/iiif.json` |
| `s3-layout.md` | rewrite (small) | phase list without `PartiallyFailed`; summary without `warmup`; per-volume detail without `sourceUrl`, and no mention of `latest`/`pipelineSteps`/`pipelineYaml`; `devStack.rustfs.publicLogs` | `projection.summarize`/`detail`/`_phase`; `charts/htrflow-devstack/values.yaml` |
| `wrapper.md` | rewrite (small) | two source links point at `https://github.com/carpelan/test/…` | the repository is `AI-Riksarkivet/htrflow-batch` (every other link on the site uses it) |

### `docs/development/`

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `index.md` | rewrite (small) | "both Python packages" (twice) | `pyproject.toml` `members = ["packages/*"]` — three of them; `make typecheck` runs `ty` over wrapper, converter and web |
| `ci.md` | rewrite | "`campaigns-apply` … `kubectl apply` its `pipelines/` then `campaigns/`"; "`SKIP_FRONTEND=1` until B63 Task 7"; the Makefile-target list omits `e2e`, `install-kyverno`, `config-reference` | `cluster.apply_objects` is server-side apply through the Kubernetes client; `.github/workflows/ci.yml:48` runs `scripts/loc-budget.sh` with no `SKIP_FRONTEND`, and the script has no such switch; all three targets exist (`make -n` green) |
| `testing.md` | rewrite (small) | "Converter: parse (ids, allow-list, revisions, …)"; "Frontend … (still against the pre-B63 shape — Task 7 migrates this)" | the allow-list and revision rules left the converter in Task 22; the frontend tests are against the read-API shape (`frontend/src/lib/api.test.ts`, `reasons.test.ts`) |
| `security.md` | rewrite (small) | `devStack.rustfs.publicLogs`; "A handful of `status/attempts.json`-era key paths are still explicitly excluded … harmless dead entries" | `rustfs.publicLogs`; those entries were pruned in this pass, so the paragraph describes a state that no longer exists |
| `deployment.md` | rewrite (small) | "The chart (`charts/htrflow-batch`, version 0.4.0)" | `Chart.yaml`: 0.6.0 |
| `local-k3s.md` | rewrite | "It prints the wrapper and API digests"; "applies … with `kubectl`"; "there is no `status.json` left to do that job"; "`kubectl apply` is idempotent"; "a plain `kubectl apply` never deletes"; "`PRUNE=1` adds `--prune -l htrflow.riksarkivet.se/managed-by=converter`" (and that bullet is nested inside an unrelated one) | `poc-push` prints `wrapper:` and `web:`; `apply` is the Kubernetes client; nothing writes a status document; `--prune` is a flag of `htrflow-campaigns apply` that lists by `render.CAMPAIGN_SELECTOR` and deletes — it is not a `kubectl` flag pair |
| `e2e-indexed-jobs.md` | keep as history | — | a run log; only its links were checked (`local-k3s.md`, `../reference/campaign-yaml.md#pausing` — all resolve) |
| `test-log.md` | keep as history | — | out of scope by the brief |

### `docs/roadmap/`

| Page | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `open-items.md` | rewrite (small) | D13 "Priority lanes" is listed as **built** but D14's row still calls Kyverno "optional … `verifyImages`" | `security.policies.enabled` renders three enforcing ClusterPolicies (`charts/htrflow-batch/templates/policies/`), which are now the *only* enforcement of the allow-list and the revision rule |
| `evolution.md` | rewrite (heavy) | The whole page is reconciler-era: "What exists today: the GitOps reconciler"; `jobspec.build_job` / `status.job_name`; `status.json` (three times); `status/failures/…`, `metrics-failed-latest.json`, `attempts.json`; "the reconciler's tick becomes the controller's reconcile"; "`make warmup`"; "Purpose-built git-daemon image"; the viewer "in an nginx pod" | none of `packages/reconciler`, `jobspec.py`, `status.json`, `attempts.json`, `metrics-failed-latest.json`, the git daemon or the nginx viewer exists; `make warmup` is not a target (`make -n warmup` fails); the viewer is served by `packages/web` from the `htrflow-web` image |
| `phase-2-cache.md` | keep | — | still an unbuilt, evidence-gated plan (linked from `index.md`, `wrapper.md`, `testing.md`, `open-items.md`); the only code-facing number in it, "`activeDeadlineSeconds` is a 6 h backstop", matches `ConverterConfig.max_seconds = 21600` |

### Site configuration and source comments

| File | Verdict | Stale claim found | The code fact |
|---|---|---|---|
| `zensical.toml` | rewrite (small) | `site_url`, `repo_url`, `repo_name`, `site_author`, `copyright` and the social link all say `carpelan/test` | the canonical repository is `AI-Riksarkivet/htrflow-batch` (`.github/workflows/publish.yml`, every in-page link) |
| `frontend/src/lib/api.ts` | comment fix | "The read API boundary (packages/api …)"; "Unlike the old reconciler-written status document" | the read API is `packages/web` |
| `frontend/src/lib/config.ts` | comment fix | "there is no reconciler tick any more" | nothing to compare against any more; the sentence is about a component that never existed on this branch |
| `Makefile` | comment fix | "The failure-path steps (a 404 manifest, MAX_SECONDS, pause/resume, prune)" | the wrapper has no `MAX_SECONDS`; the E2E's budget step is the pod's `activeDeadlineSeconds` |
| `charts/htrflow-devstack/values.yaml` | comment fix + key prune | "applied with `kubectl`"; "attempts.json, validation.json, volumes.json, failures/* and warmup/* always need credentials" | `apply` uses the Kubernetes client; none of those keys is written by anything |
| `charts/htrflow-devstack/templates/_helpers.tpl` | key prune | `$private` lists four reconciler-era keys | `packages/wrapper` writes exactly one `status/` key |
| `scripts/compose_init.py` | key prune | `PRIVATE_STATUS_KEYS` mirrors the same four | same |
| `scripts/config_reference.md` | rewrite (small) | the public-read sentence lists the four pruned keys | same; this sidecar is the editable half of the generated `docs/reference/configuration.md` |

### Deleted

No page was deleted. Every page in scope still has a live subject:
`evolution.md` and `local-k3s.md` were the two candidates, and both were
rewritten instead — `evolution.md` because the *questions* it answers (a
submitting frontend, a campaign CRD, sharding, cohorts) are still open,
`local-k3s.md` because the PoC node it documents is still the deployment
target. `phase-2-cache.md` is still a plan, so it stays under the brief's
"keep only if still a plan".

## Justified grep exclusions

The acceptance greps are empty outside the history paths **except** for the
lines below, each of which names an old thing on purpose. Every one is a
"was"/"no longer" sentence, a back-compatibility branch, or a name that is
still correct.

| Pattern | Where | Why it stays |
|---|---|---|
| `reconciler` | `docs/reference/configuration.md` + `scripts/config_reference.md` ("It replaces the reconciler-era [hardcoded-value inventory], which stays as history") | a link to a page under `docs/audits/`, named as history |
| `reconciler` | `packages/wrapper/tests/test_dockerfile_workspace.py` (docstring) | the regression this test pins *is* "removing `packages/reconciler` (B63) left all three dockerfiles referencing it" — the name is the bug's identity |
| `reconciler` | `zensical.toml` nav entries for `B06-…-reconciler.md`, `B29-…-reconciler.md`, `B45-reconciler-image.md` | story filenames under `docs/features/**`, which the brief keeps as history; the nav must name the files that exist |
| `gitDaemon`, `htrflow-api`, `htr-api`, `uv4-viewer`, `RECONCILER_*` | `charts/htrflow-batch/README.md`, *Upgrading* and *Changelog* | both sections open with "Everything below this line is history: each entry names the objects and value keys **as they were at that version**". The `uv4-viewer` line is a `kubectl delete svc` command an operator on 0.3.0 must actually run |
| `status/warmup` | `charts/htrflow-devstack/README.md`, changelog 0.1.1 | the entry records that the key was *dropped*; nothing writes it |
| `attempts.json` | `docs/roadmap/evolution.md` — **removed**, no longer present | — |
| `airiksarkivet/` | `.docker/htrflow-batch.dockerfile`, `.dagger/build.go`, `.github/actions/build-htrflow-base-arm64/action.yml`, `renovate.json`, `docs/development/deployment.md`, `docs/development/local-k3s.md` | `airiksarkivet/htrflow` is the **current** upstream base image on Docker Hub — the grep was aimed at the retired `airiksarkivet/htrflow-batch` naming, which is gone |
| `htrflow-api` | `zensical.toml`, `B25-htrflow-api-pin-test.md` | "htrflow API" there is the htrflow *library* API the level-0 pin test guards, not the removed package |
| `MAX_SECONDS` | `docs/how-it-works/failure-handling.md` message table; `frontend/src/lib/reasons.ts` + `reasons.test.ts` | deliberate back-compatibility: a pod written by a pre-Task-25 wrapper still says `MAX_SECONDS`, and the card must name that failure correctly. Both sentences say "from a wrapper older than Task 25" |
| `MAX_SECONDS` | `packages/converter/tests/test_render.py`, `packages/wrapper/tests/test_config.py`, `scripts/loc-budget.sh` | two tests that assert the name is **absent**/ignored, and the budget comment recording why the watchdog left the wrapper |
| `allowed_image_repos`, `require_model_revision` | `packages/converter/src/htrflow_converter/models.py` (`_MOVED_TO_THE_CHART`) + its tests and fixtures; `docs/reference/campaign-yaml.md`, `docs/reference/chart.md`, `charts/htrflow-batch/README.md` | the converter's whole job with these keys now is to *reject* them with a sentence naming where the rule went; the docs are the matching migration notes |

## Verification

<!-- VERIFICATION -->
