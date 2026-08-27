package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
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

// TestDriver runs the Level 0 htrflow API pin (audit T4) — the real
// Pipeline.from_config / Export / auto_import / run on a one-page CPU
// fixture, packages/wrapper/tests/test_driver_real.py — inside the wrapper
// image Build produces. Opt-in and not part of Checks: it needs the ~10 GB
// wrapper image. `make test-driver-real` is the local twin against a
// locally built image. pytest is installed into the image's venv at the
// version uv.lock pins; the test file is mounted alone (no conftest: moto
// is not in the image).
func (m *HtrflowBatch) TestDriver(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// HTRFLOW_BASE_REVISION build arg for the wrapper image (see Build)
	// +optional
	baseRevision string,
	// CA bundle for TLS-intercepting networks (pytest install)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	image, err := m.Build(ctx, source, baseRevision)
	if err != nil {
		return "", fmt.Errorf("wrapper build failed before the driver test: %w", err)
	}
	container := image.
		WithUser("0"). // the venv is root-owned; the test runs as root too
		WithFile("/tmp/uv.lock", source.File("uv.lock")).
		WithFile(
			"/driver-tests/test_driver_real.py",
			source.File("packages/wrapper/tests/test_driver_real.py"),
		).
		WithEnvVariable("CUDA_VISIBLE_DEVICES", "").
		WithEnvVariable("HF_HUB_OFFLINE", "1").
		WithWorkdir("/tmp")
	container = m.withCaBundle(container, caBundle)
	return container.
		WithExec([]string{"sh", "-c",
			`v=$(grep -A1 '^name = "pytest"$' /tmp/uv.lock | sed -n 's/^version = "\(.*\)"/\1/p') && ` +
				`uv pip install --python /app/.venv/bin/python --no-cache "pytest==$v"`}).
		WithExec([]string{
			"/app/.venv/bin/python", "-m", "pytest", "-m", "htrflow", "-q",
			"-p", "no:cacheprovider",
			"-o", "markers=htrflow: needs the htrflow runtime", // no pyproject in the image
			"/driver-tests",
		}).
		Stdout(ctx)
}
