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
	DefaultRegistry       = "docker.io"
	DefaultImageRepo      = "riksarkivet/htrflow-batch"
	DefaultViewerRepo     = "riksarkivet/htrflow-batch-viewer"
	DefaultReconcilerRepo = "riksarkivet/htrflow-reconciler"
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

// WrapperPackage is the [project].name of the wrapper workspace member
// (packages/wrapper) — used to scope uv --package selections.
const WrapperPackage = "htrflow-batch-wrapper"

// buildWithUv creates a dev container with uv and the workspace's deps synced.
// The repo is a uv workspace (root pyproject.toml + uv.lock, members under
// packages/), so the sync runs at the workspace root with --all-packages.
// Note: every later `uv run` must pass --no-sync, or uv would re-resolve the
// default (root + dev) environment and prune the workspace members back out.
func (m *HtrflowBatch) buildWithUv(ctx context.Context, source *dagger.Directory, caBundle *dagger.File) (*dagger.Container, error) {
	container := dag.Container().
		From("python:3.13-slim").
		WithDirectory("/app", source, dagger.ContainerWithDirectoryOpts{
			// scripts/ is linted too (audit T11); docs/ is excluded by the
			// root ruff config and not needed here.
			Include: []string{"pyproject.toml", "uv.lock", "packages/", "scripts/"},
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
