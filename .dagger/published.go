package main

import (
	"context"
	"dagger/htrflow-batch/internal/dagger"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// publishedPins are the files a release commit pins the published images in
// (docs/development/releasing.md): the web front in the chart's values, the
// wrapper in the demo pipeline `init` writes, the converter in the Argo CD
// hook. They are what a cluster installed from this commit runs, so they
// are what the checks below look at -- never a rebuild from source.
var publishedPins = map[string]string{
	"web":       "charts/htrflow-batch/values.yaml",
	"wrapper":   "packages/converter/src/htrflow_converter/template/pipelines/demo-v1.yaml",
	"campaigns": "packages/converter/src/htrflow_converter/template/argocd/apply.yaml",
}

// publishedRepos is the repository each of those files pins.
var publishedRepos = map[string]string{
	"web":       DefaultRegistry + "/" + DefaultWebRepo,
	"wrapper":   DefaultRegistry + "/" + DefaultImageRepo,
	"campaigns": DefaultRegistry + "/" + DefaultCampaignsRepo,
}

// publishedImage is the one digest-pinned reference to an image's
// repository in the file that pins it. More than one distinct digest is an
// error, not a choice: a half-applied release pin would otherwise be
// checked on whichever digest came first.
func publishedImage(ctx context.Context, source *dagger.Directory, image string) (string, error) {
	path, ok := publishedPins[image]
	if !ok {
		return "", fmt.Errorf("image must be \"wrapper\", \"web\" or \"campaigns\", got %q", image)
	}
	text, err := source.File(path).Contents(ctx)
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", path, err)
	}
	pin := regexp.MustCompile(regexp.QuoteMeta(publishedRepos[image]) + `@sha256:[0-9a-f]{64}`)
	found := map[string]bool{}
	for _, ref := range pin.FindAllString(text, -1) {
		found[ref] = true
	}
	refs := make([]string, 0, len(found))
	for ref := range found {
		refs = append(refs, ref)
	}
	sort.Strings(refs)
	if len(refs) != 1 {
		return "", fmt.Errorf("%s pins %d digests of %s, want exactly one: %v", path, len(refs), publishedRepos[image], refs)
	}
	return refs[0], nil
}

// ScanPublished runs Trivy against a published image by reference, at the
// digest this commit pins -- the image a cluster actually runs (audit 0923
// D-5). Scan, ScanWeb and ScanCampaigns rebuild from source, which tells
// you about the next release and nothing about the one installed: a CVE
// published after the release is in the pinned digest whatever main says.
// Trivy reads the image straight from the registry, one layer at a time,
// for the given platform of the multi-architecture index.
func (m *HtrflowBatch) ScanPublished(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// Which image: "wrapper", "web" or "campaigns"
	image string,
	// Platform of the multi-architecture index to scan
	// +default="linux/amd64"
	platform string,
	// +default="CRITICAL,HIGH"
	severity string,
	// +default="table"
	format string,
	// +default=1
	exitCode int,
	// Skip findings without a distribution fix (will_not_fix); false gates on everything
	// +default=true
	ignoreUnfixed bool,
	// CA bundle for TLS-intercepting networks (registry and Trivy DB)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	ref, err := publishedImage(ctx, source, image)
	if err != nil {
		return "", err
	}
	args := []string{
		"trivy", "image", "--image-src", "remote", "--platform", platform,
		"--severity", severity, "--format", format,
		"--exit-code", fmt.Sprintf("%d", exitCode),
		"--skip-version-check",
	}
	if ignoreUnfixed {
		args = append(args, "--ignore-unfixed")
	}
	output, err := m.withCaBundle(dag.Container().From(trivyImage), caBundle).
		WithExec(append(args, ref)).
		Stdout(ctx)
	if err != nil {
		if output == "" {
			return "", fmt.Errorf("trivy scan of %s failed: %w", ref, err)
		}
		return output, fmt.Errorf("vulnerabilities found in %s: %w", ref, err)
	}
	return fmt.Sprintf("%s (%s)\n%s", ref, platform, output), nil
}

// VerifyPublished runs the chart's own signature rule -- the verify-images
// ClusterPolicy exactly as values-prod.yaml renders it -- against the three
// digests this commit pins, through the Kyverno CLI the policy tests use.
// Kyverno fetches each image's signature from the registry and its
// transparency-log proof, as the admission webhook would. This is what
// found the production profile refusing every published image (audit 0923
// D-1): the rule looked for a signature format the release no longer
// writes, and nothing offline could see that.
func (m *HtrflowBatch) VerifyPublished(
	ctx context.Context,
	// +defaultPath="/"
	// +optional
	source *dagger.Directory,
	// CA bundle for TLS-intercepting networks (registry, TUF and Rekor)
	// +optional
	caBundle *dagger.File,
) (string, error) {
	var pods strings.Builder
	for _, image := range []string{"web", "wrapper", "campaigns"} {
		ref, err := publishedImage(ctx, source, image)
		if err != nil {
			return "", err
		}
		fmt.Fprintf(&pods, "---\napiVersion: v1\nkind: Pod\nmetadata: {name: %s, namespace: htr-batch}\n"+
			"spec: {containers: [{name: main, image: %q}]}\n", image, ref)
	}
	container := dag.Container().
		From(helmImage).
		WithFile("/usr/local/bin/kyverno", dag.Container().From(kyvernoCliImage).File("/ko-app/kubectl-kyverno")).
		WithDirectory("/chart", source.Directory("charts/htrflow-batch")).
		WithNewFile("/pods.yaml", pods.String())
	container = m.withCaBundle(container, caBundle)
	// A pass per image and no failure: `kyverno apply` exits non-zero on a
	// failed rule, and the count guards against a policy that matched
	// nothing (a Pod outside its namespace, say) reading as a pass.
	return container.
		WithExec([]string{"sh", "-c", `set -eu
helm template htr /chart -n htr-batch -f /chart/values-prod.yaml \
  --set publicResultsBase=https://ci.invalid/ --set network.enabled=false \
  --show-only templates/policies/verify-images.yaml > /policy.yaml
kyverno apply /policy.yaml --resource /pods.yaml --remove-color | tee /out.txt
grep -Eq 'pass: ([3-9]|[1-9][0-9]+), fail: 0, warn: 0, error: 0' /out.txt`}).
		Stdout(ctx)
}
