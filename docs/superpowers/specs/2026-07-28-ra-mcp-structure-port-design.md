# ra-mcp structure port for htrflow-batch — design

Date: 2026-07-28
Status: approved (Morgan), pending implementation plan
Reference repos: `~/ra-mcp` (canonical), `~/ape-mcp` (cleanest recent port, 2026-07-27)

## Goal

Adopt the ra-mcp repository conventions in htrflow-batch: `.dagger/` Go CI
module, `.docker/` for dockerfiles + compose, zensical-driven `docs/` site
(replacing the DESIGN.md monolith), a Helm chart replacing the raw `k8s/`
manifests, a Makefile fronting everything, and GitHub workflows that call
dagger. The wrapper Python package layout (`wrapper/`) is explicitly **not**
restructured (no `packages/` workspace port).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| DESIGN.md vs docs site | **Split DESIGN.md into the docs tree**; DESIGN.md deleted at the end (git history keeps it). No content dropped — including full Phase-2 Fluid/WebUFS detail. |
| Dagger scope | **Full port**: `checks`, `test`, `build`, `build-viewer`, `scan`, `publish`, `compose`. CI (ci.yml) calls only `checks` + `test`. |
| Publish default | `docker.io/riksarkivet/htrflow-batch` (viewer: `docker.io/riksarkivet/htrflow-batch-viewer`), registry/repo/tag overridable per call like ape-mcp. |
| Compose stack | **Full local smoke**: rustfs → fixtures-init → wrapper (CPU, `MAX_PAGES=1` default) → viewer. |
| k8s manifests | **Helm chart** `charts/htrflow-batch/` replaces `k8s/`; PoC-only pieces behind `devStack.*.enabled` flags (default false). |

## 1. Target layout

```
htrflow-batch/
├── dagger.json                  # name=htrflow-batch, go sdk, source=.dagger,
│                                #   dep: github.com/shykes/daggerverse/docker-compose (pinned)
├── .dagger/                     # Go module (see §2)
├── .docker/
│   ├── htrflow-batch.dockerfile # moved from docker/ (content unchanged)
│   ├── uv4-viewer.dockerfile    # moved
│   ├── uv4-uv-html.patch        # moved
│   └── docker-compose.yml       # §3
├── charts/htrflow-batch/        # §4
├── docs/ + zensical.toml        # §5; site/ output gitignored
│   └── superpowers/{specs,plans}
├── Makefile                     # §6
├── .github/workflows/           # ci.yml, docs.yml, publish.yml (§7)
├── scripts/
│   └── make_mock_manifest.py    # moved from k8s/fixtures/
├── wrapper/                     # unchanged
├── README.md                    # rewritten short: what it is, quickstart, link to site
└── (deleted at the end: DESIGN.md, PLAN.md → docs/superpowers/plans/, docker/, k8s/)
```

## 2. Dagger module (`.dagger/`, Go)

Follows ape-mcp file-per-function layout: `main.go`, `checks.go`, `test.go`,
`build.go`, `scan.go`, `publish.go`, `compose.go`.

- **Base container**: `python:3.13-slim` + uv binaries (ape-mcp `withUv`
  pattern), source mounted, `uv sync --frozen` **scoped to `wrapper/`**
  (single project, not a workspace — deviation from ape-mcp noted in code).
- **`checks`**: `ruff format --check` + `ruff check` on `wrapper/`
  (ruff added to `wrapper` dev extras), plus `helm lint charts/htrflow-batch`
  (alpine/helm container). No typechecker for now.
- **`test`**: `uv run --extra dev pytest` in `wrapper/` (47 tests, no GPU,
  no network beyond package install).
- **`build`**: wrapper image from `.docker/htrflow-batch.dockerfile`,
  context = repo root (needs `wrapper/`). Returns the container; `export`/
  `publish` compose on top.
- **`build-viewer`**: reproducible viewer image — clone
  `https://github.com/Riksarkivet/universalviewer4` (pinned ref arg,
  default `main`), apply `.docker/uv4-uv-html.patch`, `npm install && npm
  run build` in `node:20`, copy `dist/` onto `nginx:alpine`. Optional
  `--ca-bundle` file arg for RA-firewall TLS interception (mounted +
  `NODE_EXTRA_CA_CERTS`); unnecessary on GitHub runners.
- **`scan`**: Trivy on the wrapper image, `--severity HIGH,CRITICAL`.
  Non-zero exit on findings, but **not wired into ci.yml** (CUDA base is
  huge and will never be alpine-clean; run on demand / pre-publish).
- **`publish`**: constants `DefaultRegistry = docker.io`,
  `DefaultImageRepo = riksarkivet/htrflow-batch`; component arg
  `wrapper|viewer` (viewer repo: `riksarkivet/htrflow-batch-viewer`);
  version read from `wrapper/pyproject.toml`; `--tag` override.
- **`compose`**: wraps the shykes docker-compose module over
  `.docker/docker-compose.yml` (ape-mcp compose.go pattern).

## 3. Compose stack (`.docker/docker-compose.yml`)

Second consumer of the wrapper env contract (§5.1 of the old DESIGN):

| Service | Image | Role |
|---|---|---|
| `rustfs` | `rustfs/rustfs:latest` | S3 at `localhost:9000` (console 9001), named volume |
| `fixtures-init` | `python:3.13-slim` (one-shot) | create `htr-fixtures`/`htr-results` buckets, download the 4 htr_demo images, run `scripts/make_mock_manifest.py`, set anonymous-read + CORS on `htr-results` |
| `wrapper` | `riksarkivet/htrflow-batch` (or `--build`) | one volume, CPU pipeline, `MAX_PAGES=1` default (override `MAX_PAGES` env; full 4-page CPU run is ~13 min), `PUBLIC_RESULTS_BASE=http://localhost:9000/htr-results` |
| `viewer` | `riksarkivet/htrflow-batch-viewer` | `localhost:8080`, `/` redirect to the mock-vol manifest (localhost URLs → browser works with no tunnels) |

`depends_on` chain with healthchecks; `make compose-test` asserts the
wrapper exits 0 and `iiif.json` is served.

## 4. Helm chart (`charts/htrflow-batch/`)

Chart scope = **the platform, not the Jobs** (per-volume Jobs are runtime
submissions; the future `htrq` CLI owns them).

Templates:
- `kueue.yaml` — ResourceFlavor + ClusterQueue + LocalQueue (v1beta2);
  GPU quota and flavor name from values.
- `pipelines.yaml` — `range` over `pipelines:` map → one **immutable**
  ConfigMap `htr-pipeline-<id>` each (D17 semantics: new id = new ConfigMap).
- `viewer-*.yaml` — Deployment/Service/nginx ConfigMap, `viewer.enabled`
  (default true), NodePort + default-manifest redirect from values.
- `devstack-*.yaml` — PoC-only, all default **false**:
  `devStack.rustfs.enabled` (Deployment/PVC/Service/Secret, NodePorts),
  `devStack.registry.enabled` (in-cluster registry, NodePort 30500),
  `devStack.nvidiaDevicePlugin.enabled` (DaemonSet + RuntimeClass nvidia).
- `job-example.yaml` — `exampleJob.enabled` (default false): the mock-vol
  smoke Job wired to the devStack endpoints.

Values sketch: `image.{repository,tag}`, `s3.{endpoint,bucket,existingSecret}`,
`publicResultsBase`, `queue.{name,gpuQuota,flavor}`, `pipelines.<id>` (yaml
string), `viewer.{enabled,image,nodePort,defaultManifest}`, `devStack.*`,
`exampleJob.*`.

Acceptance: `helm template` output reaches parity with today's live k3s
state; PoC replay = `helm install htr charts/htrflow-batch --set
devStack.rustfs.enabled=true --set devStack.registry.enabled=true --set
devStack.nvidiaDevicePlugin.enabled=true --set exampleJob.enabled=true`.
Then `k8s/` is deleted (fixtures generator already moved to `scripts/`).

## 5. Docs site (`docs/` + `zensical.toml`)

zensical config cloned from ape-mcp (site_name `htrflow-batch`, repo url
`carpelan/test` until the real home exists). Nav / DESIGN.md mapping —
**every DESIGN.md section must land somewhere; deleting DESIGN.md is the
last step and requires a completed mapping checklist in the plan**:

| Site section | Pages | From DESIGN.md |
|---|---|---|
| htrflow-batch | `index.md` (what/status/links) | §1–2 intro |
| Getting Started | `index.md` (prereqs incl. k3s host gotchas), `deploy.md` (helm install paths), `run-a-volume.md`, `viewing.md` (ssh tunnels, viewer) | §13 env notes, k8s/README |
| How it Works | `architecture.md` (diagrams §3–4), `decision-log.md` (D1–D19), `wrapper.md` (§5 complete: env contract, stages, exit codes, output contract, model handling, pipeline ConfigMaps), `memory-budget.md` (§6), `failure-handling.md` (§7) | §3–§7 |
| Roadmap | `phase-2-cache.md` (§11 verbatim depth: nginx variant + Fluid/AlluxioRuntime/WebUFS shim + spike gate), `evolution.md` (§12: frontend/htrq-api, CRD guidance), `open-items.md` (§10) | §10–§12 |
| Development | `index.md` (uv setup, TDD norms), `testing.md` (§9), `ci.md` (dagger + make), `security.md` (§8, D14), `deployment.md` (images, publish, chart release), `test-log.md` (§13 + §14 verbatim — the validation record) | §8–§9, §13–§14 |

`docs/superpowers/plans/` receives `PLAN.md` (renamed
`2026-07-27-d16-wrapper-plan.md`); this spec lives in
`docs/superpowers/specs/`. `.superpowers/sdd/` (scratch ledger) is deleted —
its parked finding is resolved (commit af8df6a) and test logs are in docs.

Trade-off accepted: no single standalone document anymore; the site is the
document.

## 6. Makefile

ape-mcp target set adapted: `install` (uv sync in wrapper), `format`,
`lint`, `check` (format+lint), `test`, `ci` (dagger call checks + test),
`build`, `build-viewer`, `scan`, `publish`, `compose-up`, `compose-test`,
`compose-down`, `helm-lint`, `docs-serve` (`uvx zensical serve`),
`docs-build`, `clean`. PoC extras kept until the chart lands everywhere:
`poc-push` (docker build + push to `127.0.0.1:30500`).

## 7. GitHub workflows

- `ci.yml` — push/PR → dagger `checks` then `test` (ape-mcp verbatim,
  pinned action SHAs).
- `docs.yml` — `workflow_dispatch` only (repo private; same caveat comment
  as ape-mcp) → `pip install zensical`, `zensical build --clean`, deploy
  Pages.
- `publish.yml` — `workflow_dispatch` (+ tag trigger later) → dagger
  `publish` with Docker Hub secrets.

## Out of scope

- `packages/` uv-workspace restructure of `wrapper/`
- Real search service for the viewer stub; upstream UV4 PR
- The scale test (separate effort; unblocked by this port)
- Publishing anything now (repo private; no Docker Hub push until asked)

## Risks / notes

- **RA firewall TLS interception** breaks in-container npm/uv/trivy pulls
  when dagger runs on dmlpai01 — mitigations: `--ca-bundle` arg
  (build-viewer), and accepting that `scan`/`build-viewer` may only be
  CI-runnable; `checks`/`test` use uv against PyPI which worked on-host via
  system CA, but inside dagger containers may need the same bundle arg.
  Verify early in implementation; if uv-in-dagger fails on-site, wire the
  CA bundle through all functions from the start.
- **Kueue CRDs** are a cluster prerequisite, not chart-managed (matches
  upstream Kueue guidance); chart README states it.
- The immutable-ConfigMap semantics (D17) survive helm upgrades only if
  pipeline ids are never reused with different content — chart README
  states this; `helm lint` can't enforce it.
