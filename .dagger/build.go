package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// buildArgs renders the HTRFLOW_BASE_REVISION build arg when given; empty
// keeps the dockerfile's default (the upstream tag's own revision suffix).
func buildArgs(baseRevision string) []dagger.BuildArg {
	if baseRevision == "" {
		return nil
	}
	return []dagger.BuildArg{{Name: "HTRFLOW_BASE_REVISION", Value: baseRevision}}
}

// BuildWrapper creates the wrapper production image from
// .docker/htrflow-batch.dockerfile. Heavy: the base (airiksarkivet/htrflow +
// cu128 torch) is ~10 GB; first run populates the engine cache.
//
// The dockerfile serves both architectures — it selects its base stage from
// TARGETARCH — so the image this returns is the ENGINE's architecture unless
// a platform is named. Naming a foreign one means qemu, and uv segfaults
// under qemu-x86_64 (docs/development/local-k3s.md), which is why every
// caller in this repo leaves it empty and builds on a native runner instead:
// ci.yml and publish.yml put the arm64 image on an ubuntu-24.04-arm runner.
// The argument exists for a caller that has an engine per platform and wants
// to say which one it is talking to.
func (m *HtrflowBatch) BuildWrapper(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
	// `git describe --tags --always --dirty` of the htrflow checkout the base
	// image was built from; stamped as the se.riksarkivet.htrflow.base.revision
	// label (audit W8). Empty keeps the dockerfile default.
	// +optional
	baseRevision string,
	// Platform to build for, e.g. "linux/arm64". Empty (the default, and what
	// every caller here passes) builds for the engine's own platform — no
	// emulation. See the note above before setting it.
	// +optional
	platform dagger.Platform,
) (*dagger.Container, error) {
	return source.DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: ".docker/htrflow-batch.dockerfile",
		BuildArgs:  buildArgs(baseRevision),
		Platform:   platform,
	}), nil
}

// BuildWeb creates the web image from .docker/htrflow-web.dockerfile: the
// campaign browser (bun), the Universal Viewer fork with uv4-uv-html.patch
// applied (npm, pinned commit) and the read API that serves both. The whole
// recipe lives in the dockerfile so `make build-web` and this build the same
// image — there is no second copy of it here to drift.
//
// Light next to the wrapper: CPU-only, no torch. The converter is not built
// into an image at all: it runs in CI/laptops via `uvx --from
// "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter"
// htrflow-campaigns` (or `uv tool install` from a checkout — see
// examples/campaigns/.github/workflows/render.yml).
func (m *HtrflowBatch) BuildWeb(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks (the git clone, npm and bun
	// installs). Passed to the build as the optional `ca` secret the
	// dockerfile mounts; without it the images' stock CA set is used.
	// +optional
	caBundle *dagger.File,
) (*dagger.Container, error) {
	opts := dagger.DirectoryDockerBuildOpts{Dockerfile: ".docker/htrflow-web.dockerfile"}
	if caBundle != nil {
		contents, err := caBundle.Contents(ctx)
		if err != nil {
			return nil, err
		}
		opts.Secrets = []*dagger.Secret{dag.SetSecret("ca", contents)}
	}
	return source.DockerBuild(opts), nil
}
