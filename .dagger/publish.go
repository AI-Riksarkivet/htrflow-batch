package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"time"
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

// tagExists asks the registry whether ref resolves (crane digest). Only a
// registry that answers "no such manifest" (or "no such repository") counts as
// free: any other failure -- no network, a refused credential -- is an error,
// because treating "could not tell" as "free" is how a release tag gets
// overwritten. The credential goes in as a docker config.json secret, never
// on the command line.
func (m *HtrflowBatch) tagExists(
	ctx context.Context,
	registry string,
	ref string,
	dockerUsername *dagger.Secret,
	dockerPassword *dagger.Secret,
	caBundle *dagger.File,
) (bool, error) {
	crane := dag.Container().From(craneImage).
		// A registry lookup is never a cache hit: the same question asked a
		// minute later can have a different answer.
		WithEnvVariable("CACHE_BUSTER", time.Now().UTC().Format(time.RFC3339Nano))
	crane = m.withCaBundle(crane, caBundle)
	if dockerUsername != nil && dockerPassword != nil {
		username, err := dockerUsername.Plaintext(ctx)
		if err != nil {
			return false, fmt.Errorf("failed to read docker username: %w", err)
		}
		password, err := dockerPassword.Plaintext(ctx)
		if err != nil {
			return false, fmt.Errorf("failed to read docker password: %w", err)
		}
		host := registry
		if host == "docker.io" {
			host = "https://index.docker.io/v1/"
		}
		auth := base64.StdEncoding.EncodeToString([]byte(username + ":" + password))
		config, err := json.Marshal(map[string]any{
			"auths": map[string]any{host: map[string]string{"auth": auth}},
		})
		if err != nil {
			return false, err
		}
		crane = crane.
			WithMountedSecret("/docker-config/config.json",
				dag.SetSecret("crane-docker-config", string(config)),
				dagger.ContainerWithMountedSecretOpts{Mode: 0o444}).
			WithEnvVariable("DOCKER_CONFIG", "/docker-config")
	}
	run := crane.WithExec(
		[]string{"crane", "digest", ref},
		dagger.ContainerWithExecOpts{UseEntrypoint: false, Expect: dagger.ReturnTypeAny},
	)
	code, err := run.ExitCode(ctx)
	if err != nil {
		return false, fmt.Errorf("registry lookup of %s failed: %w", ref, err)
	}
	if code == 0 {
		return true, nil
	}
	stderr, err := run.Stderr(ctx)
	if err != nil {
		return false, fmt.Errorf("registry lookup of %s failed: %w", ref, err)
	}
	if strings.Contains(stderr, "MANIFEST_UNKNOWN") || strings.Contains(stderr, "NAME_UNKNOWN") {
		return false, nil
	}
	return false, fmt.Errorf("cannot tell whether %s exists, refusing to publish: %s", ref, strings.TrimSpace(stderr))
}

// refuseExistingTags fails if any of refs is already on the registry. Tags
// are immutable (finding 3069): this is the check publish.yml runs before it
// calls publish-docker, here as well so that every way into PublishDocker --
// `make publish` included -- refuses to replace a release, and so the check
// sits right before the push instead of a build and a test run earlier.
func (m *HtrflowBatch) refuseExistingTags(
	ctx context.Context,
	registry string,
	refs []string,
	dockerUsername *dagger.Secret,
	dockerPassword *dagger.Secret,
	caBundle *dagger.File,
) error {
	for _, ref := range refs {
		exists, err := m.tagExists(ctx, registry, ref, dockerUsername, dockerPassword, caBundle)
		if err != nil {
			return err
		}
		if exists {
			return fmt.Errorf("%s already exists; tags are immutable — bump the version and publish a new tag", ref)
		}
	}
	return nil
}

// CheckTagFree is publish-docker's "never overwrite a tag" check on its own:
// it fails when <registry>/<repository>:<tag> (or, with --tag-suffix, either
// the suffixed tag or the bare one) already exists, and says so when neither
// does. Nothing is built or pushed.
func (m *HtrflowBatch) CheckTagFree(
	ctx context.Context,
	// Image repository, e.g. riksarkivet/htrflow-batch
	imageRepository string,
	tag string,
	// +default="docker.io"
	registry string,
	// +optional
	tagSuffix string,
	// +optional
	dockerUsername *dagger.Secret,
	// +optional
	dockerPassword *dagger.Secret,
	// +optional
	caBundle *dagger.File,
) (string, error) {
	refs := publishRefs(registry, imageRepository, tag, tagSuffix)
	if err := m.refuseExistingTags(ctx, registry, refs, dockerUsername, dockerPassword, caBundle); err != nil {
		return "", err
	}
	return "free: " + strings.Join(refs, ", "), nil
}

// publishRefs is what one publish run occupies: the tag it pushes and, when
// that is a per-arch tag, the bare tag the manifest list will take.
func publishRefs(registry, imageRepository, tag, tagSuffix string) []string {
	base := registry + "/" + imageRepository + ":" + tag
	if tagSuffix == "" {
		return []string{base}
	}
	return []string{base + tagSuffix, base}
}

// PublishDocker tests, builds and publishes an image to a registry and
// returns the published reference WITH its digest
// (`<registry>/<repo>:<tag>@sha256:…`) — publish.yml signs and attests that
// digest. component: "wrapper" (default), "web" or "campaigns". The image is built for the
// engine's own platform, so the runner's architecture decides what is pushed;
// --tag-suffix is how one run's per-arch images get distinct tags.
// Tags are immutable: a tag that already exists on the registry is refused
// before anything is built, and asked again right before the push.
func (m *HtrflowBatch) PublishDocker(
	ctx context.Context,
	// +default="wrapper"
	component string,
	// Image repository; empty selects the default for the component
	// (riksarkivet/htrflow-batch, riksarkivet/htrflow-web or
	// riksarkivet/htrflow-campaigns)
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
	// HTRFLOW_BASE_REVISION build arg for the wrapper image (see BuildWrapper);
	// ignored for the other components
	// +optional
	baseRevision string,
	// Suffix appended to the tag AFTER it has been validated against the
	// wrapper version, e.g. "-amd64" for one arch of an image a manifest list
	// then joins under the bare tag (publish.yml). Empty publishes the tag
	// itself.
	// +optional
	tagSuffix string,
	// TRANSFORMERS_VERSION build arg for the wrapper image (see BuildWrapper);
	// ignored for the other components. Empty — the default, and what a normal
	// release passes — keeps the dockerfile's pin, so the published image does
	// not change unless a run asks for the other line.
	// +optional
	transformersVersion string,
) (string, error) {
	resolvedTag, err := m.resolveTag(ctx, source, tag, skipValidation, caBundle)
	if err != nil {
		return "", err
	}

	// The repository default is per component; resolve it first so the
	// check asks about the right repository.
	if imageRepository == "" {
		switch component {
		case "wrapper":
			imageRepository = DefaultImageRepo
		case "web":
			imageRepository = DefaultWebRepo
		case "campaigns":
			imageRepository = DefaultCampaignsRepo
		}
	}
	refs := publishRefs(registry, imageRepository, resolvedTag, tagSuffix)
	if err := m.refuseExistingTags(ctx, registry, refs, dockerUsername, dockerPassword, caBundle); err != nil {
		return "", err
	}

	if _, err := m.Test(ctx, source, caBundle); err != nil {
		return "", fmt.Errorf("tests failed, aborting publish: %w", err)
	}

	var container *dagger.Container
	switch component {
	case "wrapper":
		container, err = m.BuildWrapper(ctx, source, baseRevision, "", resolvedTag, transformersVersion)
	case "web":
		// Tagged off the wrapper version: the repo releases its images as one
		// set, not per workspace member -- which is why that tag, not the web
		// package's own version, is what the image reports and the status
		// page's header shows.
		container, err = m.BuildWeb(ctx, source, caBundle, resolvedTag)
	case "campaigns":
		container, err = m.BuildCampaigns(ctx, source, caBundle, resolvedTag)
	default:
		return "", fmt.Errorf("unknown component %q (wrapper|web|campaigns)", component)
	}
	if err != nil {
		return "", fmt.Errorf("build failed during publish: %w", err)
	}
	// The level-0 library-API pin, on the container about to be pushed and
	// not on a second build of it (finding 3104).
	if component == "wrapper" {
		if _, err := m.driverTest(ctx, container, source, caBundle); err != nil {
			return "", fmt.Errorf("driver test failed, aborting publish: %w", err)
		}
	}
	// The CRITICAL gate ci.yml runs on main, on the image about to be pushed
	// (finding 3060): the arm64 wrapper job in publish.yml runs the same one
	// through `make scan-image`, so no architecture ships a critical finding
	// that has a fix.
	if _, err := m.scanImage(ctx, container, "CRITICAL", "table", 1, true, caBundle); err != nil {
		return "", fmt.Errorf("vulnerability gate failed, aborting publish: %w", err)
	}

	// Asked again: the test run and the build take long enough for a
	// parallel run to have pushed the tag since.
	if err := m.refuseExistingTags(ctx, registry, refs, dockerUsername, dockerPassword, caBundle); err != nil {
		return "", err
	}
	imageRef := refs[0]
	if dockerPassword != nil && dockerUsername != nil {
		username, err := dockerUsername.Plaintext(ctx)
		if err != nil {
			return "", fmt.Errorf("failed to read docker username: %w", err)
		}
		return container.WithRegistryAuth(registry, username, dockerPassword).Publish(ctx, imageRef)
	}
	return container.Publish(ctx, imageRef)
}
