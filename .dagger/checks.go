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

// frontend is the campaign browser's build container: node (digest-pinned)
// with the bun binary from the digest-pinned oven/bun image, and the
// lockfile-exact dependency set installed. Node is deliberate: `bun run
// <script>` executes vitest/vite under node when node is on PATH, which is
// what every developer host does — under the bun runtime itself vitest
// cannot see zod's named `z` export (verified: `bun --bun run test` fails
// on status.test.ts), and oven/bun ships no real node. Shared by
// CheckFrontend and BuildViewer so the image ships exactly what CI checked.
func (m *HtrflowBatch) frontend(source *dagger.Directory, caBundle *dagger.File) *dagger.Container {
	bun := dag.Container().From(bunImage).File("/usr/local/bin/bun")
	spa := dag.Container().
		From(nodeImage).
		WithFile("/usr/local/bin/bun", bun).
		WithDirectory("/app", source.Directory("frontend"), dagger.ContainerWithDirectoryOpts{
			Exclude: []string{"node_modules", "dist"},
		}).
		WithWorkdir("/app")
	spa = m.withCaBundle(spa, caBundle)
	return spa.WithExec([]string{"bun", "install", "--frozen-lockfile"})
}

// CheckFrontend runs the campaign browser's own gates — svelte-check, vitest
// and a production build — which CI never ran before (audit T2/F15).
func (m *HtrflowBatch) CheckFrontend(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks (bun install)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	_, err := m.frontend(source, caBundle).
		WithExec([]string{"bun", "run", "check"}).
		WithExec([]string{"bun", "run", "test"}).
		WithExec([]string{"bun", "run", "build"}).
		Sync(ctx)
	if err != nil {
		return "", fmt.Errorf("frontend checks failed: %w", err)
	}
	return "frontend passed", nil
}

// Chart render inputs: defaults, and ci/full-values.yaml with every optional
// feature on (no cluster lookups). Release/namespace mirror .env.example.
var chartRenders = []struct{ name, values string }{
	{"default", ""},
	{"full", "/chart/ci/full-values.yaml"},
}

// CheckChart lints and renders the Helm chart on defaults and on
// ci/full-values.yaml, then validates the rendered manifests with kubeconform
// (-strict, unknown CRD kinds skipped). `make helm-template` is the local
// twin (audit T2/T8).
func (m *HtrflowBatch) CheckChart(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks (kubeconform schema downloads)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	helm := dag.Container().
		From(helmImage).
		WithDirectory("/chart", source.Directory("charts/htrflow-batch")).
		WithExec([]string{"mkdir", "-p", "/out"})
	for _, r := range chartRenders {
		lint := []string{"helm", "lint", "/chart"}
		template := []string{"helm", "template", "htr", "/chart", "-n", "htr-batch"}
		if r.values != "" {
			lint = append(lint, "-f", r.values)
			template = append(template, "-f", r.values)
		}
		helm = helm.
			WithExec(lint).
			WithExec(template, dagger.ContainerWithExecOpts{RedirectStdout: "/out/" + r.name + ".yaml"})
	}
	if _, err := helm.Sync(ctx); err != nil {
		return "", fmt.Errorf("helm lint/template failed: %w", err)
	}

	kubeconform := dag.Container().From(kubeconformImage)
	kubeconform = m.withCaBundle(kubeconform, caBundle)
	args := []string{"/kubeconform", "-strict", "-ignore-missing-schemas", "-summary"}
	for _, r := range chartRenders {
		path := "/render/" + r.name + ".yaml"
		kubeconform = kubeconform.WithFile(path, helm.File("/out/"+r.name+".yaml"))
		args = append(args, path)
	}
	out, err := kubeconform.WithExec(args).Stdout(ctx)
	if err != nil {
		return "", fmt.Errorf("kubeconform failed: %w", err)
	}
	return "chart passed\n" + out, nil
}

// Checks is what CI gates on: ruff (format + lint), ty, the campaign browser
// (check/test/build) and the chart (lint/render/kubeconform) — the same set
// `make ci` runs (audit T1/T2/X11).
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
	if _, err := m.CheckFrontend(ctx, source, caBundle); err != nil {
		return "", err
	}
	if _, err := m.CheckChart(ctx, source, caBundle); err != nil {
		return "", err
	}
	return "All checks passed", nil
}
