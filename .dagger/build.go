package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
)

// Build creates the wrapper production image from .docker/htrflow-batch.dockerfile.
// Heavy: the base (airiksarkivet/htrflow + cu128 torch) is ~10 GB; first run
// populates the engine cache.
func (m *HtrflowBatch) Build(
	ctx context.Context,
	// +defaultPath="/"
	source *dagger.Directory,
) (*dagger.Container, error) {
	return source.DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: ".docker/htrflow-batch.dockerfile",
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
