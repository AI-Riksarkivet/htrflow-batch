package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
	"strings"
)

// resolveTag returns tag if given (validated against the wrapper version unless
// skipped), else "v" + wrapper version.
func (m *HtrflowBatch) resolveTag(ctx context.Context, source *dagger.Directory, tag string, skipValidation bool, caBundle *dagger.File) (string, error) {
	version, err := m.getVersion(ctx, source, caBundle)
	if err != nil {
		return "", err
	}
	if tag == "" {
		return "v" + version, nil
	}
	if !skipValidation {
		norm := func(v string) string { return strings.TrimPrefix(strings.TrimSpace(v), "v") }
		if norm(version) != norm(tag) {
			return "", fmt.Errorf("version mismatch: packages/wrapper/pyproject.toml has 'v%s' but tag is '%s'", version, tag)
		}
	}
	return tag, nil
}

// PublishDocker tests, builds and publishes an image to a registry and
// returns the published reference WITH its digest
// (`<registry>/<repo>:<tag>@sha256:…`) — publish.yml signs and attests that
// digest. component: "wrapper" (default), "viewer" or "web".
// Tags are treated as immutable: the workflow refuses a tag that already
// exists before calling this.
func (m *HtrflowBatch) PublishDocker(
	ctx context.Context,
	// +default="wrapper"
	component string,
	// Image repository; empty selects the default for the component
	// (riksarkivet/htrflow-batch, -viewer or riksarkivet/htrflow-web)
	// +optional
	imageRepository string,
	// Image tag (empty: "v" + version from packages/wrapper/pyproject.toml)
	// +optional
	tag string,
	// +default="docker.io"
	registry string,
	// +optional
	dockerUsername *dagger.Secret,
	// +optional
	dockerPassword *dagger.Secret,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// +optional
	skipValidation bool,
	// +optional
	caBundle *dagger.File,
	// HTRFLOW_BASE_REVISION build arg for the wrapper image (see Build);
	// ignored for the other components
	// +optional
	baseRevision string,
) (string, error) {
	resolvedTag, err := m.resolveTag(ctx, source, tag, skipValidation, caBundle)
	if err != nil {
		return "", err
	}

	if _, err := m.Test(ctx, source, caBundle); err != nil {
		return "", fmt.Errorf("tests failed, aborting publish: %w", err)
	}

	var container *dagger.Container
	switch component {
	case "wrapper":
		if imageRepository == "" {
			imageRepository = "riksarkivet/htrflow-batch"
		}
		container, err = m.Build(ctx, source, baseRevision)
	case "viewer":
		if imageRepository == "" {
			imageRepository = "riksarkivet/htrflow-batch-viewer"
		}
		// Use pinned ref to mirror BuildViewer's default for reproducibility
		container, err = m.BuildViewer(ctx, source, "f2e8f66d3bd5a69e8e392764204d13d9524f63b2", caBundle)
	case "web":
		if imageRepository == "" {
			imageRepository = "riksarkivet/htrflow-web"
		}
		// Tagged off the wrapper version like the viewer is: the repo releases
		// its images as one set, not per workspace member.
		container, err = m.BuildWeb(ctx, source)
	default:
		return "", fmt.Errorf("unknown component %q (wrapper|viewer|web)", component)
	}
	if err != nil {
		return "", fmt.Errorf("build failed during publish: %w", err)
	}

	imageRef := registry + "/" + imageRepository + ":" + resolvedTag
	if dockerPassword != nil && dockerUsername != nil {
		username, err := dockerUsername.Plaintext(ctx)
		if err != nil {
			return "", fmt.Errorf("failed to read docker username: %w", err)
		}
		return container.WithRegistryAuth(registry, username, dockerPassword).Publish(ctx, imageRef)
	}
	return container.Publish(ctx, imageRef)
}
