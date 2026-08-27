package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// ruff and ty come from the workspace venv (`uv run --no-sync`), never `uvx`:
// uvx resolves the newest release every run while uv.lock pins the versions
// the repo is checked with, and the two drifted (audit T1).
var (
	ruffFormatCheckCmd = []string{"uv", "run", "--no-sync", "ruff", "format", "--check", "."}
	ruffCheckCmd       = []string{"uv", "run", "--no-sync", "ruff", "check", "."}
	tyCheckCmd         = []string{
		"uv", "run", "--no-sync", "ty", "check",
		"packages/wrapper/src", "packages/reconciler/src",
	}
)

// Lint runs ruff format --check and ruff check over the workspace (packages
// and scripts) with the locked ruff.
func (m *HtrflowBatch) Lint(
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
		return "", fmt.Errorf("ruff failed: %w", err)
	}
	return "ruff passed", nil
}

// Typecheck runs ty over both workspace members' sources with the locked ty.
// The shared venv resolves cross-member imports, so one invocation from the
// workspace root covers the wrapper and the reconciler.
func (m *HtrflowBatch) Typecheck(
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
	_, err = container.WithExec(tyCheckCmd).Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("ty failed: %w", err)
	}
	return "ty passed", nil
}

// Checks is what CI gates on: ruff (format + lint), ty and the Helm chart
// lint — the same set `make ci` runs (audit T1/T2).
func (m *HtrflowBatch) Checks(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks
	// +optional
	caBundle *dagger.File,
) (string, error) {
	if _, err := m.Lint(ctx, source, caBundle); err != nil {
		return "", err
	}
	if _, err := m.Typecheck(ctx, source, caBundle); err != nil {
		return "", err
	}

	// Helm chart lint (alpine/helm has no TLS needs — no caBundle wiring)
	_, err := dag.Container().
		From("alpine/helm:latest").
		WithDirectory("/chart", source.Directory("charts/htrflow-batch")).
		WithExec([]string{"helm", "lint", "/chart"}).
		Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("helm lint failed: %w", err)
	}

	return "All checks passed", nil
}
