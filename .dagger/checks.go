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
		"packages/wrapper/src", "packages/converter/src", "packages/web/src",
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

// Typecheck runs ty over the workspace members' sources with the locked ty.
// The shared venv resolves cross-member imports, so one invocation from the
// workspace root covers the wrapper, the converter and the read API.
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
// on status.test.ts), and oven/bun ships no real node. `bun run build` is
// checked here and run again by the web image's own bun stage
// (.docker/htrflow-web.dockerfile).
func (m *HtrflowBatch) frontend(source *dagger.Directory, caBundle *dagger.File) *dagger.Container {
	bun := dag.Container().From(bunImage).File("/usr/local/bin/bun")
	spa := dag.Container().
		From(frontendNodeImage).
		WithFile("/usr/local/bin/bun", bun).
		WithDirectory("/app", source.Directory("frontend"), dagger.ContainerWithDirectoryOpts{
			// Same allow-list-vs-exclude-list reasoning as repoExclude
			// (main.go, B63 Task 20A), applied to the frontend/ subtree
			// this container mounts.
			Exclude: []string{"node_modules", "dist", "coverage", ".svelte-kit", "build"},
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

// chartRender is one helm lint + helm template invocation: values files
// (relative to the chart's mounted root, later ones winning) plus --set
// overrides. The inputs are the chart's own ci/ files -- the one copy
// test_chart_render.py and `make helm-template` render with too -- and
// which renders the chart refuses is test_chart_render.py's guard table,
// which `dagger call test` runs; neither is repeated here.
type chartRender struct {
	name   string
	values []string
	sets   []string
}

// The prod chart: its defaults with the placeholders it refuses to render
// without (and the two intents an operator has to state: a public web
// front, no admission policy), every optional feature, and the production
// profile completed with the site values it leaves to the operator.
var prodChartRenders = []chartRender{
	{name: "default", values: []string{"ci/default-values.yaml"}, sets: []string{
		"network.web.allowPublicIngress=true",
		"security.policies.allowDisabled=true",
	}},
	{name: "full", values: []string{"ci/full-values.yaml"}},
	{name: "prod", values: []string{"values-prod.yaml", "ci/default-values.yaml", "ci/prod-values.yaml"}},
}

// The devstack chart's own values are all `enabled: false` by default, so
// "default" renders nothing (still worth lint+template-ing); "full" turns on
// RustFS, the registry and the nvidia device plugin.
var devstackChartRenders = []chartRender{
	{name: "default"},
	{name: "full", values: []string{"ci/full-values.yaml"}},
}

// CheckChart lints and renders both Helm charts — the prod chart
// (charts/htrflow-batch) on its digest/CIDR-complete defaults and on
// ci/full-values.yaml, and the PoC-only devstack chart
// (charts/htrflow-devstack) the same way — then asserts on the prod chart's
// renders, then validates every render, plus the converter's Job/ConfigMap
// manifest skeletons
// (packages/converter/src/htrflow_converter/manifests, B63 Task 9), with
// kubeconform (-strict, unknown CRD kinds skipped). `make helm-template` is
// the local twin (audit T2/T8).
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
		WithDirectory("/chart-devstack", source.Directory("charts/htrflow-devstack")).
		WithExec([]string{"mkdir", "-p", "/out"})

	charts := []struct {
		dir, prefix string
		renders     []chartRender
	}{
		{"/chart", "prod-", prodChartRenders},
		{"/chart-devstack", "devstack-", devstackChartRenders},
	}
	var outNames []string
	for _, c := range charts {
		for _, r := range c.renders {
			lint := []string{"helm", "lint", c.dir}
			template := []string{"helm", "template", "htr", c.dir, "-n", "htr-batch"}
			for _, v := range r.values {
				lint = append(lint, "-f", c.dir+"/"+v)
				template = append(template, "-f", c.dir+"/"+v)
			}
			for _, s := range r.sets {
				lint = append(lint, "--set", s)
				template = append(template, "--set", s)
			}
			outName := c.prefix + r.name
			helm = helm.
				WithExec(lint).
				WithExec(template, dagger.ContainerWithExecOpts{RedirectStdout: "/out/" + outName + ".yaml"})
			outNames = append(outNames, outName)
		}
	}
	if _, err := helm.Sync(ctx); err != nil {
		return "", fmt.Errorf("helm lint/template failed: %w", err)
	}

	kubeconform := dag.Container().From(kubeconformImage)
	kubeconform = m.withCaBundle(kubeconform, caBundle)
	args := []string{"/kubeconform", "-strict", "-ignore-missing-schemas", "-summary"}
	for _, name := range outNames {
		path := "/render/" + name + ".yaml"
		kubeconform = kubeconform.WithFile(path, helm.File("/out/"+name+".yaml"))
		args = append(args, path)
	}

	// The converter's Job/ConfigMap skeletons (B63 Task 9: render.py loads
	// these and patches dynamic fields rather than building nested dicts) are
	// complete, valid objects on their own, placeholder values included --
	// validate them as-is so a skeleton edit that breaks the schema (e.g. a
	// placeholder of the wrong type) fails here instead of only surfacing in
	// a real render.
	manifestsDir := source.Directory("packages/converter/src/htrflow_converter/manifests")
	for _, name := range []string{
		"campaign-job.yaml", "warmup-job.yaml", "configmap.yaml", "pipeline-configmap.yaml",
	} {
		path := "/render/converter-" + name
		kubeconform = kubeconform.WithFile(path, manifestsDir.File(name))
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
