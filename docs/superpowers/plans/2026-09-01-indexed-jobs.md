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

## Self-review
- Spec coverage: D1–D2 → Task 2; D3–D5 → Tasks 1–2, 6; D6–D7 → Task 3; D8 → Task 4, 7; D9 → Tasks 2, 5; D10 → Task 8 asserts; D11 untouched (open); D12 → Tasks 5–6; §5 → Task 5; §6 → each task + Task 8; §7 → Task 8.
- Types: `JobSummary/JobDetail/VolumeView` shapes identical in Task 4 (Python) and Task 7 (Zod). `volumes.txt` line format identical in Task 1 (`source_line`), Task 2 (`args`), Task 3 (`IMAGES` parsing), Task 4 (ConfigMap read).
- No placeholders; every step names its command and expected state.
