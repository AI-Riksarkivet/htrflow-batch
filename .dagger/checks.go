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

// Checks runs code-quality checks on the workspace packages (ruff format +
// lint, from the workspace root) and lints the Helm chart.
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

	// Helm chart lint (alpine/helm has no TLS needs — no caBundle wiring)
	_, err = dag.Container().
		From("alpine/helm:latest").
		WithDirectory("/chart", source.Directory("charts/htrflow-batch")).
		WithExec([]string{"helm", "lint", "/chart"}).
		Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("helm lint failed: %w", err)
	}

	return "All checks passed", nil
}
