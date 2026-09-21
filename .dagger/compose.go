package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// ComposeUp loads .docker/docker-compose.yml and starts the stack on the
// Dagger engine. Only .docker/ is mounted, which is the compose project root
// — so any service this brings up must be image-only (a `build:` context of
// `..` cannot resolve here); see the note on the `web` service.
func (m *HtrflowBatch) ComposeUp(
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
) *dagger.Service {
	project := dag.DockerCompose().Project(dagger.DockerComposeProjectOpts{
		Source: source.Directory(".docker"),
	})
	return project.Service("web").Up()
}

// ComposeTest starts the compose stack and verifies the web image serves
// uv.html. The web service runs the release pinned by digest in the compose
// file (see ComposeUp), so this checks that release, not the checkout; the
// service runs site-only — no apiserver in a compose stack — and listens on
// 8081. `make compose-smoke` is the checkout's twin: it builds both images
// with the recipes that ship them and runs the stack on those.
func (m *HtrflowBatch) ComposeTest(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
) (string, error) {
	service := m.ComposeUp(source)
	output, err := dag.Container().
		From(curlImage).
		WithServiceBinding("web", service).
		WithExec([]string{"curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "http://web:8081/uv.html"}).
		Stdout(ctx)
	if err != nil {
		return "", fmt.Errorf("compose health check failed: %w", err)
	}
	return fmt.Sprintf("web healthy (HTTP %s)", output), nil
}
