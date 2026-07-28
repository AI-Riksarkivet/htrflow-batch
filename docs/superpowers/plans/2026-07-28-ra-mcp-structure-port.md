# ra-mcp Structure Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port htrflow-batch to the ra-mcp repo conventions: `.dagger/` Go CI module, `.docker/` (dockerfiles + compose), zensical `docs/` site replacing DESIGN.md, Helm chart `charts/htrflow-batch/` replacing raw `k8s/`, Makefile, GitHub workflows.

**Architecture:** Reference implementation is `~/ape-mcp` (cleanest recent port; `~/ra-mcp` is the origin — use it only for the Helm chart layout). Spec: `docs/superpowers/specs/2026-07-28-ra-mcp-structure-port-design.md`. The wrapper Python package (`wrapper/`) is NOT restructured.

**Tech Stack:** Dagger Go SDK (engine v0.20.x), uv/ruff/pytest, Helm 3, zensical, docker compose, Trivy.

## Global Constraints

- NEVER `git push`, never `dagger call publish*` against a real registry, never `docker push` to anything except the in-cluster PoC registry `127.0.0.1:30500`. The user pushes/publishes.
- No Claude/AI mentions or Co-Authored-By in commit messages.
- Commit at the end of every task (steps say when).
- The live k3s PoC on dmlpai01 must stay running. Do not `helm install/upgrade` against it; parity is verified with `helm template` diffs only.
- RA firewall does TLS interception. Inside dagger containers, downloads (PyPI via uv, npm, Trivy DB) may fail with cert errors. Every dagger function that downloads takes an `// +optional` `caBundle *dagger.File` argument wired as shown in Task 3. On dmlpai01 pass `--ca-bundle /etc/ssl/certs/ca-certificates.crt`; on GitHub runners omit it.
- Python: wrapper tests must stay green after every task: `cd wrapper && uv run --extra dev pytest -q` → `47 passed` (Task 2 may add to that count only if ruff fixes require a behavioral test change — not expected).
- DESIGN.md and k8s/ are deleted only in their designated tasks (13 and 9), each gated by an explicit checklist in that task.
- Registry/publish defaults (from spec): `DefaultRegistry = "docker.io"`, `DefaultImageRepo = "riksarkivet/htrflow-batch"`, viewer repo `riksarkivet/htrflow-batch-viewer`.

---

### Task 1: Directory scaffolding moves

**Files:**
- Move: `docker/htrflow-batch.dockerfile` → `.docker/htrflow-batch.dockerfile`
- Move: `docker/uv4-viewer.dockerfile` → `.docker/uv4-viewer.dockerfile`
- Move: `docker/uv4-uv-html.patch` → `.docker/uv4-uv-html.patch`
- Move: `k8s/fixtures/make_mock_manifest.py` → `scripts/make_mock_manifest.py`
- Move: `PLAN.md` → `docs/superpowers/plans/2026-07-27-d16-wrapper-plan.md`
- Modify: `.gitignore`, `scripts/make_mock_manifest.py`, `k8s/README.md`, `.dockerignore`

**Interfaces:**
- Produces: `.docker/` paths consumed by Tasks 3–7; `scripts/make_mock_manifest.py` with `BASE` from env, consumed by Task 7 and k8s replay docs.

- [ ] **Step 1: Move files with git mv**

```bash
cd ~/htrflow-batch
mkdir -p .docker scripts docs/superpowers/plans
git mv docker/htrflow-batch.dockerfile .docker/
git mv docker/uv4-viewer.dockerfile .docker/
git mv docker/uv4-uv-html.patch .docker/
rmdir docker
git mv k8s/fixtures/make_mock_manifest.py scripts/
rmdir k8s/fixtures
git mv PLAN.md docs/superpowers/plans/2026-07-27-d16-wrapper-plan.md
```

- [ ] **Step 2: Parametrize the fixture script's base URL**

In `scripts/make_mock_manifest.py` replace the hardcoded line

```python
BASE = "http://10.16.51.53:30900/htr-fixtures/mock-vol"
```

with

```python
import os
BASE = os.environ.get("MOCK_BASE", "http://10.16.51.53:30900/htr-fixtures/mock-vol")
```

(keep the module docstring; add one line to it: `Set MOCK_BASE to point at a different S3 endpoint (compose uses http://localhost:9000/htr-fixtures/mock-vol).`)

- [ ] **Step 3: Update references**

- `.dockerignore`: the entry `docker/` (if present) → remove; add `site/`, `.dagger/`, `docs/`, `charts/` to keep image contexts lean. Final content must include: `wrapper/.venv`, `**/__pycache__`, `.git`, `.superpowers`, `k8s`, `*.md`, `site/`, `.dagger/`, `docs/`, `charts/`.
- `k8s/README.md`: change `fixtures/make_mock_manifest.py` reference to `../scripts/make_mock_manifest.py`.
- `.docker/htrflow-batch.dockerfile` + `.docker/uv4-viewer.dockerfile`: update any comment lines that mention `~/htrflow-batch/docker/...` paths to `.docker/...`.
- `.gitignore`: append `site/` and `.cache/` if missing.
- Then verify nothing else points at the old paths:

```bash
grep -rn 'docker/htrflow-batch.dockerfile\|docker/uv4-viewer\|fixtures/make_mock_manifest' \
  --include='*.md' --include='*.yaml' --include='*.py' . | grep -v '.docker/\|scripts/\|.git/'
```

Expected: no output.

- [ ] **Step 4: Verify wrapper tests still pass**

Run: `cd wrapper && uv run --extra dev pytest -q`
Expected: `47 passed`

- [ ] **Step 5: Verify the fixture script still renders**

Run: `MOCK_BASE=http://example/x python3 scripts/make_mock_manifest.py 2 | python3 -c "import json,sys; m=json.load(sys.stdin); assert m['id']=='http://example/x/manifest.json' and len(m['items'])==2; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Adopt .docker/ and scripts/ layout (ra-mcp structure)"
```

---

### Task 2: Ruff for the wrapper

**Files:**
- Modify: `wrapper/pyproject.toml`
- Modify: any `wrapper/src/**/*.py`, `wrapper/tests/**/*.py` that ruff reformats/fixes

**Interfaces:**
- Produces: `uvx ruff format --check wrapper` and `uvx ruff check wrapper` exit 0 — Task 3's `checks` depends on this being green.

- [ ] **Step 1: Add ruff config + dev dependency**

In `wrapper/pyproject.toml`, extend the dev extra and add config:

```toml
[project.optional-dependencies]
dev = ["pytest>=8", "moto[s3]>=5", "ruff>=0.8"]

[tool.ruff]
line-length = 88
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
```

(`py310` matches the code's declared floor, not the venv's 3.14.)

- [ ] **Step 2: See what fails first**

Run: `cd wrapper && uvx ruff format --check . ; uvx ruff check .`
Expected: some files would be reformatted / some findings (this is the "failing test" for this task; note the count).

- [ ] **Step 3: Apply**

Run: `cd wrapper && uvx ruff format . && uvx ruff check --fix .`
Manually fix anything `--fix` can't (unused imports in tests were noted as deferred minors in the old SDD ledger — `F401` in `tests/test_iiif.py` is expected; delete the unused import).

- [ ] **Step 4: Verify clean + tests green**

Run: `cd wrapper && uvx ruff format --check . && uvx ruff check . && uv run --extra dev pytest -q`
Expected: both ruff commands exit 0, `47 passed`.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Add ruff config and apply formatting to wrapper"
```

---

### Task 3: Dagger module bootstrap — checks + test (firewall probe)

**Files:**
- Create: `dagger.json`, `.dagger/main.go`, `.dagger/checks.go`, `.dagger/test.go`
- Generated by `dagger develop`: `.dagger/go.mod`, `.dagger/go.sum`, `.dagger/dagger.gen.go`, `.dagger/internal/`

**Interfaces:**
- Produces: Go module `main`, struct `HtrflowBatch`, helpers `withUv(container) *dagger.Container`, `withCaBundle(container, caBundle) *dagger.Container`, `buildWithUv(ctx, source, caBundle) (*dagger.Container, error)` (uv-synced `/app` with workdir `/app/wrapper`) — consumed by Tasks 4–7. Functions `Checks(ctx, source, caBundle)` and `Test(ctx, source, caBundle)`.

- [ ] **Step 1: Init the module**

```bash
cd ~/htrflow-batch
dagger init --sdk=go --name=htrflow-batch --source=.dagger
dagger install github.com/shykes/daggerverse/docker-compose@v0.1.1
```

If `dagger` is not on PATH, install per docs (`curl -fsSL https://dl.dagger.io/dagger/install.sh | BIN_DIR=$HOME/.local/bin sh`). Confirm `dagger.json` has `"name": "htrflow-batch"`, `"source": ".dagger"`, and the docker-compose dependency (pinned, like ape-mcp's).

- [ ] **Step 2: Write `.dagger/main.go`**

```go
// htrflow-batch Dagger CI/CD pipeline
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"strings"
)

// HtrflowBatch provides CI/CD pipeline functions for the htrflow-batch project
type HtrflowBatch struct{}

// Default configuration constants. Dagger's +default annotations can't reference
// Go constants (they require inline literals) — these document the canonical
// registry/repos that publish.go's inlined literals must stay in sync with.
const (
	DefaultRegistry   = "docker.io"
	DefaultImageRepo  = "riksarkivet/htrflow-batch"
	DefaultViewerRepo = "riksarkivet/htrflow-batch-viewer"
)

// withUv adds uv/uvx binaries to a container (development/CI tasks only)
func (m *HtrflowBatch) withUv(container *dagger.Container) *dagger.Container {
	uv := dag.Container().From("ghcr.io/astral-sh/uv:latest")
	return container.
		WithFile("/usr/local/bin/uv", uv.File("/uv")).
		WithFile("/usr/local/bin/uvx", uv.File("/uvx"))
}

// withCaBundle mounts a CA bundle for TLS-intercepting networks (RA firewall).
// SSL_CERT_FILE covers Python/uv (uv reads it via native-tls), NODE_EXTRA_CA_CERTS
// covers node/npm.
func (m *HtrflowBatch) withCaBundle(container *dagger.Container, caBundle *dagger.File) *dagger.Container {
	if caBundle == nil {
		return container
	}
	return container.
		WithMountedFile("/etc/ssl/certs/corp-ca.crt", caBundle).
		WithEnvVariable("SSL_CERT_FILE", "/etc/ssl/certs/corp-ca.crt").
		WithEnvVariable("UV_NATIVE_TLS", "true").
		WithEnvVariable("NODE_EXTRA_CA_CERTS", "/etc/ssl/certs/corp-ca.crt")
}

// buildWithUv creates a dev container with uv and the wrapper's deps synced.
// Unlike ape-mcp this is a single project (wrapper/), not a uv workspace.
func (m *HtrflowBatch) buildWithUv(ctx context.Context, source *dagger.Directory, caBundle *dagger.File) (*dagger.Container, error) {
	container := dag.Container().
		From("python:3.13-slim").
		WithDirectory("/app", source, dagger.ContainerWithDirectoryOpts{
			Include: []string{"wrapper/"},
		}).
		WithWorkdir("/app/wrapper")
	container = m.withUv(container)
	container = m.withCaBundle(container, caBundle)
	container = container.WithExec([]string{"uv", "sync", "--no-cache", "--extra", "dev"})
	return container, nil
}

// getVersion reads the wrapper package version
func (m *HtrflowBatch) getVersion(ctx context.Context, source *dagger.Directory, caBundle *dagger.File) (string, error) {
	container, err := m.buildWithUv(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	version, err := container.WithExec([]string{"uv", "version", "--short"}).Stdout(ctx)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(version), nil
}
```

Note: `uv sync --no-cache --extra dev` without `--frozen` — `wrapper/` has no committed `uv.lock`. If `wrapper/uv.lock` exists in the repo, add `--frozen`; otherwise generate and commit one first (`cd wrapper && uv lock`) and then use `--frozen`. Prefer the lock: run `uv lock`, commit it, use `--frozen`.

- [ ] **Step 3: Write `.dagger/checks.go`**

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

var (
	ruffFormatCheckCmd = []string{"uvx", "ruff", "format", "--check", "."}
	ruffCheckCmd       = []string{"uvx", "ruff", "check", "."}
)

// Checks runs code-quality checks on the wrapper (ruff format + lint).
// Helm chart linting is added in a later task once charts/ exists.
func (m *HtrflowBatch) Checks(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks (e.g. /etc/ssl/certs/ca-certificates.crt on RA hosts)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	container, err := m.buildWithUv(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	_, err = container.
		WithExec(ruffFormatCheckCmd).
		WithExec(ruffCheckCmd).
		Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("checks failed: %w", err)
	}
	return "All checks passed", nil
}
```

(Deviation from ape-mcp, intentional: no auto-fix inside CI `Checks` — check-only; `make format`/`make lint` fix locally. No `ty`, no pip-audit for now.)

- [ ] **Step 4: Write `.dagger/test.go`**

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// Test runs the wrapper test suite (pytest, no GPU required)
func (m *HtrflowBatch) Test(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks
	// +optional
	caBundle *dagger.File,
) (string, error) {
	container, err := m.buildWithUv(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	return container.
		WithExec([]string{"uv", "run", "pytest", "--tb=short", "-q"}).
		Stdout(ctx)
}
```

- [ ] **Step 5: Generate bindings, verify the module loads**

Run: `dagger develop && dagger functions`
Expected: `checks`, `test` listed (plus compose dep). Fix compile errors before proceeding.

- [ ] **Step 6: Firewall probe — run both on this host**

Run: `dagger call test` — if it fails with TLS/cert errors during uv sync, run `dagger call test --ca-bundle /etc/ssl/certs/ca-certificates.crt`. Record which form works in the task report; whichever it is:
Expected final output contains `47 passed`.
Run the same for `dagger call checks` → `All checks passed`.

- [ ] **Step 7: Commit**

```bash
git add dagger.json .dagger && git commit -m "Add dagger module with checks and test functions"
```

(If Step 2's note generated `wrapper/uv.lock`, include it: it belongs to this commit.)

---

### Task 4: Dagger build + scan (wrapper image)

**Files:**
- Create: `.dagger/build.go`, `.dagger/scan.go`

**Interfaces:**
- Consumes: `HtrflowBatch`, `withCaBundle` from Task 3.
- Produces: `Build(ctx, source) (*dagger.Container, error)` — the wrapper image; `Scan(ctx, source, severity, format, exitCode, caBundle)` — consumed by publish (Task 6) and Makefile (Task 10).

- [ ] **Step 1: Write `.dagger/build.go`**

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// Build creates the wrapper production image from .docker/htrflow-batch.dockerfile.
// Heavy: the base (airiksarkivet/htrflow + cu128 torch) is ~10 GB; first run
// populates the engine cache.
func (m *HtrflowBatch) Build(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
) (*dagger.Container, error) {
	return source.DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: ".docker/htrflow-batch.dockerfile",
	}), nil
}
```

- [ ] **Step 2: Write `.dagger/scan.go`** (ape-mcp pattern, trimmed to two functions)

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// Scan runs Trivy against the wrapper image. The CUDA/ubuntu base will never be
// alpine-clean; default severity gate is CRITICAL,HIGH. Not wired into ci.yml.
func (m *HtrflowBatch) Scan(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// +default="CRITICAL,HIGH"
	severity string,
	// +default="table"
	format string,
	// +default=1
	exitCode int,
	// CA bundle for TLS-intercepting networks (Trivy DB download)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	container, err := m.Build(ctx, source)
	if err != nil {
		return "", fmt.Errorf("build failed before scanning: %w", err)
	}
	tarFile := container.AsTarball()
	trivy := dag.Container().From("aquasec/trivy:latest")
	trivy = m.withCaBundle(trivy, caBundle)
	output, err := trivy.
		WithMountedFile("/image.tar", tarFile).
		WithExec([]string{
			"trivy", "image", "--input", "/image.tar",
			"--severity", severity, "--format", format,
			"--exit-code", fmt.Sprintf("%d", exitCode),
		}).
		Stdout(ctx)
	if err != nil {
		if output == "" {
			return "", fmt.Errorf("trivy scan failed: %w", err)
		}
		return output, fmt.Errorf("vulnerabilities found: %w", err)
	}
	return output, nil
}

// ScanJson returns JSON scan results without failing on findings
func (m *HtrflowBatch) ScanJson(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// +default="CRITICAL,HIGH"
	severity string,
	// +optional
	caBundle *dagger.File,
) (string, error) {
	return m.Scan(ctx, source, severity, "json", 0, caBundle)
}
```

- [ ] **Step 3: Verify build compiles and produces the image**

Run: `dagger develop && dagger call build --help` (lists the function) then
`dagger call build` — expect it to complete (first run pulls the ~10 GB base into the engine cache; allow 10–30 min). Then spot-check the entrypoint:
`dagger call build with-exec --args python,-c,"import htrflow_batch; print('ok')" stdout`
Expected: `ok`.

- [ ] **Step 4: Verify scan runs (non-blocking form)**

Run: `dagger call scan-json` (add `--ca-bundle /etc/ssl/certs/ca-certificates.crt` if Task 3's probe needed it)
Expected: JSON output (findings are fine; command must not error out with `--exit-code 0` semantics of ScanJson).

- [ ] **Step 5: Commit**

```bash
git add .dagger && git commit -m "Add dagger build and scan functions"
```

---

### Task 5: Dagger build-viewer (reproducible UV4 image)

**Files:**
- Create: `.dagger/viewer.go`

**Interfaces:**
- Consumes: `withCaBundle` (Task 3).
- Produces: `BuildViewer(ctx, source, ref, caBundle) (*dagger.Container, error)` — nginx image with UV4 dist; consumed by publish (Task 6).

- [ ] **Step 1: Write `.dagger/viewer.go`**

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// BuildViewer builds the UV4 viewer image reproducibly: clone the Riksarkivet
// universalviewer4 fork, apply .docker/uv4-uv-html.patch (enables the ALTO text
// panel config fetch + fixes overlay coordinates), npm build on node:20, then
// layer dist/ onto nginx:alpine. See docs D19 notes for why the patch exists.
func (m *HtrflowBatch) BuildViewer(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// Git ref of Riksarkivet/universalviewer4 to build
	// +default="main"
	ref string,
	// CA bundle for TLS-intercepting networks (npm + git clone)
	// +optional
	caBundle *dagger.File,
) (*dagger.Container, error) {
	uvSrc := dag.Git("https://github.com/Riksarkivet/universalviewer4").
		Ref(ref).
		Tree()

	builder := dag.Container().
		From("node:20-bookworm").
		WithDirectory("/src", uvSrc).
		WithFile("/src/uv4.patch", source.File(".docker/uv4-uv-html.patch")).
		WithWorkdir("/src")
	builder = m.withCaBundle(builder, caBundle)
	builder = builder.
		WithExec([]string{"git", "init", "-q"}). // patch applies with git apply; clone tree has no .git
		WithExec([]string{"git", "apply", "uv4.patch"}).
		WithExec([]string{"npm", "install", "--no-audit", "--no-fund"}).
		WithExec([]string{"npm", "run", "build"})

	dist := builder.Directory("/src/dist")

	viewer := dag.Container().
		From("nginx:alpine").
		WithDirectory("/usr/share/nginx/html", dist)
	return viewer, nil
}
```

- [ ] **Step 2: Verify it compiles and builds**

Run: `dagger develop && dagger call build-viewer --ca-bundle /etc/ssl/certs/ca-certificates.crt with-exec --args ls,/usr/share/nginx/html stdout`
(drop `--ca-bundle` if Task 3's probe showed it unnecessary)
Expected output contains: `uv.html`, `index.html`, `umd`, `uv-iiif-config.json`.

- [ ] **Step 3: Verify the patch actually landed in the artifact**

Run: `dagger call build-viewer --ca-bundle /etc/ssl/certs/ca-certificates.crt with-exec --args grep,-c,uv-iiif-config.json,/usr/share/nginx/html/uv.html stdout`
Expected: `2` (the patched uv.html fetches its config; unpatched is `0`).

- [ ] **Step 4: Commit**

```bash
git add .dagger && git commit -m "Add dagger build-viewer for reproducible UV4 image"
```

---

### Task 6: Dagger publish + compose functions

**Files:**
- Create: `.dagger/publish.go`, `.dagger/compose.go`

**Interfaces:**
- Consumes: `Build` (Task 4), `BuildViewer` (Task 5), `Test` (Task 3), `getVersion` (Task 3).
- Produces: `PublishDocker(...)`, `ComposeUp(source) *dagger.Service`, `ComposeTest(ctx, source)` — Makefile (Task 10) and publish.yml (Task 10) call these. Compose functions operate on `.docker/docker-compose.yml` which Task 7 creates; ComposeTest is only *runnable* after Task 7.

- [ ] **Step 1: Write `.dagger/publish.go`** (adapted from ape-mcp: version from `wrapper/pyproject.toml`; `component` selects wrapper vs viewer)

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
	"strings"
)

// resolveTag returns tag if given (validated against the wrapper version unless
// skipped), else "v" + wrapper version.
func (m *HtrflowBatch) resolveTag(ctx context.Context, source *dagger.Directory, tag string, skipValidation bool, caBundle *dagger.File) (string, error) {
	version, err := m.getVersion(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	if tag == "" {
		return "v" + version, nil
	}
	if !skipValidation {
		norm := func(v string) string { return strings.TrimPrefix(strings.TrimSpace(v), "v") }
		if norm(version) != norm(tag) {
			return "", fmt.Errorf("version mismatch: wrapper/pyproject.toml has 'v%s' but tag is '%s'", version, tag)
		}
	}
	return tag, nil
}

// PublishDocker tests, builds and publishes an image to a registry.
// component: "wrapper" (default) or "viewer".
func (m *HtrflowBatch) PublishDocker(
	ctx context.Context,
	// +default="wrapper"
	component string,
	// Image repository; empty selects the default for the component
	// (riksarkivet/htrflow-batch or riksarkivet/htrflow-batch-viewer)
	// +optional
	imageRepository string,
	// Image tag (empty: "v" + version from wrapper/pyproject.toml)
	// +optional
	tag string,
	// +default="docker.io"
	registry string,
	// +optional
	dockerUsername *dagger.Secret,
	// +optional
	dockerPassword *dagger.Secret,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// +optional
	skipValidation bool,
	// +optional
	caBundle *dagger.File,
) (string, error) {
	resolvedTag, err := m.resolveTag(ctx, source, tag, skipValidation, caBundle)
	if err != nil {
		return "", err
	}

	if _, err := m.Test(ctx, source, caBundle); err != nil {
		return "", fmt.Errorf("tests failed, aborting publish: %w", err)
	}

	var container *dagger.Container
	switch component {
	case "wrapper":
		if imageRepository == "" {
			imageRepository = "riksarkivet/htrflow-batch"
		}
		container, err = m.Build(ctx, source)
	case "viewer":
		if imageRepository == "" {
			imageRepository = "riksarkivet/htrflow-batch-viewer"
		}
		container, err = m.BuildViewer(ctx, source, "main", caBundle)
	default:
		return "", fmt.Errorf("unknown component %q (wrapper|viewer)", component)
	}
	if err != nil {
		return "", fmt.Errorf("build failed during publish: %w", err)
	}

	imageRef := registry + "/" + imageRepository + ":" + resolvedTag
	if dockerPassword != nil && dockerUsername != nil {
		username, err := dockerUsername.Plaintext(ctx)
		if err != nil {
			return "", fmt.Errorf("failed to read docker username: %w", err)
		}
		return container.WithRegistryAuth(registry, username, dockerPassword).Publish(ctx, imageRef)
	}
	return container.Publish(ctx, imageRef)
}
```

- [ ] **Step 2: Write `.dagger/compose.go`** (ape-mcp pattern; the "app" service here is the viewer since the wrapper is a run-to-completion job)

```go
package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// ComposeUp loads .docker/docker-compose.yml and starts the stack on the Dagger engine
func (m *HtrflowBatch) ComposeUp(
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
) *dagger.Service {
	project := dag.DockerCompose().Project(dagger.DockerComposeProjectOpts{
		Source: source.Directory(".docker"),
	})
	return project.Service("viewer").Up()
}

// ComposeTest starts the compose stack and verifies the viewer serves uv.html
func (m *HtrflowBatch) ComposeTest(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
) (string, error) {
	service := m.ComposeUp(source)
	output, err := dag.Container().
		From("curlimages/curl:latest").
		WithServiceBinding("viewer", service).
		WithExec([]string{"curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "http://viewer:80/uv.html"}).
		Stdout(ctx)
	if err != nil {
		return "", fmt.Errorf("compose health check failed: %w", err)
	}
	return fmt.Sprintf("viewer healthy (HTTP %s)", output), nil
}
```

- [ ] **Step 3: Verify compilation and function listing only**

Run: `dagger develop && dagger functions`
Expected: `publish-docker`, `compose-up`, `compose-test` listed.
**Do NOT run publish-docker** (Global Constraints — publishing is the user's). ComposeTest is exercised in Task 7 once the compose file exists.

- [ ] **Step 4: Commit**

```bash
git add .dagger && git commit -m "Add dagger publish and compose functions"
```

---

### Task 7: Compose stack — full local smoke

**Files:**
- Create: `.docker/docker-compose.yml`, `scripts/compose_init.py`

**Interfaces:**
- Consumes: `scripts/make_mock_manifest.py` (Task 1, `MOCK_BASE` env), wrapper env contract (see table in step 2 — matches `wrapper/src/htrflow_batch/config.py`).
- Produces: services `rustfs`, `fixtures-init`, `wrapper`, `viewer` — `ComposeUp/ComposeTest` (Task 6) and `make compose-*` (Task 10) run these.

- [ ] **Step 1: Write `scripts/compose_init.py`** (one-shot: buckets, fixtures, policy/CORS, manifest)

```python
"""Compose init: create buckets, upload htr_demo fixture pages, publish the
mock IIIF manifest, and open anonymous read + CORS on htr-results.

Env: S3_ENDPOINT (default http://rustfs:9000), MOCK_BASE (default
http://localhost:9000/htr-fixtures/mock-vol — localhost because the *browser*
resolves the published URLs), AWS creds via standard vars."""
import json
import os
import subprocess
import sys

import boto3
import httpx

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://rustfs:9000")
MOCK_BASE = os.environ.get("MOCK_BASE", "http://localhost:9000/htr-fixtures/mock-vol")
HF = "https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples"
PAGES = ["A0062408_00006.jpg", "A0073477_00025.jpg", "A0068699_00021.jpg", "C0000263_00048.jpg"]

s3 = boto3.client("s3", endpoint_url=ENDPOINT)
for bucket in ("htr-fixtures", "htr-results"):
    try:
        s3.create_bucket(Bucket=bucket)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

for i, name in enumerate(PAGES, start=1):
    key = f"mock-vol/{i:04d}.jpg"
    r = httpx.get(f"{HF}/{name}", follow_redirects=True)
    r.raise_for_status()
    s3.put_object(Bucket="htr-fixtures", Key=key, Body=r.content, ContentType="image/jpeg")
    print("uploaded", key)

manifest = subprocess.run(
    [sys.executable, "/scripts/make_mock_manifest.py", str(len(PAGES))],
    env={**os.environ, "MOCK_BASE": MOCK_BASE}, capture_output=True, text=True, check=True,
).stdout
s3.put_object(Bucket="htr-fixtures", Key="mock-vol/manifest.json",
              Body=manifest.encode(), ContentType="application/json")

for bucket in ("htr-fixtures", "htr-results"):
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"AWS": ["*"]},
                       "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]}]}))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"],
        "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]})
print("init complete")
```

Note the fixture *page filenames*: verify the four names against what `k8s/mini-wrapper.yaml`/`k8s/job-example.yaml` used (they are the known-good htr_demo examples). If any 404s at runtime, substitute another `.gradio_cache/examples` filename — the exact pages don't matter, count does.

- [ ] **Step 2: Write `.docker/docker-compose.yml`**

```yaml
# Full local smoke stack (no k8s): S3 + fixtures + one wrapper run + viewer.
# Wrapper env contract mirrors the k8s Job (see docs how-it-works/wrapper).
services:
  rustfs:
    image: rustfs/rustfs:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      RUSTFS_ACCESS_KEY: rustfsadmin
      RUSTFS_SECRET_KEY: rustfsadmin
    volumes:
      - rustfs-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/"]
      interval: 5s
      timeout: 3s
      retries: 20

  fixtures-init:
    image: ghcr.io/astral-sh/uv:python3.13-bookworm-slim
    depends_on:
      rustfs:
        condition: service_healthy
    volumes:
      - ../scripts:/scripts:ro
    environment:
      S3_ENDPOINT: http://rustfs:9000
      MOCK_BASE: http://localhost:9000/htr-fixtures/mock-vol
      AWS_ACCESS_KEY_ID: rustfsadmin
      AWS_SECRET_ACCESS_KEY: rustfsadmin
    command: ["uv", "run", "--with", "boto3", "--with", "httpx",
              "python", "/scripts/compose_init.py"]

  wrapper:
    image: riksarkivet/htrflow-batch:latest
    build:
      context: ..
      dockerfile: .docker/htrflow-batch.dockerfile
    depends_on:
      fixtures-init:
        condition: service_completed_successfully
    volumes:
      - ../k8s/pipeline-demo-v1.yaml:/config/pipeline-src.yaml:ro
    entrypoint: ["/bin/bash", "-c"]
    command:
      - >
        python -c "import yaml,sys;
        d=yaml.safe_load(open('/config/pipeline-src.yaml'));
        open('/config/pipeline.yaml','w').write(d['data']['pipeline.yaml'])"
        && python -m htrflow_batch
    environment:
      VOLUME_REF: mock-vol
      IIIF_MANIFEST_URL: http://rustfs:9000/htr-fixtures/mock-vol/manifest.json
      PIPELINE_PATH: /config/pipeline.yaml
      PIPELINE_ID: demo-v1
      S3_ENDPOINT: http://rustfs:9000
      S3_BUCKET: htr-results
      AWS_ACCESS_KEY_ID: rustfsadmin
      AWS_SECRET_ACCESS_KEY: rustfsadmin
      PUBLIC_RESULTS_BASE: http://localhost:9000/htr-results
      MAX_PAGES: "1"          # CPU is ~41x slower than GPU; full volume ≈ 13 min
      WORKDIR_PATH: /work
    tmpfs:
      - /work
    # No GPU: htrflow falls back to CPU automatically.

  viewer:
    image: riksarkivet/htrflow-batch-viewer:latest
    ports:
      - "8080:80"
    depends_on:
      rustfs:
        condition: service_healthy

volumes:
  rustfs-data:
```

Adjustments the implementer must make from reality, not assumption:
- Check `wrapper/src/htrflow_batch/config.py` for the exact required env names (the table above is from the DESIGN §5.1 contract; `config.py` is the source of truth). Fix any mismatch.
- Check the RustFS image's actual env var names for credentials (`docker run --rm rustfs/rustfs:latest --help` or the k8s/rustfs.yaml which is known-working) and its healthcheck-friendly endpoint; copy from `k8s/rustfs.yaml`.
- The wrapper command extracts the pipeline YAML out of the k8s ConfigMap wrapper — after Task 9 deletes `k8s/`, change the mount to the chart's pipeline value file or a plain `.docker/pipeline-demo-v1.yaml` copy. Simpler and preferred: create `.docker/pipeline-demo-v1.yaml` now containing just the pipeline YAML (copy the `data."pipeline.yaml"` block content out of `k8s/pipeline-demo-v1.yaml`, dedented), mount it directly to `/config/pipeline.yaml`, and drop the extraction entrypoint entirely (plain `command: []`, default entrypoint `python -m htrflow_batch`).
- The `viewer` image must exist locally; until the user publishes, load it via `dagger call build-viewer ... export --path /tmp/viewer.tar && docker load -i /tmp/viewer.tar` or tag from the k3s registry (`docker pull 127.0.0.1:30500/uv4:v3 && docker tag ... riksarkivet/htrflow-batch-viewer:latest`). Document the chosen path in the compose file header comment.

- [ ] **Step 3: Verify with plain docker compose first (faster loop)**

```bash
cd ~/htrflow-batch/.docker
docker compose up --build --abort-on-container-exit --exit-code-from wrapper wrapper
```
Expected: fixtures-init prints `init complete`; wrapper logs end with `COMPLETE 1 pages (1 processed)` and exit 0 (models download on first run — allow time; CPU inference for 1 page ≈ 3–4 min after download).

Then: `docker compose up -d viewer && curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/uv.html` → `200`,
and `curl -s http://localhost:9000/htr-results/demo-v1/mock-vol/iiif.json | python3 -c "import json,sys; m=json.load(sys.stdin); assert m['service'][0]['profile'].endswith('search/1/search'); assert 'thumbnail' in m['items'][0]; print('manifest ok')"` → `manifest ok`
(this also proves Task "hardening" viewer.py output works end-to-end).
Finally `docker compose down -v`.

- [ ] **Step 4: Verify via dagger**

Run: `dagger call compose-test`
Expected: `viewer healthy (HTTP 200)`.

- [ ] **Step 5: Commit**

```bash
git add .docker scripts && git commit -m "Add docker-compose local smoke stack and init script"
```

---

### Task 8: Helm chart — core (Chart, values, kueue, pipelines) + helm lint in checks

**Files:**
- Create: `charts/htrflow-batch/Chart.yaml`, `charts/htrflow-batch/values.yaml`, `charts/htrflow-batch/.helmignore`, `charts/htrflow-batch/templates/_helpers.tpl`, `charts/htrflow-batch/templates/kueue.yaml`, `charts/htrflow-batch/templates/pipelines.yaml`, `charts/htrflow-batch/templates/NOTES.txt`
- Modify: `.dagger/checks.go`

**Interfaces:**
- Consumes: existing `k8s/kueue-queues.yaml`, `k8s/pipeline-demo-v1.yaml` as the parity reference (read them; port, don't invent).
- Produces: chart skeleton + values schema consumed by Task 9's templates: `.Values.image`, `.Values.s3`, `.Values.publicResultsBase`, `.Values.queue`, `.Values.pipelines`, `.Values.viewer`, `.Values.devStack`, `.Values.exampleJob`.

- [ ] **Step 1: Chart.yaml, .helmignore, NOTES.txt**

```yaml
# charts/htrflow-batch/Chart.yaml
apiVersion: v2
name: htrflow-batch
description: Kueue-gated batch HTR platform around the htrflow image (queues, pipeline configs, results viewer). Per-volume Jobs are submitted at runtime, not by this chart.
type: application
version: 0.1.0
appVersion: "0.1.0"
```

`.helmignore`: copy `~/ra-mcp/charts/ra-mcp/.helmignore` verbatim.
`NOTES.txt`:

```
htrflow-batch platform installed.
- LocalQueue: {{ .Values.queue.name }} (namespace {{ .Release.Namespace }})
- Kueue CRDs are a prerequisite, not installed by this chart.
- Pipeline ConfigMaps are immutable: never reuse a pipeline id with different content — bump the id (D17).
- Submit per-volume Jobs with suspend: true and label kueue.x-k8s.io/queue-name: {{ .Values.queue.name }}.
{{- if .Values.viewer.enabled }}
- Viewer: NodePort {{ .Values.viewer.nodePort }}
{{- end }}
```

- [ ] **Step 2: values.yaml**

```yaml
# Default values: production-shaped. The k3s PoC replay overrides are shown in
# docs getting-started (devStack.* true, image from the in-cluster registry).
image:
  repository: docker.io/riksarkivet/htrflow-batch
  tag: latest

s3:
  endpoint: ""            # in-cluster S3 endpoint URL; empty = provider default chain
  bucket: htr-results
  existingSecret: htr-batch-s3   # keys: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

publicResultsBase: ""      # browser-reachable URL base for published results (REQUIRED for viewer manifests)

queue:
  name: htr-batch
  flavor: default-flavor
  # ClusterQueue quota: how many concurrent GPU jobs. PoC modelled GPUs as CPU
  # quota (no real gpu resource on the flavor) — keep both knobs:
  gpuQuota: 2
  resource: cpu            # "cpu" on the PoC; "nvidia.com/gpu" on a real cluster

pipelines:
  # id -> htrflow pipeline YAML (string). Rendered as immutable ConfigMap
  # htr-pipeline-<id>. NEVER change content under an existing id (D17).
  {}

viewer:
  enabled: true
  image: docker.io/riksarkivet/htrflow-batch-viewer:latest
  nodePort: 30800
  defaultManifest: ""      # if set, / redirects to uv.html#?manifest=<this>

devStack:                  # PoC-only in-cluster dependencies, all off by default
  rustfs:
    enabled: false
    nodePortS3: 30900
    nodePortConsole: 30901
  registry:
    enabled: false
    nodePort: 30500
  nvidiaDevicePlugin:
    enabled: false

exampleJob:
  enabled: false           # mock-vol smoke Job wired to the devStack endpoints
  image: ""                # e.g. 127.0.0.1:30500/htrflow-batch:v3 on the PoC
  manifestUrl: ""
  pipelineId: demo-v1
```

- [ ] **Step 3: _helpers.tpl**

```
{{/* charts/htrflow-batch/templates/_helpers.tpl */}}
{{- define "htrflow-batch.labels" -}}
app.kubernetes.io/name: htrflow-batch
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: htrflow-batch-{{ .Chart.Version }}
{{- end }}
```

- [ ] **Step 4: templates/kueue.yaml** — port `k8s/kueue-queues.yaml` (v1beta2). Read the source file first; result must render the same three objects with values substituted:

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: {{ .Values.queue.flavor }}
  labels: {{- include "htrflow-batch.labels" . | nindent 4 }}
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: ClusterQueue
metadata:
  name: {{ .Values.queue.name }}-cq
  labels: {{- include "htrflow-batch.labels" . | nindent 4 }}
spec:
  namespaceSelector: {}
  resourceGroups:
    - coveredResources: [{{ .Values.queue.resource | quote }}]
      flavors:
        - name: {{ .Values.queue.flavor }}
          resources:
            - name: {{ .Values.queue.resource | quote }}
              nominalQuota: {{ .Values.queue.gpuQuota }}
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: LocalQueue
metadata:
  name: {{ .Values.queue.name }}
  namespace: {{ .Release.Namespace }}
  labels: {{- include "htrflow-batch.labels" . | nindent 4 }}
spec:
  clusterQueue: {{ .Values.queue.name }}-cq
```

**Parity check against `k8s/kueue-queues.yaml`:** the live PoC's ClusterQueue name, flavor name, quota value and covered resource must be reproducible via values. If the live names differ from `<queue.name>-cq` (read the file!), adjust the template to allow an explicit `queue.clusterQueueName` value defaulting to `printf "%s-cq" .Values.queue.name`.

- [ ] **Step 5: templates/pipelines.yaml**

```yaml
{{- range $id, $yaml := .Values.pipelines }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: htr-pipeline-{{ $id }}
  namespace: {{ $.Release.Namespace }}
  labels: {{- include "htrflow-batch.labels" $ | nindent 4 }}
immutable: true
data:
  pipeline.yaml: |
{{ $yaml | indent 4 }}
{{- end }}
```

- [ ] **Step 6: Add helm lint to dagger Checks**

In `.dagger/checks.go`, extend `Checks` after the ruff execs:

```go
	// Helm chart lint (alpine/helm has no TLS needs — no caBundle wiring)
	_, err = dag.Container().
		From("alpine/helm:latest").
		WithDirectory("/chart", source.Directory("charts/htrflow-batch")).
		WithExec([]string{"helm", "lint", "/chart"}).
		Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("helm lint failed: %w", err)
	}
```

- [ ] **Step 7: Verify lint + render parity**

```bash
helm lint charts/htrflow-batch
helm template htr charts/htrflow-batch \
  --set-file pipelines.demo-v1=<(python3 -c "import yaml;print(yaml.safe_load(open('k8s/pipeline-demo-v1.yaml'))['data']['pipeline.yaml'])") \
  | tee /tmp/render.yaml
python3 - <<'EOF'
import yaml
rendered = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(open("/tmp/render.yaml")) if d}
live = {}
for f in ("k8s/kueue-queues.yaml", "k8s/pipeline-demo-v1.yaml"):
    for d in yaml.safe_load_all(open(f)):
        if d: live[(d["kind"], d["metadata"]["name"])] = d
missing = set(live) - set(rendered)
assert not missing, f"chart does not render: {missing}"
cm = rendered[("ConfigMap", "htr-pipeline-demo-v1")]
assert cm["immutable"] is True
assert yaml.safe_load(cm["data"]["pipeline.yaml"]) == yaml.safe_load(live[("ConfigMap","htr-pipeline-demo-v1")]["data"]["pipeline.yaml"])
print("parity ok")
EOF
```
Expected: `parity ok`. (If names mismatch, fix the template or add the override value per Step 4 note — never rename the live objects.)
Also run `dagger call checks` (with `--ca-bundle` if needed) → `All checks passed`.

- [ ] **Step 8: Commit**

```bash
git add charts .dagger && git commit -m "Add helm chart core: kueue queues + immutable pipeline configmaps"
```

---

### Task 9: Helm chart — viewer, devStack, example Job; retire k8s/

**Files:**
- Create: `charts/htrflow-batch/templates/viewer.yaml`, `templates/devstack-rustfs.yaml`, `templates/devstack-registry.yaml`, `templates/devstack-nvidia.yaml`, `templates/job-example.yaml`
- Delete (end of task): `k8s/` (entire directory)

**Interfaces:**
- Consumes: values schema from Task 8; existing `k8s/uv4-viewer.yaml`, `k8s/rustfs.yaml`, `k8s/registry.yaml`, `k8s/nvidia-device-plugin.yaml`, `k8s/job-real-wrapper.yaml` as porting sources — read each fully before templating.
- Produces: complete chart; the PoC replay command documented in `docs/getting-started/deploy.md` (Task 11).

- [ ] **Step 1: templates/viewer.yaml** — port `k8s/uv4-viewer.yaml` (Deployment + Service + nginx ConfigMap) wrapped in `{{- if .Values.viewer.enabled }}` ... `{{- end }}`. Substitutions: image → `{{ .Values.viewer.image }}`, nodePort → `{{ .Values.viewer.nodePort }}`, and the redirect location block only rendered when `.Values.viewer.defaultManifest` is non-empty:

```yaml
      location = / {
        return 302 /uv.html#?manifest={{ .Values.viewer.defaultManifest }};
      }
```
Keep `absolute_redirect off;` unconditionally (port-preserving redirects — hard-won fix).

- [ ] **Step 2: devstack templates** — each file starts with its gate:
  - `devstack-rustfs.yaml`: `{{- if .Values.devStack.rustfs.enabled }}` + full content of `k8s/rustfs.yaml` (Deployment, PVC, Service with the two NodePorts from values, Secret `htr-batch-s3`) — namespace becomes `{{ .Release.Namespace }}`; drop the standalone Namespace object (helm owns namespaces via `--create-namespace`).
  - `devstack-registry.yaml`: `{{- if .Values.devStack.registry.enabled }}` + `k8s/registry.yaml` content; keep its own namespace handling as-is from the source file *except* rendering the namespace name literally `registry` is fine for the PoC — note it in a comment.
  - `devstack-nvidia.yaml`: `{{- if .Values.devStack.nvidiaDevicePlugin.enabled }}` + `k8s/nvidia-device-plugin.yaml` content (DaemonSet + RuntimeClass, kube-system namespace as in source).

- [ ] **Step 3: templates/job-example.yaml** — port `k8s/job-real-wrapper.yaml` behind `{{- if .Values.exampleJob.enabled }}`; image → `{{ .Values.exampleJob.image | required "exampleJob.image is required when exampleJob.enabled" }}`, manifest URL → `{{ .Values.exampleJob.manifestUrl }}`, pipeline ConfigMap name → `htr-pipeline-{{ .Values.exampleJob.pipelineId }}`, queue label → `{{ .Values.queue.name }}`, `PUBLIC_RESULTS_BASE` → `{{ .Values.publicResultsBase }}`, S3 secret → `{{ .Values.s3.existingSecret }}`.

- [ ] **Step 4: Full-chart render parity vs k8s/**

```bash
helm lint charts/htrflow-batch
helm template htr charts/htrflow-batch -n htr-batch \
  --set devStack.rustfs.enabled=true --set devStack.registry.enabled=true \
  --set devStack.nvidiaDevicePlugin.enabled=true --set exampleJob.enabled=true \
  --set exampleJob.image=127.0.0.1:30500/htrflow-batch:v3 \
  --set exampleJob.manifestUrl=http://10.16.51.53:30900/htr-fixtures/mock-vol/manifest.json \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set viewer.image=127.0.0.1:30500/uv4:v3 \
  --set viewer.defaultManifest=http://localhost:30900/htr-results/demo-v1/mock-vol/iiif.json \
  --set-file pipelines.demo-v1=<(python3 -c "import yaml;print(yaml.safe_load(open('k8s/pipeline-demo-v1.yaml'))['data']['pipeline.yaml'])") \
  > /tmp/render-all.yaml
python3 - <<'EOF'
import glob, yaml
rendered = {(d["kind"], d["metadata"]["name"]) for d in yaml.safe_load_all(open("/tmp/render-all.yaml")) if d}
skip_kinds = {"Namespace"}          # helm --create-namespace replaces these
skip_files = {"k8s/mini-wrapper.yaml", "k8s/htr-real-test.yaml", "k8s/htr-real-gpu.yaml",
              "k8s/job-example.yaml"}   # one-off smoke artifacts, intentionally not chart-managed
live = set()
for f in glob.glob("k8s/*.yaml"):
    if f in skip_files: continue
    for d in yaml.safe_load_all(open(f)):
        if d and d["kind"] not in skip_kinds:
            live.add((d["kind"], d["metadata"]["name"]))
missing = live - rendered
assert not missing, f"chart is missing: {sorted(missing)}"
print("full parity ok:", len(live), "objects covered")
EOF
```
Expected: `full parity ok: N objects covered`. **Do not** helm-install against the live cluster (Global Constraints).

- [ ] **Step 5: Retire k8s/ — gated checklist**

Confirm ALL of, in the task report:
1. Parity script passed (step 4 output pasted).
2. `k8s/README.md` replay knowledge is preserved → copy its "Replay" and "Bucket setup" sections into `charts/htrflow-batch/README.md` (create it: chart purpose, prerequisite Kueue CRDs, PoC replay via the exact helm template/install flags from step 4, immutability warning).
3. `.docker/docker-compose.yml` no longer mounts anything under `k8s/` (Task 7's preferred variant — if the compose file still mounts `k8s/pipeline-demo-v1.yaml`, do that migration now: create `.docker/pipeline-demo-v1.yaml` with the pipeline YAML content and update the mount).
4. `grep -rn 'k8s/' --include='*.md' --include='*.yaml' --include='*.go' . | grep -v '.git/\|docs/superpowers/'` → remaining hits only in historical docs (specs/plans) or none.

Then: `git rm -r k8s/`

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Complete helm chart (viewer, devstack, example job); retire raw k8s manifests"
```

---

### Task 10: Makefile + GitHub workflows

**Files:**
- Create: `Makefile`, `.github/workflows/ci.yml`, `.github/workflows/docs.yml`, `.github/workflows/publish.yml`

**Interfaces:**
- Consumes: dagger functions from Tasks 3–7 (`checks`, `test`, `build`, `build-viewer`, `scan`, `publish-docker`, `compose-up`, `compose-test`).

- [ ] **Step 1: Makefile**

```makefile
.PHONY: install format lint check test ci build build-viewer scan publish \
        compose-up compose-test compose-down helm-lint docs-serve docs-build poc-push clean

# On RA hosts dagger containers need the corp CA; harmless elsewhere if the file exists.
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))

install:
	cd wrapper && uv sync --extra dev

format:
	cd wrapper && uvx ruff format .

lint:
	cd wrapper && uvx ruff check --fix .

check: format lint

test:
	cd wrapper && uv run --extra dev pytest -q

ci:
	dagger call checks $(DAGGER_CA)
	dagger call test $(DAGGER_CA)

build:
	dagger call build

build-viewer:
	dagger call build-viewer $(DAGGER_CA)

scan:
	dagger call scan-json $(DAGGER_CA)

# Publishing is manual and requires DOCKERHUB_USERNAME/DOCKERHUB_TOKEN env vars.
publish:
	dagger call publish-docker --component wrapper \
	  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN $(DAGGER_CA)

compose-up:
	cd .docker && docker compose up -d

compose-test:
	dagger call compose-test

compose-down:
	cd .docker && docker compose down -v

helm-lint:
	helm lint charts/htrflow-batch

docs-serve:
	uvx zensical serve

docs-build:
	uvx zensical build --clean

# PoC: build + push the wrapper image into the in-cluster k3s registry
poc-push:
	docker build -f .docker/htrflow-batch.dockerfile -t 127.0.0.1:30500/htrflow-batch:dev .
	docker push 127.0.0.1:30500/htrflow-batch:dev

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf site/
```

- [ ] **Step 2: workflows** — `ci.yml` copied from `~/ape-mcp/.github/workflows/ci.yml` with only the module-independent parts kept (checkout + dagger `checks` + dagger `test`, same pinned action SHAs — copy them from the ape-mcp file verbatim). `docs.yml`: copy ape-mcp's verbatim (it is already `workflow_dispatch`-only with the private-repo comment). `publish.yml`: copy ape-mcp's, change the dagger call args to `publish-docker --component wrapper --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN`, keep `workflow_dispatch` trigger only (drop any tag trigger for now).

- [ ] **Step 3: Verify**

Run: `make test` → `47 passed`. Run `make ci` → both dagger calls green. Run `make helm-lint` → passes. Validate workflow syntax: `python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/ci.yml','.github/workflows/docs.yml','.github/workflows/publish.yml']]; print('yaml ok')"` → `yaml ok`.

- [ ] **Step 4: Commit**

```bash
git add Makefile .github && git commit -m "Add Makefile and GitHub workflows fronting dagger"
```

---

### Task 11: Docs site — skeleton, index, Getting Started

**Files:**
- Create: `zensical.toml`, `docs/index.md`, `docs/getting-started/index.md`, `docs/getting-started/deploy.md`, `docs/getting-started/run-a-volume.md`, `docs/getting-started/viewing.md`
- Modify: `.gitignore` (ensure `site/`)

**Interfaces:**
- Consumes: DESIGN.md §1–2 (intro), §13–14 environment notes, `charts/htrflow-batch/README.md` (Task 9).
- Produces: zensical nav consumed by Tasks 12–13 (they append their sections to the nav in `zensical.toml`).

- [ ] **Step 1: zensical.toml** — copy `~/ape-mcp/zensical.toml` and adapt: `site_name = "htrflow-batch"`, description "Kueue-gated batch HTR around the htrflow image — streaming per-page results to S3 with IIIF viewer output.", repo `carpelan/test` (placeholder until the real home), and the nav for THIS task only:

```toml
nav = [
  { "htrflow-batch" = ["index.md"] },
  { "Getting Started" = [
    {"Prerequisites" = "getting-started/index.md"},
    {"Deploy" = "getting-started/deploy.md"},
    {"Run a Volume" = "getting-started/run-a-volume.md"},
    {"Viewing Results" = "getting-started/viewing.md"},
  ]},
]
```
Keep the theme/plugins blocks from ape-mcp as-is.

- [ ] **Step 2: Write the pages.** Content sources (this is a *move with copy-editing*, not a rewrite — prefer DESIGN.md's exact wording, fix cross-references to point at future site pages):
  - `index.md`: what the system is (DESIGN §1 goal + the phase-1 summary), current status (PoC validated 2026-07-27/28, scale test pending), links to How it Works / Roadmap.
  - `getting-started/index.md`: prerequisites — k8s with Kueue CRDs; for the bare-k3s PoC path: the host gotchas from DESIGN §13 tail + §14 (inotify sysctls, node-ip pin, eviction-hard absolute thresholds — copy the exact config snippets).
  - `getting-started/deploy.md`: helm install paths — production-shaped (`helm install htr charts/htrflow-batch -n htr-batch --create-namespace` + values to set) and PoC replay (the full `--set devStack...` command from Task 9 step 4, plus `make poc-push` for the image, plus bucket policy/CORS replay commands from the old k8s/README — now in the chart README; link it).
  - `getting-started/run-a-volume.md`: the Job contract — suspend:true + queue label, env table (copy the §5.1 table from DESIGN.md verbatim), exit codes 0/13/1, `MAX_PAGES`, and the compose alternative (`make compose-up`, wrapper service, `MAX_PAGES=1` note).
  - `getting-started/viewing.md`: viewer URL scheme (`/uv.html#?manifest=...`), the `/` redirect, ssh -L tunnel instructions with the exact two-port command, and the localhost-URL caveat from DESIGN §14.

- [ ] **Step 3: Verify build**

Run: `uvx zensical build --clean`
Expected: exit 0, `site/` produced, no missing-page warnings for nav entries.

- [ ] **Step 4: Commit**

```bash
git add zensical.toml docs .gitignore && git commit -m "Add docs site skeleton with getting-started section"
```

---

### Task 12: Docs site — How it Works

**Files:**
- Create: `docs/how-it-works/architecture.md`, `docs/how-it-works/decision-log.md`, `docs/how-it-works/wrapper.md`, `docs/how-it-works/memory-budget.md`, `docs/how-it-works/failure-handling.md`
- Modify: `zensical.toml` (append nav section)

**Interfaces:**
- Consumes: DESIGN.md §3–§7 + the D1–D19 decision table.
- Produces: pages Task 13 links to.

- [ ] **Step 1: Move content.** Mapping (move DESIGN.md text bodily; keep the mermaid diagrams — zensical/Material renders ```mermaid fences):
  - `architecture.md` ← DESIGN §3 (architecture + all three mermaid diagrams) + §4 (streaming design D16, downloader/consumer/queue detail, gpu_stall_seconds).
  - `decision-log.md` ← the D1–D19 table + any per-decision elaboration paragraphs from §2. Keep the table intact — it is the index into everything else; convert `§N` references to page links.
  - `wrapper.md` ← §5 complete: 5.1 env contract table, 5.2 queue integration, 5.3 Job spec/exit codes/podFailurePolicy note (including "now safe to wire after commit af8df6a"), 5.4 output contract, 5.5 stages, 5.6 model handling, 5.7 pipeline ConfigMaps + D19 viewer-manifest emission incl. the UV4-fork gotchas block (search-service stub, thumbnails, uv.html patch, coordinate fix).
  - `memory-budget.md` ← §6 (tmpfs accounting incl. the WORKDIR_PATH note).
  - `failure-handling.md` ← §7 (failure classes, idempotency/resume, verify gate D8).

- [ ] **Step 2: Append to nav in zensical.toml**

```toml
  { "How it Works" = [
    {"Architecture" = "how-it-works/architecture.md"},
    {"Decision Log" = "how-it-works/decision-log.md"},
    {"The Wrapper" = "how-it-works/wrapper.md"},
    {"Memory Budget" = "how-it-works/memory-budget.md"},
    {"Failure Handling" = "how-it-works/failure-handling.md"},
  ]},
```

- [ ] **Step 3: Verify build + no content loss**

Run: `uvx zensical build --clean` → exit 0.
Content-loss guard: for each of §3, §4, §5.1–5.7, §6, §7 in DESIGN.md, pick one distinctive sentence and grep it in `docs/how-it-works/` — every probe must hit:
```bash
grep -rl "gpu_stall_seconds" docs/how-it-works/ && grep -rl "immutable" docs/how-it-works/ && grep -rl "tmpfs" docs/how-it-works/
```
Expected: hits for all.

- [ ] **Step 4: Commit**

```bash
git add docs zensical.toml && git commit -m "Docs: how-it-works section from design doc sections 3-7"
```

---

### Task 13: Docs site — Roadmap + Development; delete DESIGN.md; README

**Files:**
- Create: `docs/roadmap/phase-2-cache.md`, `docs/roadmap/evolution.md`, `docs/roadmap/open-items.md`, `docs/development/index.md`, `docs/development/testing.md`, `docs/development/ci.md`, `docs/development/security.md`, `docs/development/deployment.md`, `docs/development/test-log.md`
- Modify: `zensical.toml`, `README.md`
- Delete (gated): `DESIGN.md`, `.superpowers/sdd/`

**Interfaces:**
- Consumes: DESIGN.md §8–§14.

- [ ] **Step 1: Move content:**
  - `roadmap/phase-2-cache.md` ← §11 **complete and verbatim-depth** (11.1 nginx variant, 11.2 Fluid/AlluxioRuntime/WebUFS with all subsections 11.2.1–11.2.7 including the component contracts code blocks and the adoption-spike gate). This is the content the user explicitly required never to lose.
  - `roadmap/evolution.md` ← §12 (frontend/htrq-api, CRD guidance, other items).
  - `roadmap/open-items.md` ← §10 (D6, D13, D14, D15, htrq CLI; note scale-test as the active next step).
  - `development/index.md` ← dev setup (uv, `make install`, TDD norms — 2 short paragraphs, new text) + §9 testing/acceptance table.
  - `development/testing.md` ← §9 detail + how to run: `make test`, `dagger call test`, compose smoke.
  - `development/ci.md` ← new text (short): dagger functions table (checks/test/build/build-viewer/scan/publish/compose), the `--ca-bundle` firewall note, Makefile targets, workflows.
  - `development/security.md` ← §8 + D14 open (NetworkPolicy), RustFS PoC-creds caveat.
  - `development/deployment.md` ← image build/publish paths (dagger + `make poc-push`), chart release notes, registry defaults.
  - `development/test-log.md` ← §13 AND §14 **verbatim** (they are the validation record).

- [ ] **Step 2: Append nav**

```toml
  { "Roadmap" = [
    {"Phase 2: Cache Layer" = "roadmap/phase-2-cache.md"},
    {"Evolution" = "roadmap/evolution.md"},
    {"Open Items" = "roadmap/open-items.md"},
  ]},
  { "Development" = [
    {"Setup" = "development/index.md"},
    {"Testing" = "development/testing.md"},
    {"CI" = "development/ci.md"},
    {"Security" = "development/security.md"},
    {"Deployment" = "development/deployment.md"},
    {"Test Log" = "development/test-log.md"},
  ]},
```

- [ ] **Step 3: DESIGN.md deletion — gated checklist.** Confirm ALL in the task report before `git rm`:
1. Every DESIGN.md top-level section §1–§14 has a named destination page (list the mapping §→file explicitly in the report).
2. Distinctive-sentence probes: `grep -c "WebUFS" docs/roadmap/phase-2-cache.md` ≥ 5; `grep -c "eviction-hard" docs/development/test-log.md` ≥ 1; `grep -c "D17" docs/how-it-works/decision-log.md` ≥ 1; `grep "gpu_stall_seconds: 0.0" docs/development/test-log.md` hits.
3. `uvx zensical build --clean` green with the full nav.
4. No repo file references DESIGN.md anymore: `grep -rn 'DESIGN.md' --include='*.py' --include='*.go' --include='*.yaml' --include='*.md' . | grep -v '.git/\|docs/superpowers/\|test-log'` — fix hits (wrapper docstrings reference "DESIGN.md D16/§5.7" — update those strings to "docs: how-it-works/wrapper" wording; tests must stay green after).

Then:
```bash
git rm DESIGN.md
rm -rf .superpowers/sdd
```
(`.superpowers/sdd` is untracked scratch — plain rm; its parked finding was resolved in commit af8df6a and its history is in the test log.)

- [ ] **Step 4: Rewrite README.md** (short, ape-mcp style): name + one-paragraph description, badges omitted, quickstart (`make install && make test`, `make compose-up`, helm pointer), docs pointer ("full documentation in docs/ — `make docs-serve`"), license/ownership line if the old README had one.

- [ ] **Step 5: Final verify**

```bash
cd wrapper && uv run --extra dev pytest -q     # 47 passed (docstring edits can't break tests, prove it)
cd .. && uvx zensical build --clean            # site green
make ci                                        # dagger checks + test green
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Docs: roadmap and development sections; retire DESIGN.md into the site"
```

---

## Self-review notes

- Spec coverage: layout moves (T1), ruff (T2 — implied by dagger checks), dagger full port (T3–T6), compose (T7), helm (T8–T9), Makefile/workflows (T10), docs split (T11–T13), DESIGN.md/k8s deletions gated (T13/T9), README (T13), `.superpowers/sdd` cleanup (T13). Out-of-scope items from spec untouched.
- The firewall probe is front-loaded (T3 step 6) and every network-touching function carries `caBundle`.
- Type consistency: `HtrflowBatch` receiver everywhere; `withCaBundle(container, caBundle)` signature used by T4/T5; values schema defined once (T8) and consumed (T9).
