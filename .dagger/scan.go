package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// scanImage runs Trivy against an already-built container. ignoreUnfixed
// skips findings the distribution has no fix for (Debian will_not_fix
// entries in the slim base): a gate on those can never go green and is
// what `make scan-web` already does.
func (m *HtrflowBatch) scanImage(
	ctx context.Context,
	container *dagger.Container,
	severity string,
	format string,
	exitCode int,
	ignoreUnfixed bool,
	caBundle *dagger.File,
) (string, error) {
	output, err := m.trivy(container, caBundle).
		WithExec(trivyArgs(severity, format, exitCode, ignoreUnfixed)).
		Stdout(ctx)
	if err != nil {
		if output == "" {
			return "", fmt.Errorf("trivy scan failed: %w", err)
		}
		return output, fmt.Errorf("vulnerabilities found: %w", err)
	}
	return output, nil
}

// trivy is the digest-pinned Trivy container with the image under scan
// mounted as a tarball at /image.tar.
func (m *HtrflowBatch) trivy(container *dagger.Container, caBundle *dagger.File) *dagger.Container {
	return m.withCaBundle(dag.Container().From(trivyImage), caBundle).
		WithMountedFile("/image.tar", container.AsTarball())
}

// trivyArgs is the one Trivy command line the gates and the report share.
func trivyArgs(severity string, format string, exitCode int, ignoreUnfixed bool) []string {
	args := []string{
		"trivy", "image", "--input", "/image.tar",
		"--severity", severity, "--format", format,
		"--exit-code", fmt.Sprintf("%d", exitCode),
		"--skip-version-check",
	}
	if ignoreUnfixed {
		args = append(args, "--ignore-unfixed")
	}
	return args
}

// ScanSarif writes Trivy's findings for one image as SARIF, for the GitHub
// Security tab (security.yml). It is a report, not a gate: it never fails on
// findings, and it keeps the unfixed ones the gates skip, because a CVE with
// no fix yet is still worth seeing. The gates stay Scan and ScanWeb.
func (m *HtrflowBatch) ScanSarif(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// Which image to scan: "wrapper" or "web"
	image string,
	// +default="CRITICAL,HIGH"
	severity string,
	// CA bundle for TLS-intercepting networks (Trivy DB download)
	// +optional
	caBundle *dagger.File,
) (*dagger.File, error) {
	var container *dagger.Container
	var err error
	switch image {
	case "wrapper":
		container, err = m.BuildWrapper(ctx, source, "", "", "")
	case "web":
		container, err = m.BuildWeb(ctx, source, caBundle, "")
	default:
		return nil, fmt.Errorf("image must be \"wrapper\" or \"web\", got %q", image)
	}
	if err != nil {
		return nil, fmt.Errorf("%s build failed before scanning: %w", image, err)
	}
	args := append(trivyArgs(severity, "sarif", 0, false), "--output", "/trivy.sarif")
	scanned, err := m.trivy(container, caBundle).WithExec(args).Sync(ctx)
	if err != nil {
		return nil, fmt.Errorf("trivy scan failed: %w", err)
	}
	return scanned.File("/trivy.sarif"), nil
}

// Scan runs Trivy against the wrapper image. The CUDA/ubuntu base will never be
// alpine-clean; default severity gate is CRITICAL,HIGH. ci.yml runs it with
// --severity CRITICAL (blocking) on pushes to main and manual runs — the
// ~10 GB base is too much for every pull request.
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
	// Skip findings without a distribution fix (will_not_fix); false gates on everything
	// +default=true
	ignoreUnfixed bool,
	// CA bundle for TLS-intercepting networks (Trivy DB download)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	container, err := m.BuildWrapper(ctx, source, "", "", "")
	if err != nil {
		return "", fmt.Errorf("build failed before scanning: %w", err)
	}
	return m.scanImage(ctx, container, severity, format, exitCode, ignoreUnfixed, caBundle)
}

// ScanWeb runs Trivy against the web image. Unlike the wrapper this one
// has a slim debian base with no CUDA stack, so a clean gate is a realistic
// expectation here; ci.yml runs it on every push and pull request with
// --severity CRITICAL.
func (m *HtrflowBatch) ScanWeb(
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
	// Skip findings without a distribution fix (will_not_fix); false gates on everything
	// +default=true
	ignoreUnfixed bool,
	// CA bundle for TLS-intercepting networks (Trivy DB download)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	container, err := m.BuildWeb(ctx, source, caBundle, "")
	if err != nil {
		return "", fmt.Errorf("web build failed before scanning: %w", err)
	}
	return m.scanImage(ctx, container, severity, format, exitCode, ignoreUnfixed, caBundle)
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
	return m.Scan(ctx, source, severity, "json", 0, false, caBundle)
}
