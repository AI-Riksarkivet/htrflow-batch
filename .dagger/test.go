package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// withTestTools puts the CLIs the suite shells out to on the PATH of
// the pytest container. Without them the tests that use them do not fail —
// they SKIP, which is worse: `test_apply.py` stopped exercising the commit
// provenance it records, and `test_render.py` stopped validating the
// rendered manifests, and both went on reporting green (audit T4/T5).
//
// kubeconform and helm are static Go binaries lifted straight out of the
// digest-pinned images CheckChart already uses, so the suite validates with
// the same versions the chart render is checked with, and there is no second
// version to keep in step. The Kyverno CLI is lifted the same way, for
// `test_policy_admission.py`: without it every policy bypass test skips. git has no such image: it comes from Debian's
// archive, the unpinned input here, because a repo that cannot run `git
// init` cannot test what `git rev-parse` returns. jq and make come the same
// way, for `test_make_cluster_targets.py`: it drives the Makefile's cluster
// targets and the scripts they call, which read kubectl's JSON with jq.
func (m *HtrflowBatch) withTestTools(container *dagger.Container) *dagger.Container {
	return container.
		WithExec([]string{"sh", "-c",
			"apt-get update -qq && apt-get install -y --no-install-recommends git jq make " +
				"&& rm -rf /var/lib/apt/lists/*"}).
		WithFile("/usr/local/bin/kubeconform", dag.Container().From(kubeconformImage).File("/kubeconform")).
		WithFile("/usr/local/bin/helm", dag.Container().From(helmImage).File("/usr/bin/helm")).
		WithFile("/usr/local/bin/kyverno", dag.Container().From(kyvernoCliImage).File("/ko-app/kubectl-kyverno"))
}

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
	return m.withTestTools(container).
		WithExec([]string{"sh", "-c", testAndAuditSkips}).
		Stdout(ctx)
}

// testAndAuditSkips runs the suite and then fails the run on any skip but
// the expected one (audit 0923 T-1). A `skipif` on a missing binary turns
// into a green run that tested nothing: the tool guard in test_render.py
// checks withTestTools for the tools it knows, and this catches the one it
// does not know yet. The real-driver test is the one expected skip -- it
// needs the htrflow runtime and runs in the wrapper image instead
// (TestDriver). `-rs` puts every skip and its reason at the end of the log;
// the log is printed first either way, so a failure keeps its traceback.
const testAndAuditSkips = `uv run --no-sync pytest --tb=short -q -rs > /tmp/pytest.log 2>&1
rc=$?
cat /tmp/pytest.log
[ "$rc" -eq 0 ] || exit "$rc"
if grep '^SKIPPED' /tmp/pytest.log | grep -v '^SKIPPED \[1\] packages/wrapper/tests/test_driver_real\.py:'; then
  echo "unexpectedSkips: the skips above mean a tool the suite shells out to is missing from withTestTools, or a test skipped for another reason" >&2
  exit 1
fi`

// TestDriver runs the Level 0 htrflow API pin (audit T4) — the real
// Pipeline.from_config / Export / auto_import / run on a one-page CPU
// fixture, packages/wrapper/tests/test_driver_real.py — inside the wrapper
// image Build produces. Not part of Checks: it needs the ~10 GB wrapper
// image. ci.yml runs it in the amd64 scan job, sharing that job's build, and
// publish-docker runs the same test on the image it pushes. `make test-driver-real` is the local twin against a
// locally built image. pytest is installed into the image's venv at the
// version uv.lock pins; the test file is mounted alone (no conftest: moto
// is not in the image).
func (m *HtrflowBatch) TestDriver(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// HTRFLOW_BASE_REVISION build arg for the wrapper image (see BuildWrapper)
	// +optional
	baseRevision string,
	// CA bundle for TLS-intercepting networks (pytest install)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	image, err := m.BuildWrapper(ctx, source, baseRevision, "", "", "")
	if err != nil {
		return "", fmt.Errorf("wrapper build failed before the driver test: %w", err)
	}
	return m.driverTest(ctx, image, source, caBundle)
}

// driverTest runs test_driver_real.py inside a given wrapper image. It is
// TestDriver's body, and what PublishDocker runs on the very container it is
// about to push (finding 3104): the pin test used to run only against an
// arm64 image built in ci.yml, never against the amd64 image that ships.
func (m *HtrflowBatch) driverTest(
	ctx context.Context,
	image *dagger.Container,
	source *dagger.Directory,
	caBundle *dagger.File,
) (string, error) {
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
