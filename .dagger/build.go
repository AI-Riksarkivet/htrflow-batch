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

// Build creates the wrapper production image from .docker/htrflow-batch.dockerfile.
// Heavy: the base (airiksarkivet/htrflow + cu128 torch) is ~10 GB; first run
// populates the engine cache.
func (m *HtrflowBatch) Build(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
	// `git describe --tags --always --dirty` of the htrflow checkout the base
	// image was built from; stamped as the se.riksarkivet.htrflow.base.revision
	// label (audit W8). Empty keeps the dockerfile default.
	// +optional
	baseRevision string,
) (*dagger.Container, error) {
	return source.DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: ".docker/htrflow-batch.dockerfile",
		BuildArgs:  buildArgs(baseRevision),
	}), nil
}

// BuildReconciler creates the campaign reconciler image from
// .docker/htrflow-reconciler.dockerfile. Light: CPU-only, no torch — it builds
// the workspace member with uv rather than layering onto the htrflow base.
func (m *HtrflowBatch) BuildReconciler(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
) (*dagger.Container, error) {
	return source.DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: ".docker/htrflow-reconciler.dockerfile",
	}), nil
}
