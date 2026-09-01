# Campaigns as Indexed Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CronJob reconciler with plain Kubernetes Indexed Jobs rendered by a pure converter, a thin read API for the status page, and a chart that ships only production objects.

**Architecture:** `packages/converter` turns `campaigns/*.yaml` + `pipelines/*.yaml` into ConfigMaps + Indexed Jobs + warm-up Jobs (the reconciler's `parse.py`/`jobspec.py` logic moves here). The wrapper gains `MAX_SECONDS` and `IMAGES`, loses thumbnails and failure metrics. `packages/api` projects Job status for the status page. The chart drops reconciler/pipelines/devstack and adds the API. The reconciler package is deleted.

**Tech Stack:** Python 3.13 + uv workspace, pydantic, PyYAML, FastAPI + kubernetes client, pytest; Helm 3 + kubeconform; SvelteKit 5 + Vitest (bun); dagger (Go module in `.dagger/`).

**Spec:** `docs/superpowers/specs/2026-09-01-indexed-jobs-design.md` — decisions cited as D1–D12. Read it first.

## Global Constraints

- Budgets, non-test lines, enforced by `scripts/loc-budget.sh` (Task 1) and CI (Task 6): wrapper ≤ 1 500 · converter ≤ 400 · api ≤ 400 · frontend ≤ 2 500 TS/Svelte · chart ≤ 700 template lines. Python only in the batch system.
- Indexed Job contract (D1): `completionMode: Indexed`, `completions` = volumes, `parallelism` = window (default 20), `backoffLimitPerIndex: 3`, `maxFailedIndexes` = completions, `podFailurePolicy`: `Ignore` on `DisruptionTarget`, `FailIndex` on exit 13 (container `wrapper`), `ttlSecondsAfterFinished: 86400`, `restartPolicy: Never`, `suspend` unset (Kueue's webhook sets it), labels `kueue.x-k8s.io/queue-name`, `htrflow.riksarkivet.se/{campaign,pipeline,managed-by=converter}`, `app: htrflow-batch`; annotation `kueue.x-k8s.io/job-min-parallelism: "1"`; `kueue.x-k8s.io/priority-class` label when a campaign sets `priority`.
- Pod template = today's `jobspec.build_job` (security contexts, mounts, env, resources, runtimeClass/nodeSelector/tolerations) plus: volume `campaign` (ConfigMap `campaign-<name>`) at `/campaign` ro; `command: ["/bin/sh","-c"]` + args from Task 2; init container `warmup-wait` (same image, `sh -c 'until [ -f /data/warmup/<pipeline>.done ]; do sleep 10; done'`, `/data` ro, no GPU, same security context).
- Volume list format (D2): `volumes.txt`, line = `<id>\t<manifest-url>` or `<id>\timages:<url>,<url>`; ≤ 10 000 lines per Job, split `-part1…`; campaigns are append-only.
- Wrapper env/exit contract unchanged except: new `MAX_SECONDS` (0 = none; on expiry: termination log `{"permanent": false, "error": "MAX_SECONDS"}`, exit 1), new `IMAGES` (comma-separated http(s) URLs; mutually exclusive with `IIIF_MANIFEST_URL`; the wrapper builds the P3 manifest with `build_manifest` and publishes it to `sources/<pipeline>/<volume>/manifest.json` before processing), `S3_PREFIX` honoured as today; warm-up writes `/data/warmup/<pipeline>.done` on success.
- Read API (D8): `GET /api/v1/jobs`, `GET /api/v1/jobs/{ns}/{name}?offset=0&limit=200`, `GET /healthz`; read-only; phases `Queued|Paused|Running|Succeeded|Failed`.
- S3 layout (D9): `S3_PREFIX=<namespace>/` unless converter config `legacyLayout: true`.
- Nothing here lists or deletes S3 objects (D10).
- Commit messages end with `(B63)`; no Co-Authored-By trailer. Work in the worktree `.worktrees/b63-indexed` (branch `b63-indexed` off `org/main`).

---

## File structure

```
packages/converter/
  pyproject.toml                       # ra-htrflow-converter? no: name "htrflow-converter", script htrflow-campaigns
  src/htrflow_converter/__init__.py
  src/htrflow_converter/models.py      # Campaign, Volume, Pipeline, ConverterConfig (pydantic)  [from reconciler models.py + parse.py]
  src/htrflow_converter/parse.py       # load + validate campaigns/, pipelines/, converter.yaml   [from reconciler parse.py]
  src/htrflow_converter/render.py      # ConfigMaps, Indexed Job, warm-up Job                     [from reconciler jobspec.py]
  src/htrflow_converter/cli.py         # htrflow-campaigns validate|render
  tests/{test_parse.py,test_render.py,test_cli.py, fixtures/…, golden/…}
packages/api/
  pyproject.toml                       # name "htrflow-api", script htrflow-api
  src/htrflow_api/{__init__.py, app.py, projection.py, kube.py}
  tests/{test_projection.py, test_app.py}
packages/wrapper/src/htrflow_batch/{config.py, main.py, warmup.py, synthetic.py(new, moved), viewer.py}
charts/htrflow-batch/templates/api.yaml (new); reconciler.yaml, pipelines.yaml, job-example.yaml, devstack-*.yaml (deleted)
charts/htrflow-devstack/               (new chart; moved templates + values)
scripts/loc-budget.sh
examples/campaigns/{converter.yaml, campaigns/demo.yaml, pipelines/demo-v1.yaml, .github/workflows/render.yml}
.docker/htrflow-api.dockerfile (new); .docker/htrflow-reconciler.dockerfile (deleted)
```

---

### Task 1: Converter package — models, parsing, validation, budgets

**Files:**
- Create: `packages/converter/pyproject.toml`, `src/htrflow_converter/{__init__,models,parse,cli}.py`, `tests/test_parse.py`, `tests/test_cli.py`, `tests/fixtures/good/{campaigns/kyrk.yaml,campaigns/loc.yaml,pipelines/demo-v1.yaml,converter.yaml}`, `tests/fixtures/bad/…` (one file per rule), `scripts/loc-budget.sh`
- Modify: `pyproject.toml` (root: `testpaths` += `packages/converter/tests`; `[tool.uv.sources]` if the repo uses them), `uv.lock`
- Reference (read, then port — do not import from it): `packages/reconciler/src/htrflow_reconciler/{parse.py,models.py}`

**Interfaces:**
- Produces: `models.Volume{id: str; manifest: str | None; images: list[str]}` with `.source_line() -> str` (`"<id>\t<manifest>"` or `"<id>\timages:<a>,<b>"`); `models.Campaign{name; pipeline; volumes: list[Volume]; priority: str = ""; window: int | None}`; `models.Pipeline{id; image; steps: list[dict]; model_revision: str = ""}` with `.pipeline_yaml() -> str` (`yaml.safe_dump({"steps": steps}, sort_keys=False)`) and `.sha256`; `models.ConverterConfig{namespace="htr-batch"; queue="htr-batch"; window=20; s3_secret="htr-batch-s3"; data_pvc="htr-test-data"; runtime_class="nvidia"; node_selector: dict={}; tolerations: list=[]; public_results_base: str; legacy_layout=False; source_template="https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"; max_seconds=21600; manifest_max_bytes=16 MiB; fetch_max_bytes=64 MiB; allowed_image_repos: list[str]=[]}`; `parse.load(campaigns_dir, pipelines_dir, config_path) -> tuple[list[Campaign], dict[str, Pipeline], ConverterConfig]` raising `ValidationError(list[str])` with every problem (not just the first); CLI `htrflow-campaigns validate <repo-dir>` exit 0/1 printing problems one per line.
- Rules (port verbatim from `parse.py` + spec §3): pipeline referenced exists; image matches `^[a-z0-9./:-]+@sha256:[0-9a-f]{64}$` and, if `allowed_image_repos` set, starts with one of them; volume id `^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$`, unique per campaign; bare string → `manifest = source_template.format(ref=id)`; `manifest` xor non-empty `images`; every URL http(s) with netloc; campaign name is the file stem and must match the pipeline-id pattern; `model_revision` if set `^[0-9a-f]{40}$` and `require_model_revision` handled as today; more than 10 000 volumes is allowed here (split happens in render).

- [ ] **Step 1: Budget script** (`scripts/loc-budget.sh`, executable):

```bash
#!/usr/bin/env bash
# Non-test line budgets from the spec (§1). Fails the build when exceeded.
set -euo pipefail
cd "$(dirname "$0")/.."
count() { find "$1" -type f \( "${@:2}" \) -not -path '*/tests/*' -not -name '*.test.ts' -not -path '*/node_modules/*' -print0 | xargs -0 cat 2>/dev/null | wc -l; }
check() { local name=$1 got=$2 max=$3; printf '%-10s %6d / %d\n' "$name" "$got" "$max"; [ "$got" -le "$max" ] || { echo "::error::$name over budget ($got > $max)"; fail=1; }; }
fail=0
check wrapper   "$(count packages/wrapper/src -name '*.py')" 1500
check converter "$(count packages/converter/src -name '*.py')" 400
check api       "$(count packages/api/src -name '*.py')" 400
check frontend  "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 2500
check chart     "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 700
exit $fail
```
Run it: wrapper/frontend/chart rows are over budget today — expected; it is wired into CI in Task 6 when all rows pass.

- [ ] **Step 2: Failing tests** — `test_parse.py`: good fixture loads (2 campaigns, 1 pipeline; bare id expands with the template; `images:` volume kept); each bad fixture yields the expected message substring (`unsafe volume id`, `duplicate volume id`, `must be an http(s) URL`, `unknown pipeline`, `image must be digest-pinned`, `needs manifest or images`); errors are collected, not raised on first; `Volume.source_line()` for both shapes. `test_cli.py`: `validate` on good → exit 0, on bad → exit 1 with the problems on stdout. Run `uv run pytest packages/converter -q` → FAIL (package missing).
- [ ] **Step 3: Implement** (`uv add --package htrflow-converter pydantic pyyaml typer`; keep `typer` optional — `argparse` is fine and lighter, prefer argparse). Port `parse.py` logic; collect errors in a list.
- [ ] **Step 4: PASS**; `uv run ruff format . && uv run ruff check . && uv run ty check packages/converter`; `scripts/loc-budget.sh` converter row.
- [ ] **Step 5: Commit** `feat(converter): campaign/pipeline parsing and validation, budget script (B63)`

---

### Task 2: Converter render — ConfigMaps, Indexed Job, warm-up Job, split, append-only

**Files:**
- Create: `packages/converter/src/htrflow_converter/render.py`, `tests/test_render.py`, `tests/golden/{demo-v1.pipeline.yaml,kyrk.job.yaml,kyrk.configmap.yaml}`
- Modify: `cli.py` (`render <repo-dir> --out <dir>` writes one file per object: `rendered/pipelines/<id>.yaml` (ConfigMap + warm-up Job), `rendered/campaigns/<name>[-partN].yaml` (ConfigMap + Job)); `render` refuses to change an existing `rendered/campaigns/<name>*.yaml` whose ConfigMap `volumes.txt` differs ("campaign <name> is append-only: create a new campaign")
- Reference: `packages/reconciler/src/htrflow_reconciler/jobspec.py` (port `build_job`, `build_warmup_job`, security contexts, env, mounts, `_pod_failure_policy`, `label_value`)

**Interfaces:**
- Produces: `render.pipeline_objects(p: Pipeline, cfg) -> list[dict]` (ConfigMap `htr-pipeline-<id>` with annotation `htrflow.riksarkivet.se/pipeline-sha256`, warm-up Job `htr-warmup-<id>`), `render.campaign_objects(c: Campaign, p: Pipeline, cfg) -> list[dict]` (for each part: ConfigMap `campaign-<name>[-partN]` with `data["volumes.txt"]`, Job `<name>[-partN]`), `render.split(volumes, 10_000)`.
- Job spec differences from `build_job`: name = campaign (label-sanitised); `completionMode/completions/parallelism/backoffLimitPerIndex/maxFailedIndexes/ttlSecondsAfterFinished` per Global Constraints; `podFailurePolicy` second rule action `FailIndex`; no `suspend`; annotation `kueue.x-k8s.io/job-min-parallelism: "1"`; env `MAX_SECONDS`, `MANIFEST_MAX_BYTES`, `FETCH_MAX_BYTES`, `S3_PREFIX` (`""` if legacy else `f"{cfg.namespace}/"`), `PUBLIC_RESULTS_BASE`, `IMAGE_DIGEST`, `PIPELINE_ID`, `PIPELINE_PATH`; **no** `VOLUME_REF`/`IIIF_MANIFEST_URL` env (set by the shell); container `command: ["/bin/sh","-c"]`, `args`:

```sh
set -eu
line=$(sed -n "$((JOB_COMPLETION_INDEX + 1))p" /campaign/volumes.txt)
[ -n "$line" ] || { echo "no volume for index $JOB_COMPLETION_INDEX" >&2; exit 13; }
id=${line%%	*}; src=${line#*	}
export VOLUME_REF="$id"
case "$src" in images:*) export IMAGES="${src#images:}" ;; *) export IIIF_MANIFEST_URL="$src" ;; esac
exec python -m htrflow_batch
```
(a real TAB inside `${line%%	*}`; write it with `\t` in Python and assert the golden file contains the tab); init container `warmup-wait` per Global Constraints; volume `campaign` from the ConfigMap.

- [ ] **Step 1: Failing tests** — `test_render.py`: golden comparison of the three files for the `kyrk` fixture (3 volumes incl. one `images:`); Job fields per Global Constraints asserted explicitly (not only golden); `split` of 10 001 volumes → two Jobs `kyrk-part1` (10 000) and `kyrk-part2` (1) with their own ConfigMaps; `legacy_layout` flips `S3_PREFIX`; `priority` adds the label; the `args` string contains a TAB and `exec python -m htrflow_batch`; init container present with the pipeline's marker path; `kubeconform -strict` passes on the rendered files (skip the test if `kubeconform` is not on PATH, but CI has it — `.dagger/checks.go` uses it). `test_cli.py`: `render` writes the expected file names; second `render` with an added volume to an existing campaign exits 1 with the append-only message; a new campaign renders fine.
- [ ] **Step 2: FAIL.** - [ ] **Step 3: Implement** (dict-building like `jobspec.py`; `yaml.safe_dump_all` per file). - [ ] **Step 4: PASS**, ruff/ty, budget (converter ≤ 400 — if over, drop docstrings carried from the Python, do not raise). - [ ] **Step 5: Commit** `feat(converter): render pipelines and campaigns as ConfigMaps, warm-up Jobs and Indexed Jobs (B63)`

---

### Task 3: Wrapper — `MAX_SECONDS`, `IMAGES`, warm-up marker; drop thumbnails and failure metrics

**Files:**
- Modify: `packages/wrapper/src/htrflow_batch/config.py` (`max_seconds: int = 0`, `images: str = ""`; `manifest_url` optional when `images` set — validation: exactly one), `main.py` (timer via `threading.Timer` or the existing `RunState`/signal path: on expiry write the termination log `{"permanent": false, "error": "MAX_SECONDS", "stage": <stage>}`, ship the log, `_hard_exit(1)`; `IMAGES` → `build_manifest(volume, urls, f"{public_base}/{prefix}sources/{pipeline}/{volume}/manifest.json")`, `store.put_json("sources/<pipeline>/<volume>/manifest.json", …)`, then proceed with that manifest dict as if fetched; delete `make_thumbnail`, `previous_thumbnail`, `publish_failure_metrics`, `_publish_failure`, `thumb_box`, the `"thumbnail"` manifest key), `viewer.py` (remove `_thumbnail`), `warmup.py` (after success `Path("/data/warmup").mkdir(exist_ok=True); Path(f"/data/warmup/{pipeline_id}.done").touch()`), `packages/wrapper/pyproject.toml` (drop `pillow` if unused: `uv tree --package htrflow-batch-wrapper | grep -i pillow`)
- Create: `packages/wrapper/src/htrflow_batch/synthetic.py` (moved `build_manifest` from the reconciler, unchanged)
- Test: `packages/wrapper/tests/test_main.py` (delete thumbnail/failure-metrics cases; add: `IMAGES` run publishes the synthetic manifest to `sources/…` and processes both pages via the mocked fetcher; `MAX_SECONDS=1` with a slow fake driver exits 1 with the expected termination JSON; `manifest.json` has no `thumbnail` key; a failed run writes no `metrics-failed-latest.json`), `test_viewer.py`, `test_warmup.py` (marker file written), `test_config.py` (`IMAGES` xor `IIIF_MANIFEST_URL`)
- Docs: `docs/reference/wrapper.md` (env table: add `MAX_SECONDS`, `IMAGES`; remove thumbnail/metrics rows), `docs/reference/s3-layout.md` (remove `thumbnail`, `metrics-failed-latest.json`; `sources/` now written by the wrapper)

- [ ] **Step 1: Failing tests** (run `uv run pytest packages/wrapper -q`). - [ ] **Step 2: Implement.** - [ ] **Step 3: PASS**, ruff/ty, budget (wrapper ≤ 1 500 — today 1 833; the deletions remove ~150 lines; if still over, `logship.py` (229) is **not** to be touched (D11) — trim docstrings in `main.py`/`fetch.py` instead and report the count). - [ ] **Step 4: Commit** `feat(wrapper): MAX_SECONDS and IMAGES; warm-up marker; drop thumbnails and failure metrics (B63)`

---

### Task 4: Read API

**Files:**
- Create: `packages/api/pyproject.toml` (deps `fastapi`, `uvicorn`, `kubernetes`), `src/htrflow_api/{__init__,app,projection,kube}.py`, `tests/{test_projection.py,test_app.py}`, `.docker/htrflow-api.dockerfile` (uv, non-root 1000, `CMD ["htrflow-api"]`, port 8081)
- Modify: root `pyproject.toml` `testpaths`

**Interfaces:**
- `projection.summarize(job: dict, cfg) -> JobSummary` and `projection.detail(job: dict, configmap: dict, pods: list[dict], cfg, offset, limit) -> JobDetail` (pure functions over Kubernetes API dicts — this is what the tests target); `kube.Reader` (list Jobs with label `htrflow.riksarkivet.se/managed-by=converter` across the namespaces in `HTRFLOW_NAMESPACES` env, get ConfigMap, list Pods by `batch.kubernetes.io/job-name`); `app.create_app(reader) -> FastAPI`.
- `JobSummary = {namespace, name, pipeline, phase, counts: {total, active, done, failed}, suspended, createdAt, resultsBase}` where `total = spec.completions`, `done` = count of `status.completedIndexes` ranges, `failed` = count of `status.failedIndexes`, `active = status.active`; phase: Job condition `Complete` → `Succeeded`; `Failed` → `Failed`; `spec.suspend and done == 0` → `Queued`; `spec.suspend` → `Paused`; else `Running`. `resultsBase = f"{cfg.public_results_base}/{'' if cfg.legacy_layout else ns + '/'}{pipeline}"`.
- `JobDetail = JobSummary + {failures: [...], volumes: [{index, id, state: pending|active|done|failed, manifestUrl, iiifUrl, altoPrefix, logKey, reason?}]}` — `state` from the index sets; `reason` = the terminated container message of the newest pod for that index (`batch.kubernetes.io/job-completion-index` label) when present; paging by index.
- Index-range parsing: `"0-2,5,7-9"` → set; write it once, test it (`""` → empty).

- [ ] **Step 1: Failing tests** — fixtures: a Job dict with `completedIndexes: "0-2,5"`, `failedIndexes: "3"`, `active: 1`, `suspend: false`; a ConfigMap with 7 lines; two pods (index 4 active; index 3 terminated with message `{"permanent": true, "error": "manifest unsupported"}`). Assert counts {7,1,4,1}, phase Running, per-volume states, reason for index 3, paging `offset=5&limit=2`, phases Queued/Paused/Succeeded/Failed for four more Job dicts; `test_app.py` with a fake reader: 200 shapes, 404 unknown, 405 on POST, `/healthz`.
- [ ] **Step 2: FAIL.** - [ ] **Step 3: Implement.** - [ ] **Step 4: PASS**, ruff/ty, budget (api ≤ 400). - [ ] **Step 5: Commit** `feat(api): read-only /api/v1/jobs projection of Indexed Jobs for the status page (B63)`

---

### Task 5: Chart 0.3.0 and the devstack chart

**Files:**
- Create: `charts/htrflow-batch/templates/api.yaml` (Deployment 1 replica, image `api.image` digest-pinned, SA, Role+RoleBinding read-only `jobs`, `pods`, `configmaps` (get/list/watch) in the release namespace, Service `htrflow-api:8081`, restricted pod security like the viewer, NetworkPolicy: ingress from viewer pods only, egress to API server `network.apiServer.cidr` + DNS), `charts/htrflow-devstack/{Chart.yaml,values.yaml,templates/_helpers.tpl,templates/{rustfs,registry,nvidia,gitdaemon}.yaml,README.md}` (moved from `devstack-*.yaml`, values = today's `devStack.*` subtree)
- Delete: `templates/reconciler.yaml`, `templates/pipelines.yaml`, `templates/job-example.yaml`, `templates/devstack-*.yaml`
- Modify: `values.yaml` + `values.schema.json` (remove `reconciler`, `pipelines`, `exampleJob`, `devStack` except `devStack.allowTagImages` → `security.allowTagImages`; add `api.{image,resources}`; keep `queue`, `modelCache`, `security`, `network`, `viewer`, `publicResultsBase`), `templates/viewer.yaml` (nginx `location /api/ { proxy_pass http://htrflow-api:8081/api/; }`; `config.js` sets `API_BASE`), `templates/network.yaml` (remove reconciler rules; batch pods unchanged; ConfigMap/campaign volumes need no policy), `templates/validate.yaml`, `templates/NOTES.txt`, `Chart.yaml` (0.3.0), `charts/htrflow-batch/README.md` (0.3.0 upgrade notes: reconciler removed, campaigns rendered by the converter, `legacyLayout`), `.dagger/checks.go` (`CheckChart` also lints/templates `charts/htrflow-devstack`; assertions: no `kind: CronJob`, `htrflow-api` Deployment present, no object labelled devstack in the prod chart), `Makefile` (`install-devstack`)

- [ ] **Step 1: Failing render assertions** in `.dagger/checks.go` (run `dagger call check-chart --source=.`). - [ ] **Step 2: Implement** the chart changes. - [ ] **Step 3: `helm template htr charts/htrflow-batch --set publicResultsBase=https://x/ --set network.apiServer.cidr=10.16.51.10/32 --set api.image=docker.io/riksarkivet/htrflow-api@sha256:<64 zeros>`** renders: Kueue ×3, NetworkPolicies, viewer, API objects, model-cache PVC — no CronJob, no pipelines ConfigMap; `helm template charts/htrflow-devstack` renders the four PoC objects; kubeconform passes; `scripts/loc-budget.sh` chart ≤ 700. - [ ] **Step 4: `dagger call check-chart` PASS.** - [ ] **Step 5: Commit** `feat(chart): 0.3.0 — read API in, reconciler/pipelines/exampleJob out, devstack in its own chart (B63)`

---

### Task 6: Delete the reconciler; CI, publish matrix, Makefile, campaigns example

**Files:**
- Delete: `packages/reconciler/`, `.docker/htrflow-reconciler.dockerfile`, `docs/reference/reconciler.md`
- Modify: root `pyproject.toml` + `uv.lock` (workspace member gone; `testpaths` = wrapper, converter, api), `.dagger/build.go` (`BuildApi` replaces `BuildReconciler`; `BuildConverter` not needed — the converter is a pure Python package published to the wrapper image? **No**: the converter runs in CI/laptops, install with `uvx --from git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter htrflow-campaigns`; document that), `.dagger/publish.go` (`case "api"`, repository `riksarkivet/htrflow-api`; delete `reconciler`), `.github/workflows/publish.yml` (matrix `api` replaces `reconciler`), `.github/workflows/ci.yml` (+ `scripts/loc-budget.sh` step, converter/api tests already via pytest), `Makefile` (`campaigns-apply DIR=…` = `uv run htrflow-campaigns render $(DIR) --out $(DIR)/rendered && kubectl apply -f $(DIR)/rendered/pipelines -f $(DIR)/rendered/campaigns`; remove `poc-push` reconciler parts; `build-api`), `renovate.json` (nothing reconciler-specific? check), `docs/development/{deployment,testing,ci}.md` (reconciler → converter/api), `docs/reference/campaign-yaml.md` (converter config section, `rendered/`, append-only rule, `priority`/`window` per campaign), `docs/how-it-works/campaigns.md` (rewrite: Indexed Job lifecycle, the two bold GitOps rules), `docs/index.md`, `zensical.toml` nav
- Create: `examples/campaigns/{README.md,converter.yaml,campaigns/demo.yaml,pipelines/demo-v1.yaml,.github/workflows/render.yml}` — the shape of the campaigns repo: a workflow that runs `validate` on PRs and `render` + commit of `rendered/` on `main` (Azure Pipelines variant as a comment block; I15 creates the real repo)
- Test: `grep -rn reconciler --include=*.py --include=*.go --include=*.yaml --include=*.toml --include=*.md . | grep -vE 'docs/(audits|superpowers|features)|CHANGELOG'` returns nothing; `uv sync --frozen && uv run pytest -q` green; `dagger call checks` green; `uvx zensical build --clean` clean; `scripts/loc-budget.sh` passes for wrapper/converter/api/chart (frontend row: allow CI to skip it via `SKIP_FRONTEND=1` until Task 7 — remove in Task 7).

- [ ] **Step 1: grep → non-empty.** - [ ] **Step 2: delete, rewire, rewrite docs.** - [ ] **Step 3: green.** - [ ] **Step 4: Commit** `refactor: remove the CronJob reconciler; converter + read API replace it in CI, publish and docs (B63)`

---

### Task 7: Frontend reads the read API

**Files:**
- Create: `frontend/src/lib/api.ts` (Zod `JobSummary`, `JobDetail`, `VolumeView` matching Task 4; `fetchJobs()`, `fetchJob(ns, name, offset=0, limit=200)`; `ApiUnreachable` on non-2xx), `frontend/src/lib/api.test.ts`
- Delete: `frontend/src/lib/{status.ts,status.test.ts,derive.ts,derive.test.ts}`
- Modify: `config.ts` (`resolveApiBase()` from `window.API_BASE` / `VITE_API_BASE`, default `/api/v1`), `routes/+page.svelte` (list from `fetchJobs()`, banner "controller unreachable" → "API unreachable" on error, no STALE-by-age), `components/CampaignCard.svelte` (prop `job: JobSummary`; volumes via `fetchJob` paged; no thumbnails), `PagesTable.svelte` (rows = `VolumeView`), tests, `frontend/README.md`, `frontend/static/config.js`
- Test: `bun run test && bun run build`; `scripts/loc-budget.sh` frontend ≤ 2 500 (candidates if over: `routes/log/+page.svelte` 507 → move parsing into `runlog.ts`); remove `SKIP_FRONTEND` from CI.

- [ ] **Step 1: `api.test.ts` failing.** - [ ] **Step 2: Implement + delete.** - [ ] **Step 3: green + budget.** - [ ] **Step 4: Commit** `feat(frontend): read campaigns from /api/v1/jobs; drop status.json derivation (B63, C08)`

---

### Task 8: E2E on the PoC and close-out

**Files:** `docs/development/e2e-indexed-jobs.md` (run log), `Makefile` (`e2e` target for the reproducible parts), `.env`

- [ ] **Step 1: Preconditions** — `kubectl version` on the PoC (server ≥ 1.29; `backoffLimitPerIndex` requires ≥ 1.29 — record the version); `kubectl -n htr-batch get jobs` shows nothing running; `kubectl -n htr-batch delete cronjob htr-reconciler`.
- [ ] **Step 2: Publish** — bump `packages/wrapper/pyproject.toml` to `0.2.0`, `uv lock`, push, `gh workflow run publish.yml -f tag=v0.2.0` → wrapper, viewer, api digests; put them in the chart defaults / PoC values.
- [ ] **Step 3: Install** — `make install-devstack` (unchanged PoC deps), `make install` chart 0.3.0 with `legacyLayout=true`; `kubectl -n htr-batch get deploy htrflow-api` Ready; `curl <viewer>/api/v1/jobs` → `[]`.
- [ ] **Step 4: Campaigns** — convert the PoC campaigns repo layout (`converter.yaml` with the PoC values), `make campaigns-apply DIR=…`: warm-up Job completes and writes the marker; a 50-volume campaign runs; `kubectl get job` shows `completedIndexes` growing; viewer opens volumes from the status page; `aws s3 ls …/status/` shows no new `status.json`.
- [ ] **Step 5: Failure paths** — a campaign with one bad manifest URL → that index in `failedIndexes`, others complete; `MAX_SECONDS=60` campaign → index retried 3× then failed; `suspend: true` applied mid-run → active pods gone, done indexes kept, resume continues; delete the campaign file + apply → Job pruned, S3 untouched; Kueue partial admission with `nominalQuota` 1 GPU and `parallelism: 4` → admitted with 1.
- [ ] **Step 6: Record** in `docs/development/e2e-indexed-jobs.md`; update B63's story `Klart när` boxes; `python3 scripts/stories/azure_sync.py story …`; link the merge commit on #2978 (`Custom.Commits` + Hyperlink). Do not set state Done. Commit `docs: Indexed Jobs E2E on the PoC — resume, pause, partial admission, no status files (B63)`.

### Task 9: Skeleton manifests instead of dict builders

**Why:** `render.py` builds every Kubernetes object as nested dicts (`_container`, `_pod_template`, `_job`, `_configmap`, `_campaign_job`, `_warmup_job`); nobody can read the Job off the Python. Ship the skeletons as real YAML and keep only the field patching in Python. Output must stay byte-identical (the golden tests are the acceptance).

**Files:**
- Create: `packages/converter/src/htrflow_converter/manifests/campaign-job.yaml`, `warmup-job.yaml` (complete, valid `batch/v1` Jobs with every static field — securityContexts, `restartPolicy: Never`, `automountServiceAccountToken: false`, `podFailurePolicy`, `backoffLimitPerIndex: 3`, `ttlSecondsAfterFinished: 86400`, `completionMode: Indexed`, the `campaign`/`pipeline`/`data`/`work`/`s3` volumes and mounts, the wrapper env list, the `/campaign/volumes.txt` shell args as a block scalar — and placeholder values such as `name: CAMPAIGN` for the fields Python sets), `configmap.yaml`
- Modify: `render.py` — `_load(name) -> dict` (`yaml.safe_load` of the packaged file via `importlib.resources`, `copy.deepcopy` per render), `_set(obj, "spec.template.spec.containers[0].image", value)` dotted-path helper (≤ 15 lines), and `_campaign_job`/`_warmup_job`/`_configmap` become "load skeleton, set dynamic fields" (name, namespace, labels, annotations, `completions`, `parallelism`, `maxFailedIndexes`, image, ConfigMap names, `claimName`, `secretName`, queue/priority labels, `runtimeClassName`, `nodeSelector`, `tolerations`, `MAX_SECONDS`/pipeline env). Delete `_container`, `_pod_template`, `_job`, `_resources`, `_workdir_env`, `_s3_env`, `_pod_failure_policy`, `_POD_SEC`, `_CTR_SEC`, `_SHELL_ARGS` once nothing uses them.
- Modify: `packages/converter/pyproject.toml` (`[tool.hatch.build.targets.wheel]` includes `manifests/*.yaml`), `docs/reference/campaign-yaml.md` (one paragraph: the skeletons are the Job; point at the files), `.dagger/checks.go` kubeconform step also validates the two skeleton files as-is.
- Test: `packages/converter/tests/test_render.py` — add `test_skeletons_are_valid_jobs` (load each skeleton, assert `kind`, `apiVersion`, `spec.completionMode == "Indexed"` for the campaign one); `test_set_dotted_path` (nested dict + list index); existing golden tests unchanged and green.

- [ ] **Step 1: Failing tests** — the two new tests (`_load`/`_set` do not exist yet). Run `uv run pytest packages/converter -q`; expect ImportError/AttributeError.
- [ ] **Step 2: Skeletons** — write the three YAML files by dumping today's output for the golden fixture (`htrflow-campaigns render tests/golden/... `) and replacing the dynamic values with placeholders; keep key order identical to today's dict order so the golden YAML stays byte-identical.
- [ ] **Step 3: Rewrite** `render.py` onto `_load`/`_set`; delete the dict builders. Run `uv run pytest packages/converter -q` — all green incl. golden; `scripts/loc-budget.sh` converter ≤ 800 (expect a drop of ~80–100 lines).
- [ ] **Step 4: kubeconform** — `dagger call checks` green with the skeleton files validated.
- [ ] **Step 5: Commit** `refactor(converter): ship Job skeletons as YAML, patch fields in Python (B63)`.

### Task 10: Pydantic-native parsing

**Why:** `parse.py` (231 lines) hand-validates campaign/pipeline/volume shapes while `models.py` is already pydantic but rule-free — two validation mechanisms, one of them (the `_load_config` → `model_validate` → flatten `e.errors()` pattern) already the clean one. Move the rules onto the models; `parse.py` keeps only file I/O, error flattening and the cross-file check. Behaviour-preserving: the 16 tests in `packages/converter/tests/test_parse.py` are the acceptance and must pass unchanged (they assert on message substrings and on "collect every problem, do not stop at the first").

**Files:**
- Modify: `packages/converter/src/htrflow_converter/models.py` — `Volume`: `field_validator("id")` raising `ValueError(f"unsafe volume id: {v!r}")`; `manifest`/`images` http(s)-only validators; `model_validator(mode="after")` "volume needs manifest or images" (xor); a `model_validator(mode="before")` that turns a bare string entry into `{"id": s}` (the `source_template` expansion happens in the same before-validator using `info.context["source_template"]`). `Campaign`: `name` validator (`_NAME_RE`, message `unsafe campaign name`), `pipeline` non-empty (`campaign needs pipeline:`), `window: int | None = Field(None, ge=1)` (message must contain `window`), `model_validator(mode="after")` for `duplicate volume id: <id>`. `Pipeline`: `id` validator (`unsafe pipeline id`), `image` digest-pinned (`image must be digest-pinned`) and `not under an allowed repository` via `info.context["allowed_image_repos"]`, `steps: list[dict]` (`missing steps`), `model_revision` 40-hex (`model_revision must be 40 hex chars`), `model_validator(mode="after")` running the per-step `needs a 40-hex revision (require_model_revision)` check when `info.context["require_model_revision"]`. Keep `source_line()`, `pipeline_yaml()`, `sha256`, `ConverterConfig` as they are.
- Modify: `packages/converter/src/htrflow_converter/parse.py` — keep `ValidationError`, `_read_yaml_mapping`, `_load_config`; add one `_problems(stem, exc) -> list[str]` that flattens a `pydantic.ValidationError` to `f"{stem}: {loc}: {msg}"` with the `Value error, ` prefix stripped (so substrings such as `unsafe volume id` survive) and no newlines; `_parse_campaign`/`_parse_pipeline` become read-mapping + `Model.model_validate({"name"|"id": path.stem, **doc}, context={...})` inside a try; delete `_fail`, `_safe_name`, `_repo_allowed`, `_check_revisions`, `_http_url`, `_volume` and the regexes that move to `models.py`. `load()` keeps the unknown-pipeline cross-file check.
- Test: `packages/converter/tests/test_parse.py` unchanged and green; `test_render.py` + golden unchanged; `packages/converter/tests/test_models.py` (new, small): bare-string volume expands with the template from context; `window=0` rejected with a message containing `window`; duplicate ids rejected.

- [ ] **Step 1: Failing tests** — write `test_models.py` first; run `uv run pytest packages/converter -q`; expect failures (models carry no rules yet).
- [ ] **Step 2: Move the rules** onto the models (validators with the exact message substrings above); run `test_models.py` green.
- [ ] **Step 3: Slim `parse.py`** onto `model_validate` + `_problems`; delete the hand-rolled helpers. Run `uv run pytest packages/converter -q` — all green incl. `test_parse.py` untouched and golden; `scripts/loc-budget.sh` converter ≤ 800 (expect roughly −90 net).
- [ ] **Step 4: Commit** `refactor(converter): validate campaign and pipeline YAML with pydantic, not by hand (B63)`.

### Task 11: Wrapper structure — stages and a streaming generator (zero behaviour change)

**Why:** `packages/wrapper/src/htrflow_batch/main.py::_main` is one 210-line function running five stages inline, with hand-built concurrency plumbing (downloader thread + `queue.Queue` + `Semaphore` + `None` sentinel + `bytes_box` + `stop` event) shared between `main.py`, `fetch.run_downloader` and `stream.consume`. Morgan: "I dont want to loose any functionality" — this task changes structure only. Every existing wrapper test (`packages/wrapper/tests`, 156 tests) must pass; where a test drives the old queue API directly (`test_stream.py`, parts of `test_fetch.py`/`test_main.py`) it may be adapted to the new API, but every behavioural assertion in it (lookahead backpressure, W6 `UploadOutage` after `MAX_UPLOAD_FAILURES`, W9 model-load stage, W10 stop-on-failure, `stall_seconds`, image cleanup, skipped/failed/ok outcomes, resume with changed sources, verify gate, publish order with `manifest.json` last, `MAX_SECONDS` race, SIGTERM 143, termination-log redaction) must survive unchanged in meaning.

**Files:**
- Modify: `main.py` — `_main` becomes ≤ 40 lines: `cfg/store/capture/timer` setup, then `pages = _setup(cfg, client, store)`, `todo, done = _resume(cfg, store, pages)`, `stats = _stream(cfg, client, todo, process_page_factory, state, stop)`, `_verify(store, pages, stats)`, `publish.run(cfg, env, store, source, pages, stats, uploaded, wall, bytes_fetched)`; the two `except` arms and `finally` stay exactly as they are. Each `_stage` function is ≤ 40 lines and sets `state.stage` itself.
- Create: `publish.py` — `alto_dims(cfg, store, pages, uploaded) -> dict`, `run_manifest(...) -> dict` (the `manifest.json` body, field for field as today incl. `page_sources` redaction and `canvas_ids`), `run(...)` (iiif.json when dims, `pipeline.yaml`, `manifest.json` LAST, the COMPLETE log line). Move `_results_json`, `_canvas_id`, `_htrflow_version`, `_changed_sources` to where they are used (`publish.py` / `_resume`).
- Modify: `stream.py` — add `def fetched(pages, dest_dir, client, *, lookahead, concurrency, max_bytes, stop) -> Iterator[FetchResult]`: a generator that owns the `ThreadPoolExecutor`, submits at most `lookahead` downloads ahead of the consumer (bounded submission — the backpressure the Semaphore gave), yields results in completion order, records bytes fetched on an attribute or a returned counter, and always terminates (a downloader failure yields nothing further and re-raises after the consumer has drained, matching today's "sentinel is always pushed" guarantee). `consume(items: Iterable[FetchResult], process, upload, keep_images, max_upload_failures) -> StreamStats` iterates it; `stall_seconds` is measured around `next()`.
- Modify: `fetch.py` — `run_downloader` loses the queue/semaphore parameters (the per-page `fetch_page` with retries/backoff/max_bytes stays as is); keep its tests' behavioural assertions.
- Test: `uv run pytest packages/wrapper -q` green; `uv run --all-packages pytest -q` green; `scripts/loc-budget.sh` wrapper ≤ 1850 (expect a drop of ~50–80 lines; do not chase it); `ruff`/`ty` clean (`make check typecheck`); `dagger call test` green.

- [ ] **Step 1: Characterise first** — before moving anything, run the wrapper suite and note the tests that touch `queue`/`Semaphore` directly; write the new `stream.fetched` generator test (`test_stream.py`: lookahead bound — with `lookahead=2` and a blocking consumer at most 2 downloads are in flight; termination on downloader failure; completion order) and see it fail.
- [ ] **Step 2: Streaming** — implement `fetched`, rewire `consume`, `run_downloader`; wrapper tests green.
- [ ] **Step 3: Stages + `publish.py`** — split `_main`; wrapper tests green; `test_main.py` untouched except imports if a helper moved.
- [ ] **Step 4: Budget, docs** — `scripts/loc-budget.sh`; `docs/how-it-works/wrapper.md` (or wherever the streaming loop is described — grep `lookahead`) updated to name `stream.fetched`/`publish.py`.
- [ ] **Step 5: Commit** in two commits: `refactor(wrapper): streaming loop as a bounded generator (B63)` and `refactor(wrapper): split _main into stage functions and publish.py (B63)`.

## Self-review
- Spec coverage: D1–D2 → Task 2; D3–D5 → Tasks 1–2, 6; D6–D7 → Task 3; D8 → Task 4, 7; D9 → Tasks 2, 5; D10 → Task 8 asserts; D11 untouched (open); D12 → Tasks 5–6; §5 → Task 5; §6 → each task + Task 8; §7 → Task 8.
- Types: `JobSummary/JobDetail/VolumeView` shapes identical in Task 4 (Python) and Task 7 (Zod). `volumes.txt` line format identical in Task 1 (`source_line`), Task 2 (`args`), Task 3 (`IMAGES` parsing), Task 4 (ConfigMap read).
- No placeholders; every step names its command and expected state.
