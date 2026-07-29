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
		WithExec([]string{"uv", "run", "--no-sync", "pytest", "packages/wrapper/tests", "--tb=short", "-q"}).
		Stdout(ctx)
}
