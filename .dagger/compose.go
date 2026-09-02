package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
)

// ComposeUp loads .docker/docker-compose.yml and starts the stack on the Dagger engine
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
// uv.html. The service is built from .docker/htrflow-web.dockerfile and runs
// site-only (no apiserver in a compose stack), listening on 8081.
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
