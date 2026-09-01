# TranscriptionJob Controller Implementation Plan (Plan B: frontend, wrapper trim, devstack chart, docs)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Prerequisite: Plan A (`2026-08-31-transcriptionjob-controller.md`) Tasks 1–12 merged** — this plan consumes `/api/v1/jobs`.

**Goal:** Point the status page at the controller's read API, delete the `status.json` derivation layer, remove thumbnails and failure-metrics from the wrapper, move devStack into its own chart, and rewrite the docs — bringing every layer under its size budget.

**Architecture:** The frontend keeps its components and swaps the data source: one `fetch` of `/api/v1/jobs` (list) and `/api/v1/jobs/{ns}/{name}` (detail, paged) replaces `status.ts` + `derive.ts`. The wrapper loses two side features (no contract change). The chart splits into `htrflow-batch` (prod) and `htrflow-devstack` (PoC dependencies). Docs describe the CRDs.

**Tech Stack:** SvelteKit 5 + Zod + Vitest (bun), Python 3.13 + pytest (uv), Helm 3, zensical.

**Spec:** `docs/superpowers/specs/2026-08-31-transcriptionjob-controller-design.md` (§5, §6 devstack, §7, §9 item 4).

## Global Constraints

- Budgets (spec §1): frontend ≤ 2 500 non-test TS/Svelte lines (today 3 952); wrapper ≤ 1 500 (today 1 833); chart ≤ 700 template lines. Plan A Task 12 left `SKIP_FRONTEND=1` in CI — Task 1 here removes it.
- The wrapper's env/exit contract and `manifest.json` fields are unchanged except the removal of the `thumbnail` field and the `metrics-failed-latest.json` object.
- Story id in every commit message (B63; C-series ids where a status-page story is touched).
- Work in a git worktree off `org/main`.

---

### Task 1: Frontend reads `/api/v1/jobs`

**Files:**
- Create: `frontend/src/lib/api.ts` (Zod schemas `JobSummary`, `JobDetail`, `VolumeView` = the shapes from Plan A Task 9; `fetchJobs()`, `fetchJob(ns, name, offset, limit)`), `frontend/src/lib/api.test.ts`
- Delete: `frontend/src/lib/status.ts`, `status.test.ts`, `derive.ts`, `derive.test.ts`
- Modify: `frontend/src/lib/config.ts` (`resolveApiBase()` replaces `resolveStatusUrl()`; `window.API_BASE` / `VITE_API_BASE`, default `/api/v1`), `frontend/src/routes/+page.svelte` (list from `fetchJobs()`, banner "controller unreachable" on fetch error instead of STALE-by-age), `frontend/src/lib/components/CampaignCard.svelte` (props: `job: JobSummary`, volumes loaded lazily via `fetchJob` with paging; drop thumbnail rendering), `PagesTable.svelte` (rows from `VolumeView`), `frontend/src/routes/page.test.ts`, `CampaignCard.test.ts`, `frontend/README.md`, `frontend/static/config.js` + `charts/htrflow-batch/templates/viewer.yaml` (ConfigMap sets `API_BASE`, nginx `location /api/ { proxy_pass http://htrflow-controller:8081/api/; }`)
- Test: the four test files above; `bun run test`; `bun run build`

**Interfaces:**
- Consumes: `GET /api/v1/jobs`, `GET /api/v1/jobs/{ns}/{name}?offset&limit` (Plan A Task 9).
- Produces: `fetchJobs(): Promise<JobSummary[]>`, `fetchJob(ns: string, name: string, offset = 0, limit = 200): Promise<JobDetail>`; `CampaignCard` prop `job: JobSummary`.

- [ ] **Step 1: Write `api.test.ts`** — Zod parse of a fixture list (two jobs) and a detail with 3 volumes; `fetchJobs` against a mocked `fetch` returning the fixture; unknown extra fields are ignored (parse, don't validate — as `status.ts` did); a non-2xx throws `ApiUnreachable`.
- [ ] **Step 2: `bun run test` → FAIL (module missing).**
- [ ] **Step 3: Implement `api.ts`, rewire `+page.svelte` and `CampaignCard`, delete the four files, update nginx/config.**
- [ ] **Step 4: `bun run test && bun run build` → PASS; `SKIP_FRONTEND= scripts/loc-budget.sh` frontend ≤ 2 500** (if over: `log/+page.svelte` 507 lines and `RunSummaryCard` 245 are the candidates — split the log page's parsing into `runlog.ts` which already exists; do not raise the budget).
- [ ] **Step 5: Remove `SKIP_FRONTEND` from `.github/workflows/ci.yml`; commit** `feat(frontend): read jobs from the controller API; drop status.json derivation (B63, C08 paging)`

---

### Task 2: Wrapper — remove thumbnails and failure metrics

**Files:**
- Modify: `packages/wrapper/src/htrflow_batch/main.py` (delete `publish_failure_metrics`, `_publish_failure`, `make_thumbnail`, `previous_thumbnail`, the `thumb_box` plumbing at lines ~289–294 and ~353, the `"thumbnail"` key at ~387; the two `_publish_failure` call sites at ~409/~416 become plain `raise`), `packages/wrapper/src/htrflow_batch/viewer.py` (`_thumbnail` and its use — UV4 renders without it), `packages/wrapper/pyproject.toml` (drop `pillow` if only thumbnails used it — check `uv tree --package htrflow-batch-wrapper | grep -i pillow`), `docs/reference/s3-layout.md` (remove `thumbnail`, `metrics-failed-latest.json` rows), `docs/reference/wrapper.md`
- Test: `packages/wrapper/tests/test_main.py`, `test_viewer.py` (delete the thumbnail/failure-metrics cases; add one asserting `manifest.json` has **no** `thumbnail` key and that a failed run publishes **no** `metrics-failed-latest.json` — using the existing `FakeBucket`)

- [ ] **Step 1: Write the two negative tests → FAIL** (`uv run pytest packages/wrapper -q`).
- [ ] **Step 2: Delete the code.** - [ ] **Step 3: PASS; `scripts/loc-budget.sh` wrapper ≤ 1 500.** - [ ] **Step 4: Commit** `refactor(wrapper): drop thumbnails and metrics-failed-latest.json; contract otherwise unchanged (B63)`

---

### Task 3: `charts/htrflow-devstack`

**Files:**
- Create: `charts/htrflow-devstack/{Chart.yaml, values.yaml, templates/_helpers.tpl, templates/rustfs.yaml, registry.yaml, nvidia.yaml, gitdaemon.yaml, README.md}` (moved from `charts/htrflow-batch/templates/devstack-*.yaml`; values = today's `devStack.*` subtree, top-level)
- Modify: `charts/htrflow-batch/values.yaml` + `values.schema.json` (remove `devStack`, keep `devStack.allowTagImages` as `security.allowTagImages`), `templates/validate.yaml` (references), `templates/network.yaml` (devstack-specific rules move with the templates), `Makefile` (`install-devstack`), `.dagger/checks.go` (lint/template both charts), `docs/development/local-k3s.md`, `docs/getting-started/*.md`
- Test: `helm template` both charts; kubeconform; `scripts/loc-budget.sh` chart ≤ 700; PoC `make install-devstack && make install` order documented.

- [ ] **Step 1: Add a `check-chart` assertion that `htrflow-batch` renders zero objects labelled `app.kubernetes.io/component: devstack` → FAIL.** - [ ] **Step 2: Move templates/values.** - [ ] **Step 3: PASS both charts + budget.** - [ ] **Step 4: Commit** `refactor(chart): devStack becomes charts/htrflow-devstack; prod chart is prod only (B63)`

---

### Task 4: Docs rewrite

**Files:**
- Rewrite: `docs/how-it-works/campaigns.md` (CR lifecycle, phases, codes, pause/delete semantics, the two bold GitOps rules from spec §9.4), `docs/how-it-works/architecture.md` (controller replaces reconciler in the diagram source), `docs/how-it-works/failure-handling.md` (attempts, permanent vs transient, `failures[]`), `docs/reference/campaign-yaml.md` → renamed `docs/reference/crds.md` (field tables from the CRD schemas — generate with `kubectl explain`-style tables from `charts/htrflow-batch/crds/*.yaml` via a 40-line script `scripts/docs/crd-reference.py`, run by `wire.py`), `docs/reference/frontend.md` (API shapes), `docs/reference/s3-layout.md` (no `status/` tree except `logs/`), `docs/getting-started/campaigns.md` and `run-a-volume.md` (`kubectl apply -f` a TranscriptionJob), `docs/development/{deployment,testing,ci,security}.md` (controller build/test, RBAC, NetworkPolicy), `docs/roadmap/open-items.md`, `docs/index.md`, `zensical.toml` nav
- Delete: `docs/reference/reconciler.md`, `docs/how-it-works/live-run-log.md` sections about `status/logs` written by the reconciler (the wrapper still ships run logs — keep that half)
- Test: `uvx zensical build --clean` clean; `grep -rn 'status.json\|reconciler' docs --include=*.md | grep -vE 'superpowers|audits|features|decision-log|test-log|evolution'` empty (history pages keep their words); docs CI gate (B58) green.

- [ ] **Step 1: Run the grep → non-empty.** - [ ] **Step 2: Rewrite.** - [ ] **Step 3: Build + grep clean.** - [ ] **Step 4: Commit** `docs: campaigns are TranscriptionJobs — how-it-works, CRD reference, getting started (B63)`

---

### Task 5: Close out B63

- [ ] Plan A Task 13 E2E re-run with the new frontend (viewer opens from the status page; `STALE` banner appears only when the controller is down).
- [ ] `scripts/loc-budget.sh` all four rows green in CI with no skips.
- [ ] Update `docs/features/batch-kueue-helm/stories/B63-*.md` "Klart när" boxes; `python3 scripts/stories/azure_sync.py story …` (description refresh); link the merge commit on #2978 (`Custom.Commits` + Hyperlink). Do **not** set state Done.
- [ ] Commit `docs(stories): B63 delivered — controller, CRDs, budgets green (B63)`

## Self-review

- Spec §5 → Task 1; §7 → Tasks 1–2; §6 devstack → Task 3; §9.4 docs → Task 4; §1 budgets → Tasks 1–3 + 5. Types: `JobSummary/JobDetail/VolumeView` names match Plan A Task 9. No placeholders.
