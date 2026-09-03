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
	DefaultWebRepo    = "riksarkivet/htrflow-web"
)

// Every image the pipeline pulls, pinned by tag AND multi-arch index digest
// (audit T9/S7): a floating tag made `checks` depend on whatever alpine/helm
// or uv shipped that morning. Renovate tracks these lines (renovate.json,
// customManagers "dagger-images"); refresh by hand with
// `docker buildx imagetools inspect <ref>`.
const (
	pythonImage = "python:3.13-slim@sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4"
	uvImage     = "ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d"
	bunImage    = "oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4"
	// vitest's jsdom needs undici's markAsUncloneable (node >= 22). The UV4
	// build's own node (20, its lockfile's) is pinned in
	// .docker/htrflow-web.dockerfile, not here.
	frontendNodeImage = "node:22-bookworm@sha256:8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d"
	helmImage         = "alpine/helm:3.19.0@sha256:aef9b56f64e866207d9591d0abd8f6d767b36aadd12edf68f8a719716d9d29c9"
	kubeconformImage  = "ghcr.io/yannh/kubeconform:v0.7.0@sha256:85dbef6b4b312b99133decc9c6fc9495e9fc5f92293d4ff3b7e1b30f5611823c"
	trivyImage        = "aquasec/trivy:0.65.0@sha256:a22415a38938a56c379387a8163fcb0ce38b10ace73e593475d3658d578b2436"
	curlImage         = "curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
)

// withUv adds uv/uvx binaries to a container (development/CI tasks only)
func (m *HtrflowBatch) withUv(container *dagger.Container) *dagger.Container {
	uv := dag.Container().From(uvImage)
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
		WithEnvVariable("UV_NATIVE_TLS", "true"). // uv <0.9 name, still honoured
		WithEnvVariable("UV_SYSTEM_CERTS", "true").
		WithEnvVariable("NODE_EXTRA_CA_CERTS", "/etc/ssl/certs/corp-ca.crt")
}

// WrapperPackage is the [project].name of the wrapper workspace member
// (packages/wrapper) — used to scope uv --package selections.
const WrapperPackage = "htrflow-batch-wrapper"

// repoExclude is what every CI container that mounts the *whole* repo
// leaves out. It replaces the old per-container `Include` allow-lists
// (B63 Task 20A): an allow-list silently hides new paths from every test
// that runs inside these containers — twice a test passed only because its
// fixture wasn't on the list, which an exclude-list can't do (a new file is
// visible by default). Derived from .gitignore plus paths that are
// obviously irrelevant to CI (VCS/worktree plumbing, other packages'
// dependency/build caches). Keep in sync with .gitignore by hand; there is
// no tooling that parses one into the other.
var repoExclude = []string{
	// VCS / worktrees — .worktrees holds sibling checkouts of this same
	// repo (this file may itself be running from inside one); never a CI
	// input.
	".git", ".worktrees",
	// .gitignore
	".superpowers", "**/__pycache__", "**/*.pyc", ".venv", "site", ".cache",
	".env", ".claude/worktrees",
	// not gitignored, but irrelevant to every container built here
	"**/node_modules", "**/.pytest_cache",
	"frontend/coverage", "frontend/.svelte-kit", "frontend/dist", "frontend/build",
}

// buildWithUv creates a dev container with uv and the workspace's deps synced.
// The repo is a uv workspace (root pyproject.toml + uv.lock, members under
// packages/), so the sync runs at the workspace root with --all-packages.
// Note: every later `uv run` must pass --no-sync, or uv would re-resolve the
// default (root + dev) environment and prune the workspace members back out.
func (m *HtrflowBatch) buildWithUv(ctx context.Context, source *dagger.Directory, caBundle *dagger.File) (*dagger.Container, error) {
	container := dag.Container().
		From(pythonImage).
		WithDirectory("/app", source, dagger.ContainerWithDirectoryOpts{
			// Whole repo minus repoExclude (B63 Task 20A) — see its comment
			// for why this replaced the old per-path Include allow-list.
			Exclude: repoExclude,
		}).
		WithWorkdir("/app")
	container = m.withUv(container)
	container = m.withCaBundle(container, caBundle)
	container = container.WithExec([]string{"uv", "sync", "--no-cache", "--frozen", "--all-packages"})
	return container, nil
}

// getVersion reads the wrapper package version
func (m *HtrflowBatch) getVersion(ctx context.Context, source *dagger.Directory, caBundle *dagger.File) (string, error) {
	container, err := m.buildWithUv(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	version, err := container.WithExec([]string{"uv", "version", "--short", "--package", WrapperPackage}).Stdout(ctx)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(version), nil
}
