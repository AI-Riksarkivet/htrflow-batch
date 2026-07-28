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
