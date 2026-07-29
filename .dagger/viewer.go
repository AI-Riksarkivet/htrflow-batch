package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// BuildViewer builds the UV4 viewer image reproducibly: clone the Riksarkivet
// universalviewer4 fork, apply .docker/uv4-uv-html.patch (enables the ALTO text
// panel config fetch + fixes overlay coordinates), npm build on node:20, then
// layer dist/ onto nginx-unprivileged and the campaign browser SPA (bun build of
// frontend/) on top of that. The SPA's index.html deliberately overwrites UV's
// demo one: the campaign browser is the front door, UV stays at /uv.html. See
// docs D19 notes for why the patch exists.
func (m *HtrflowBatch) BuildViewer(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// Git ref of Riksarkivet/universalviewer4 to build. Defaults to the exact
	// commit the patch was generated against (branch "develop" @ f2e8f66,
	// "Commit from GitHub Actions (Lint and Prettify code)", 2025-10-28) rather
	// than "main": the patch was produced as an uncommitted `git diff` in a
	// local clone sitting on that commit, and `git apply` has no history to
	// fall back on if upstream has since drifted. Pinning keeps the apply
	// reproducible; bump deliberately (and re-derive the patch if needed) to
	// pick up upstream changes.
	// +default="f2e8f66d3bd5a69e8e392764204d13d9524f63b2"
	ref string,
	// CA bundle for TLS-intercepting networks (npm + git clone)
	// +optional
	caBundle *dagger.File,
) (*dagger.Container, error) {
	uvSrc := dag.Git("https://github.com/Riksarkivet/universalviewer4").
		Ref(ref).
		Tree()

	builder := dag.Container().
		From("node:20-bookworm").
		WithDirectory("/src", uvSrc).
		WithFile("/src/uv4.patch", source.File(".docker/uv4-uv-html.patch")).
		WithWorkdir("/src")
	builder = m.withCaBundle(builder, caBundle)
	builder = builder.
		WithExec([]string{"git", "init", "-q"}). // patch applies with git apply; clone tree has no .git
		WithExec([]string{"git", "apply", "uv4.patch"}).
		WithExec([]string{"npm", "install", "--no-audit", "--no-fund"}).
		WithExec([]string{"npm", "run", "build"})

	dist := builder.Directory("/src/dist")

	spa := dag.Container().
		From("oven/bun:1").
		WithDirectory("/app", source.Directory("frontend"), dagger.ContainerWithDirectoryOpts{
			Exclude: []string{"node_modules", "dist"},
		}).
		WithWorkdir("/app")
	spa = m.withCaBundle(spa, caBundle)
	spa = spa.
		WithExec([]string{"bun", "install", "--frozen-lockfile"}).
		WithExec([]string{"bun", "run", "build"})

	// nginx-unprivileged: UID 101, listens on 8080 (the chart's containerPort,
	// Service targetPort and nginx `listen` all match).
	viewer := dag.Container().
		From("nginxinc/nginx-unprivileged:1.27-alpine").
		WithDirectory("/usr/share/nginx/html", dist).
		// status.sample.json is the dev fixture (static/), not part of the app.
		WithDirectory("/usr/share/nginx/html", spa.Directory("/app/dist").WithoutFile("status.sample.json"))
	return viewer, nil
}
