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
	return project.Service("viewer").Up()
}

// ComposeTest starts the compose stack and verifies the viewer serves uv.html
func (m *HtrflowBatch) ComposeTest(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
) (string, error) {
	service := m.ComposeUp(source)
	output, err := dag.Container().
		From("curlimages/curl:latest").
		WithServiceBinding("viewer", service).
		WithExec([]string{"curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "http://viewer:80/uv.html"}).
		Stdout(ctx)
	if err != nil {
		return "", fmt.Errorf("compose health check failed: %w", err)
	}
	return fmt.Sprintf("viewer healthy (HTTP %s)", output), nil
}
