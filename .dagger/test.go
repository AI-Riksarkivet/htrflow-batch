package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// Test runs the workspace test suite (pytest, no GPU required). The bare
// invocation picks up the root pyproject's testpaths, which cover every
// workspace member's tests.
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
		WithExec([]string{"uv", "run", "--no-sync", "pytest", "--tb=short", "-q"}).
		Stdout(ctx)
}
