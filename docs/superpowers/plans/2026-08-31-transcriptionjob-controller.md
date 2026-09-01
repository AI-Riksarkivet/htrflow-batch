# TranscriptionJob Controller Implementation Plan (Plan A: controller, CRDs, chart)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CronJob reconciler and its S3 state files with a `TranscriptionJob`/`Pipeline` CRD pair and a Go controller that submits Kueue-queued wrapper Jobs and records outcomes in CR status.

**Architecture:** A kubebuilder-layout Go module in `packages/controller/` (copy of the rask-operator pattern) with two reconcilers: `Pipeline` (digest check, ConfigMap fan-out, warm-up Job, Ready/Warmed conditions) and `TranscriptionJob` (validate sources once, resume-detect from S3, submit Jobs within window/global cap with claim checks and cross-CR interleave, react to Job events, keep per-volume one-letter codes in status). A read-only HTTP projection of CRs on `:8081` replaces `status.json`. The chart ships the CRDs and a controller Deployment; `reconciler.yaml`, `pipelines.yaml`, `job-example.yaml` and the `htrflow_reconciler` package are deleted.

**Tech Stack:** Go 1.26, controller-runtime v0.24, controller-gen, envtest (ginkgo/gomega as in rask-operator), aws-sdk-go-v2 (S3 HEAD/PUT only), Helm 3, kubeconform, dagger (Go module in `.dagger/`), Kueue v1beta2 labels.

**Spec:** `docs/superpowers/specs/2026-08-31-transcriptionjob-controller-design.md` — read it first; decisions are cited as D1–D21.

## Global Constraints

- Size budgets (spec §1), enforced by `scripts/loc-budget.sh` from Task 1: controller ≤ 1 500 non-test Go lines · wrapper ≤ 1 500 Python · frontend ≤ 2 500 TS/Svelte · chart ≤ 700 template lines. One language per layer.
- Group/version `htrflow.riksarkivet.se/v1alpha1`; labels `htrflow.riksarkivet.se/{volume,pipeline,job,managed-by}` plus `app: htrflow-batch` (D18).
- `TranscriptionJob.spec.volumes` ≤ 10 000 (D3); volume id pattern `^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$`; pipeline name `^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$`; manifests/images `^https?://`.
- Immutable via CEL (D13): `Pipeline.spec`; `TranscriptionJob.spec.pipeline`, `.volumes`.
- Job name `htr-<sha1("ns/pipeline/volume")[:12]>` (D20); `backoffLimit: 0`; podFailurePolicy exit 13 → FailJob, DisruptionTarget → Ignore; `activeDeadlineSeconds = max(minDeadline, pages × perPage)`; `ttlSecondsAfterFinished: 3600`; pod security exactly as `jobspec.py` (non-root 1000, RO rootfs, drop ALL, RuntimeDefault, no SA token).
- Env contract to the wrapper unchanged; `S3_PREFIX=<namespace>/` unless `--legacy-layout` (D12); `HF_HUB_OFFLINE=1`.
- Defaults: `window` 20 per CR, `maxInflight` 40 global admitted Jobs, `validationsPerReconcile` 50, `minDeadlineSeconds` 21600, `perPageSeconds` 30, `manifestMaxBytes` 16 MiB, `fetchMaxBytes` 64 MiB.
- The controller never lists S3, never writes results, never deletes S3 objects (D19); its S3 credential is read-only except the `sources/` prefix for synthetic manifests.
- Commit messages carry the story id: `feat(controller): … (B63)`.
- Work in a git worktree (`superpowers:using-git-worktrees`) off `main` of `AI-Riksarkivet/htrflow-batch` (remote `org`); shared checkout rule.

---

## File structure

```
packages/controller/
  go.mod  go.sum  Makefile  Dockerfile  PROJECT
  cmd/main.go                       # manager: both reconcilers + read API + metrics + health
  api/v1alpha1/
    groupversion_info.go
    pipeline_types.go               # Pipeline (cluster-scoped)
    transcriptionjob_types.go       # TranscriptionJob (namespaced)
    zz_generated.deepcopy.go        # generated
  internal/volumes/codes.go         # "P"|"V"|"R:<pages>"|"D:<pages>"|"F:<n>"|"I:<reason>" + counts/phase
  internal/manifest/fetch.go        # fetch+classify+pageCount, synthetic manifests
  internal/jobspec/job.go           # Job/warm-up Job builders (port of jobspec.py)
  internal/results/s3.go            # HEAD manifest.json (resume), PUT sources/ (synthetic)
  internal/controller/pipeline_controller.go
  internal/controller/transcriptionjob_controller.go
  internal/controller/planner.go    # interleave + claim check + window/cap
  internal/api/server.go            # GET /api/v1/jobs[/{ns}/{name}]
  config/crd/bases/                 # controller-gen output, copied into the chart
charts/htrflow-batch/crds/*.yaml    # from config/crd/bases
charts/htrflow-batch/templates/controller.yaml   # replaces reconciler.yaml + pipelines.yaml
scripts/loc-budget.sh
scripts/campaigns/convert.py        # campaigns/*.yaml + pipelines/*.yaml → CR manifests
```

---

### Task 1: Go module scaffold, budgets, CI hooks

**Files:**
- Create: `packages/controller/go.mod`, `packages/controller/cmd/main.go`, `packages/controller/Makefile`, `packages/controller/PROJECT`, `packages/controller/Dockerfile`, `scripts/loc-budget.sh`
- Modify: `Makefile` (root, add `controller-*` targets), `.gitignore` (add `packages/controller/bin/`)
- Test: `packages/controller/cmd/main_test.go`

**Interfaces:**
- Produces: module path `github.com/AI-Riksarkivet/htrflow-batch/controller`; `make -C packages/controller test|build|manifests|generate`; `scripts/loc-budget.sh` exit 1 when a budget is exceeded.

- [ ] **Step 1: Write the budget script and its failing test invocation**

```bash
# scripts/loc-budget.sh
#!/usr/bin/env bash
# Non-test line budgets from the spec (§1). Fails the build when exceeded.
set -euo pipefail
cd "$(dirname "$0")/.."
count() { find "$1" -type f \( "${@:2}" \) -not -name '*_test.go' -not -name '*.test.ts' -not -name 'test_*.py' -not -path '*/node_modules/*' -not -name 'zz_generated*' -print0 | xargs -0 cat 2>/dev/null | wc -l; }
check() { local name=$1 got=$2 max=$3; printf '%-12s %6d / %d\n' "$name" "$got" "$max"; [ "$got" -le "$max" ] || { echo "::error::$name over budget ($got > $max)"; fail=1; }; }
fail=0
check controller "$(count packages/controller -name '*.go')" 1500
check wrapper    "$(count packages/wrapper/src -name '*.py')" 1500
check frontend   "$(count frontend/src -name '*.ts' -o -name '*.svelte')" 2500
check chart      "$(count charts/htrflow-batch/templates -name '*.yaml' -o -name '*.tpl')" 700
exit $fail
```

Run: `chmod +x scripts/loc-budget.sh && scripts/loc-budget.sh`
Expected: exits 1 (frontend 3952 > 2500, chart 1405 > 700) — that is the failing state Plan B and Task 10 fix. Wire it into CI only in Task 12 when it can pass.

- [ ] **Step 2: Scaffold the module (kubebuilder layout copied from rask-operator, no webhooks)**

```bash
mkdir -p packages/controller && cd packages/controller
go mod init github.com/AI-Riksarkivet/htrflow-batch/controller
go get sigs.k8s.io/controller-runtime@v0.24.1 k8s.io/api@v0.36.2 k8s.io/apimachinery@v0.36.2 k8s.io/client-go@v0.36.2 \
       github.com/onsi/ginkgo/v2 github.com/onsi/gomega github.com/aws/aws-sdk-go-v2/service/s3 github.com/aws/aws-sdk-go-v2/config
```

`cmd/main.go` — copy `~/rask-operator/cmd/main.go` minus the webhook/TLS blocks; keep metrics (`:8080`), health (`:8081` is taken by the read API → use `:8082` for probes), leader election flag, and register both reconcilers (stubs until Tasks 7–8):

```go
package main

import (
	"flag"
	"os"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	htrv1 "github.com/AI-Riksarkivet/htrflow-batch/controller/api/v1alpha1"
	"github.com/AI-Riksarkivet/htrflow-batch/controller/internal/controller"
)

var scheme = runtime.NewScheme()

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(htrv1.AddToScheme(scheme))
}

func main() {
	var cfg controller.Config
	flag.StringVar(&cfg.MetricsAddr, "metrics-bind-address", ":8080", "")
	flag.StringVar(&cfg.ProbeAddr, "health-probe-bind-address", ":8082", "")
	flag.StringVar(&cfg.APIAddr, "api-bind-address", ":8081", "read-only /api/v1 for the status page")
	flag.BoolVar(&cfg.LeaderElection, "leader-elect", true, "")
	flag.IntVar(&cfg.Window, "window", 20, "default in-flight Jobs per TranscriptionJob")
	flag.IntVar(&cfg.MaxInflight, "max-inflight", 40, "global cap on admitted Jobs")
	flag.IntVar(&cfg.ValidationsPerReconcile, "validations-per-reconcile", 50, "")
	flag.IntVar(&cfg.MinDeadlineSeconds, "min-deadline-seconds", 21600, "")
	flag.IntVar(&cfg.PerPageSeconds, "per-page-seconds", 30, "")
	flag.BoolVar(&cfg.LegacyLayout, "legacy-layout", false, "results at <pipeline>/<volume>/ (no namespace prefix)")
	flag.StringVar(&cfg.SourceTemplate, "source-template", "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest", "")
	flag.StringVar(&cfg.PublicResultsBase, "public-results-base", "", "")
	flag.StringVar(&cfg.InternalResultsBase, "internal-results-base", "", "")
	flag.StringVar(&cfg.S3Secret, "s3-secret", "htr-batch-s3", "Secret name in each tenant namespace")
	flag.StringVar(&cfg.DataPVC, "data-pvc", "htr-test-data", "")
	flag.StringVar(&cfg.Queue, "queue", "htr-batch", "LocalQueue name in each tenant namespace")
	flag.StringVar(&cfg.RuntimeClass, "runtime-class", "nvidia", "")
	flag.Parse()
	ctrl.SetLogger(zap.New())

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme, Metrics: metricsserver.Options{BindAddress: cfg.MetricsAddr},
		HealthProbeBindAddress: cfg.ProbeAddr, LeaderElection: cfg.LeaderElection,
		LeaderElectionID: "htrflow-controller.htrflow.riksarkivet.se",
	})
	if err != nil { os.Exit(exit("manager", err)) }
	if err := controller.Setup(mgr, cfg); err != nil { os.Exit(exit("setup", err)) }
	_ = mgr.AddHealthzCheck("healthz", healthz.Ping)
	_ = mgr.AddReadyzCheck("readyz", healthz.Ping)
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil { os.Exit(exit("run", err)) }
}

func exit(what string, err error) int { ctrl.Log.Error(err, what); return 1 }
```

`internal/controller/setup.go` (stub for now, filled in Tasks 7–9):

```go
package controller

import ctrl "sigs.k8s.io/controller-runtime"

type Config struct {
	MetricsAddr, ProbeAddr, APIAddr             string
	LeaderElection, LegacyLayout                bool
	Window, MaxInflight, ValidationsPerReconcile int
	MinDeadlineSeconds, PerPageSeconds          int
	SourceTemplate, PublicResultsBase, InternalResultsBase string
	S3Secret, DataPVC, Queue, RuntimeClass      string
}

func Setup(mgr ctrl.Manager, cfg Config) error { return nil }
```

`Makefile`: copy rask-operator's `manifests generate fmt vet test build docker-build controller-gen envtest` targets verbatim, with `CRD_OUTPUT=config/crd/bases` and an extra target `crds-to-chart: manifests` that runs `cp config/crd/bases/*.yaml ../../charts/htrflow-batch/crds/`. `Dockerfile`: rask-operator's (golang:1.26 builder → `gcr.io/distroless/static:nonroot`, `USER 65532`).

- [ ] **Step 3: Write the smoke test**

```go
// cmd/main_test.go
package main

import "testing"

func TestSchemeRegistersGroup(t *testing.T) {
	if !scheme.IsVersionRegistered(htrv1.GroupVersion) {
		t.Fatalf("group %s not registered", htrv1.GroupVersion)
	}
}
```
(`htrv1` compiles only after Task 2's `groupversion_info.go`; create that file now with just the `GroupVersion`/`AddToScheme` boilerplate from rask-operator, group `htrflow.riksarkivet.se`, version `v1alpha1`.)

- [ ] **Step 4: Build and test**

Run: `cd packages/controller && go build ./... && go test ./cmd/...`
Expected: PASS.

- [ ] **Step 5: Root Makefile + gitignore, commit**

Add to root `Makefile`: `controller-test: ; $(MAKE) -C packages/controller test` and `controller-build`. Add `packages/controller/bin/` to `.gitignore`.

```bash
git add packages/controller scripts/loc-budget.sh Makefile .gitignore
git commit -m "feat(controller): Go module scaffold, budgets script, Makefile targets (B63)"
```

---

### Task 2: API types and generated CRDs

**Files:**
- Create: `packages/controller/api/v1alpha1/pipeline_types.go`, `transcriptionjob_types.go`
- Generated: `api/v1alpha1/zz_generated.deepcopy.go`, `config/crd/bases/htrflow.riksarkivet.se_pipelines.yaml`, `..._transcriptionjobs.yaml`, copied to `charts/htrflow-batch/crds/`
- Test: `packages/controller/api/v1alpha1/types_test.go` (envtest: schema rejections)

**Interfaces:**
- Produces: Go types `Pipeline{Spec PipelineSpec{Image string; Steps runtime.RawExtension; ModelRevision string}; Status PipelineStatus{Conditions []metav1.Condition; PipelineSha256 string; Warmed map[string]bool}}`, `TranscriptionJob{Spec TranscriptionJobSpec{Pipeline string; Volumes []VolumeSpec; Window *int32; MaxAttempts int32; Priority string; Paused bool}; Status TranscriptionJobStatus{Phase Phase; Counts Counts; Volumes map[string]string; Failures []Failure; ResultsBase string; ObservedGeneration int64; Conditions []metav1.Condition}}`, `VolumeSpec{ID string; Manifest string; Images []string}`, `Failure{Volume string; Attempt int32; Reason string; ExitCode *int32; Log string; At metav1.Time}`, `Counts{Total, Pending, Running, Done, Failed, Invalid int32}`, `Phase` consts `Pending Validating Running Paused Succeeded Failed`.

- [ ] **Step 1: Write the schema test (envtest)**

```go
// api/v1alpha1/types_test.go
package v1alpha1_test

import (
	"context"
	"path/filepath"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	htrv1 "github.com/AI-Riksarkivet/htrflow-batch/controller/api/v1alpha1"
)

func TestSchemaRejectsBadSpecs(t *testing.T) {
	env := &envtest.Environment{CRDDirectoryPaths: []string{filepath.Join("..", "..", "config", "crd", "bases")}}
	cfg, err := env.Start(); if err != nil { t.Fatal(err) }
	t.Cleanup(func() { _ = env.Stop() })
	s := htrv1.NewScheme() // helper returning runtime.Scheme with our types + core
	c, _ := client.New(cfg, client.Options{Scheme: s})
	ctx := context.Background()

	bad := &htrv1.TranscriptionJob{ObjectMeta: metav1.ObjectMeta{Name: "x", Namespace: "default"},
		Spec: htrv1.TranscriptionJobSpec{Pipeline: "demo-v1", Volumes: []htrv1.VolumeSpec{{ID: "-bad-"}}}}
	if err := c.Create(ctx, bad); err == nil { t.Fatal("volume id '-bad-' must be rejected by the pattern") }

	bad2 := &htrv1.TranscriptionJob{ObjectMeta: metav1.ObjectMeta{Name: "y", Namespace: "default"},
		Spec: htrv1.TranscriptionJobSpec{Pipeline: "demo-v1", Volumes: []htrv1.VolumeSpec{{ID: "ok", Manifest: "ftp://x"}}}}
	if err := c.Create(ctx, bad2); err == nil { t.Fatal("non-http manifest must be rejected") }

	good := &htrv1.TranscriptionJob{ObjectMeta: metav1.ObjectMeta{Name: "z", Namespace: "default"},
		Spec: htrv1.TranscriptionJobSpec{Pipeline: "demo-v1", Volumes: []htrv1.VolumeSpec{{ID: "R0001203"}}}}
	if err := c.Create(ctx, good); err != nil { t.Fatal(err) }
	good.Spec.Pipeline = "other"
	if err := c.Update(ctx, good); err == nil { t.Fatal("spec.pipeline must be immutable (CEL)") }
}
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd packages/controller && make test`
Expected: FAIL — types undefined / CRDs missing.

- [ ] **Step 3: Write the types**

```go
// api/v1alpha1/pipeline_types.go
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
)

// PipelineSpec is immutable once created (D13): a changed pipeline is a new name.
// +kubebuilder:validation:XValidation:rule="self == oldSelf",message="Pipeline.spec is immutable; create a new Pipeline"
type PipelineSpec struct {
	// image must be digest-pinned.
	// +kubebuilder:validation:Pattern=`^[a-z0-9./:-]+@sha256:[0-9a-f]{64}$`
	Image string `json:"image"`
	// steps are htrflow pipeline steps, passed through verbatim to /config/pipeline.yaml.
	// +kubebuilder:pruning:PreserveUnknownFields
	// +kubebuilder:validation:Schemaless
	Steps runtime.RawExtension `json:"steps"`
	// +optional
	// +kubebuilder:validation:Pattern=`^[0-9a-f]{40}$`
	ModelRevision string `json:"modelRevision,omitempty"`
}

type PipelineStatus struct {
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
	// +optional
	PipelineSha256 string `json:"pipelineSha256,omitempty"`
	// warmed is namespace → true once the warm-up Job completed there (D17).
	// +optional
	Warmed map[string]bool `json:"warmed,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster,shortName=pl
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
type Pipeline struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              PipelineSpec   `json:"spec"`
	Status            PipelineStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true
type PipelineList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Pipeline `json:"items"`
}

func init() { SchemeBuilder.Register(&Pipeline{}, &PipelineList{}) }
```

```go
// api/v1alpha1/transcriptionjob_types.go
package v1alpha1

import metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

type VolumeSpec struct {
	// +kubebuilder:validation:Pattern=`^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$`
	ID string `json:"id"`
	// +optional
	// +kubebuilder:validation:Pattern=`^https?://`
	Manifest string `json:"manifest,omitempty"`
	// +optional
	// +kubebuilder:validation:items:Pattern=`^https?://`
	Images []string `json:"images,omitempty"`
}

// +kubebuilder:validation:XValidation:rule="self.pipeline == oldSelf.pipeline",message="spec.pipeline is immutable"
// +kubebuilder:validation:XValidation:rule="self.volumes == oldSelf.volumes",message="spec.volumes is immutable"
type TranscriptionJobSpec struct {
	// +kubebuilder:validation:Pattern=`^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$`
	Pipeline string `json:"pipeline"`
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:MaxItems=10000
	Volumes []VolumeSpec `json:"volumes"`
	// +optional
	// +kubebuilder:validation:Minimum=1
	Window *int32 `json:"window,omitempty"`
	// +kubebuilder:default=3
	// +kubebuilder:validation:Minimum=1
	MaxAttempts int32 `json:"maxAttempts,omitempty"`
	// +optional
	Priority string `json:"priority,omitempty"`
	// +optional
	Paused bool `json:"paused,omitempty"`
}

type Phase string

const (
	PhasePending    Phase = "Pending"
	PhaseValidating Phase = "Validating"
	PhaseRunning    Phase = "Running"
	PhasePaused     Phase = "Paused"
	PhaseSucceeded  Phase = "Succeeded"
	PhaseFailed     Phase = "Failed"
)

type Counts struct {
	Total, Pending, Running, Done, Failed, Invalid int32 `json:",omitempty"`
}

type Failure struct {
	Volume   string      `json:"volume"`
	Attempt  int32       `json:"attempt"`
	Reason   string      `json:"reason"`
	ExitCode *int32      `json:"exitCode,omitempty"`
	Log      string      `json:"log,omitempty"`
	At       metav1.Time `json:"at"`
}

type TranscriptionJobStatus struct {
	// +optional
	Phase Phase `json:"phase,omitempty"`
	// +optional
	Counts Counts `json:"counts,omitempty"`
	// volumes: id → code. P pending · V validating · R:<pages> running · D:<pages> done · F:<attempts> failed · I:<reason> invalid.
	// +optional
	Volumes map[string]string `json:"volumes,omitempty"`
	// +optional
	// +kubebuilder:validation:MaxItems=50
	Failures []Failure `json:"failures,omitempty"`
	// +optional
	ResultsBase string `json:"resultsBase,omitempty"`
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:shortName=tj
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Pipeline",type=string,JSONPath=`.spec.pipeline`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Done",type=integer,JSONPath=`.status.counts.done`
// +kubebuilder:printcolumn:name="Failed",type=integer,JSONPath=`.status.counts.failed`
// +kubebuilder:printcolumn:name="Total",type=integer,JSONPath=`.status.counts.total`
type TranscriptionJob struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              TranscriptionJobSpec   `json:"spec"`
	Status            TranscriptionJobStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true
type TranscriptionJobList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []TranscriptionJob `json:"items"`
}

func init() { SchemeBuilder.Register(&TranscriptionJob{}, &TranscriptionJobList{}) }
```

Add to `groupversion_info.go`: `func NewScheme() *runtime.Scheme` that registers client-go's scheme and this group (used by tests and the API server).

- [ ] **Step 4: Generate, run tests**

Run: `make generate manifests crds-to-chart && make test`
Expected: PASS; `ls ../../charts/htrflow-batch/crds/` shows both CRDs; `grep -c x-kubernetes-validations config/crd/bases/*transcriptionjobs.yaml` ≥ 1.

- [ ] **Step 5: Commit**

```bash
git add packages/controller charts/htrflow-batch/crds
git commit -m "feat(controller): Pipeline and TranscriptionJob CRDs with CEL immutability and size limits (B63)"
```

---

### Task 3: Volume state codes

**Files:**
- Create: `packages/controller/internal/volumes/codes.go`
- Test: `packages/controller/internal/volumes/codes_test.go`

**Interfaces:**
- Produces: `type State byte` consts `Pending='P' Validating='V' Running='R' Done='D' Failed='F' Invalid='I'`; `type Code struct{State State; Pages int32; Attempts int32; Reason string}`; `Parse(s string) Code`; `(Code) String() string`; `Summarize(m map[string]string) (htrv1.Counts, htrv1.Phase)` where phase: any `R` → Running; else any `V` → Validating; else pending>0 → Pending; else failed>0 → Failed; else Succeeded. (`Paused` is set by the reconciler, not here.)

- [ ] **Step 1: Failing tests**

```go
package volumes

import "testing"

func TestParseAndString(t *testing.T) {
	for _, s := range []string{"P", "V", "R:88", "D:143", "F:2", "I:ClaimedBy=other"} {
		if got := Parse(s).String(); got != s { t.Errorf("%q round-trips to %q", s, got) }
	}
	if Parse("garbage").State != Pending { t.Error("unknown code must read as Pending (D11: never strand a volume)") }
}

func TestSummarizePhase(t *testing.T) {
	c, p := Summarize(map[string]string{"a": "D:1", "b": "F:3"})
	if p != "Failed" || c.Done != 1 || c.Failed != 1 || c.Total != 2 { t.Errorf("got %v %v", c, p) }
	_, p = Summarize(map[string]string{"a": "D:1", "b": "R:4", "c": "P"})
	if p != "Running" { t.Errorf("got %v", p) }
	_, p = Summarize(map[string]string{"a": "D:1", "b": "I:x"})
	if p != "Succeeded" { t.Errorf("invalid volumes do not fail a job: got %v", p) }
}
```

- [ ] **Step 2: Run → FAIL (undefined)**. Run: `go test ./internal/volumes/`

- [ ] **Step 3: Implement**

```go
package volumes

import (
	"strconv"
	"strings"

	htrv1 "github.com/AI-Riksarkivet/htrflow-batch/controller/api/v1alpha1"
)

type State byte

const (
	Pending State = 'P'; Validating State = 'V'; Running State = 'R'
	Done State = 'D'; Failed State = 'F'; Invalid State = 'I'
)

type Code struct {
	State    State
	Pages    int32  // R, D
	Attempts int32  // F
	Reason   string // I
}

func Parse(s string) Code {
	if s == "" { return Code{State: Pending} }
	st, rest := State(s[0]), ""
	if len(s) > 2 && s[1] == ':' { rest = s[2:] }
	switch st {
	case Pending, Validating:
		return Code{State: st}
	case Running, Done:
		n, _ := strconv.Atoi(rest); return Code{State: st, Pages: int32(n)}
	case Failed:
		n, _ := strconv.Atoi(rest); return Code{State: st, Attempts: int32(n)}
	case Invalid:
		return Code{State: st, Reason: rest}
	}
	return Code{State: Pending}
}

func (c Code) String() string {
	switch c.State {
	case Running, Done:
		return string(c.State) + ":" + strconv.Itoa(int(c.Pages))
	case Failed:
		return string(c.State) + ":" + strconv.Itoa(int(c.Attempts))
	case Invalid:
		return string(c.State) + ":" + strings.ReplaceAll(c.Reason, "\n", " ")
	}
	return string(c.State)
}

func Summarize(m map[string]string) (htrv1.Counts, htrv1.Phase) {
	var c htrv1.Counts
	for _, v := range m {
		c.Total++
		switch Parse(v).State {
		case Pending: c.Pending++
		case Validating: c.Pending++
		case Running: c.Running++
		case Done: c.Done++
		case Failed: c.Failed++
		case Invalid: c.Invalid++
		}
	}
	switch {
	case c.Running > 0: return c, htrv1.PhaseRunning
	case anyState(m, Validating): return c, htrv1.PhaseValidating
	case c.Pending > 0: return c, htrv1.PhasePending
	case c.Failed > 0: return c, htrv1.PhaseFailed
	}
	return c, htrv1.PhaseSucceeded
}

func anyState(m map[string]string, s State) bool {
	for _, v := range m { if Parse(v).State == s { return true } }
	return false
}
```

- [ ] **Step 4: Run → PASS.** - [ ] **Step 5: Commit** `git commit -am "feat(controller): per-volume state codes and phase summary (B63)"`

---

### Task 4: Manifest fetch, classification, synthetic manifests

**Files:**
- Create: `packages/controller/internal/manifest/fetch.go`, `synthetic.go`
- Test: `packages/controller/internal/manifest/fetch_test.go`

**Interfaces:**
- Consumes: none.
- Produces: `type Verdict struct{Format string; Pages int32; Permanent bool; Err string}` with `Format` ∈ `p2|p3|unsupported|unreachable`; `Fetch(ctx, url string, maxBytes int64) Verdict` (http(s) only, byte-capped body, 4xx → Permanent, 5xx/429/network → transient `unreachable`; a P3 `Collection` → `unsupported` Permanent); `Synthetic(volumeID string, images []string, base string) ([]byte, string)` returning a P3 manifest body and its id `<base>/sources/<volume>/manifest.json` — the exact shape `htrflow_reconciler.main` produces today (copy `_synthetic_manifest`'s structure: one Canvas per image, `items[].items[].items[].body.id = image`).

- [ ] **Step 1: Failing tests with httptest**

```go
func TestFetchClassifies(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/p3": fmt.Fprint(w, `{"@context":"http://iiif.io/api/presentation/3/context.json","type":"Manifest","items":[{"id":"c1"},{"id":"c2"}]}`)
		case "/p2": fmt.Fprint(w, `{"@context":"http://iiif.io/api/presentation/2/context.json","@type":"sc:Manifest","sequences":[{"canvases":[{"@id":"a"}]}]}`)
		case "/coll": fmt.Fprint(w, `{"type":"Collection","items":[{}]}`)
		case "/gone": w.WriteHeader(404)
		case "/flaky": w.WriteHeader(503)
		case "/big": w.Write(make([]byte, 5000))
		}
	}))
	defer srv.Close()
	cases := map[string]Verdict{
		"/p3": {Format: "p3", Pages: 2}, "/p2": {Format: "p2", Pages: 1},
		"/coll": {Format: "unsupported", Permanent: true}, "/gone": {Format: "unreachable", Permanent: true},
		"/flaky": {Format: "unreachable"}, "/big": {Format: "unsupported", Permanent: true},
	}
	for p, want := range cases {
		got := Fetch(context.Background(), srv.URL+p, 4000)
		if got.Format != want.Format || got.Pages != want.Pages || got.Permanent != want.Permanent {
			t.Errorf("%s: got %+v want %+v", p, got, want)
		}
	}
	if v := Fetch(context.Background(), "ftp://x/y", 100); !v.Permanent { t.Error("non-http is permanent") }
}
```

- [ ] **Step 2: Run → FAIL.** - [ ] **Step 3: Implement** (`net/http` client with 30 s timeout, `io.LimitReader(maxBytes+1)`, `encoding/json` into `map[string]any`; classification: `type=="Collection"` → unsupported; `items` non-empty → p3; `sequences[0].canvases` → p2; else unsupported). `Synthetic` builds `map[string]any` and `json.Marshal`s it.

- [ ] **Step 4: Run → PASS.** - [ ] **Step 5: Commit** `feat(controller): manifest fetch/classify with byte caps and synthetic manifests (B63)`

---

### Task 5: Job and warm-up Job builders (port of `jobspec.py`)

**Files:**
- Create: `packages/controller/internal/jobspec/job.go`, `names.go`
- Test: `packages/controller/internal/jobspec/job_test.go`

**Interfaces:**
- Consumes: `controller.Config` fields (`S3Secret, DataPVC, Queue, RuntimeClass, PublicResultsBase, MinDeadlineSeconds, PerPageSeconds, LegacyLayout`) — pass as `jobspec.Params` to avoid an import cycle.
- Produces: `type Params struct{Namespace, Pipeline, Image, Volume, ManifestURL, Queue, S3Secret, DataPVC, RuntimeClass, PublicResultsBase, Priority string; Pages int32; MinDeadline, PerPage int; LegacyLayout bool; Owner metav1.OwnerReference; NodeSelector map[string]string; Tolerations []corev1.Toleration}`; `JobName(ns, pipeline, volume string) string` = `"htr-" + hex(sha1(ns+"/"+pipeline+"/"+volume))[:12]`; `WarmupJobName(pipeline string) string` = `"htr-warmup-" + pipeline`; `BuildJob(p Params) *batchv1.Job`; `BuildWarmupJob(ns, pipeline, image, dataPVC string) *batchv1.Job`; `Deadline(pages int32, min, perPage int) int64`.

- [ ] **Step 1: Failing tests pinning the contract**

```go
func TestBuildJobContract(t *testing.T) {
	j := BuildJob(Params{Namespace: "ra", Pipeline: "demo-v1", Image: "docker.io/x@sha256:" + strings.Repeat("a", 64),
		Volume: "R0001203", ManifestURL: "https://m/1", Queue: "htr-batch", S3Secret: "htr-batch-s3", DataPVC: "htr-test-data",
		RuntimeClass: "nvidia", PublicResultsBase: "https://r/", Pages: 1000, MinDeadline: 21600, PerPage: 30})
	if j.Name != JobName("ra", "demo-v1", "R0001203") || !strings.HasPrefix(j.Name, "htr-") || len(j.Name) != 16 { t.Errorf("name %q", j.Name) }
	if *j.Spec.ActiveDeadlineSeconds != 30000 { t.Errorf("deadline = max(21600, 1000*30) want 30000, got %d", *j.Spec.ActiveDeadlineSeconds) }
	if *j.Spec.BackoffLimit != 0 || *j.Spec.TTLSecondsAfterFinished != 3600 || !*j.Spec.Suspend { t.Error("backoff/ttl/suspend contract") }
	env := map[string]string{}
	for _, e := range j.Spec.Template.Spec.Containers[0].Env { env[e.Name] = e.Value }
	for k, v := range map[string]string{"VOLUME_REF": "R0001203", "IIIF_MANIFEST_URL": "https://m/1", "PIPELINE_ID": "demo-v1",
		"PIPELINE_PATH": "/config/pipeline.yaml", "S3_PREFIX": "ra/", "HF_HUB_OFFLINE": "1", "AWS_SHARED_CREDENTIALS_FILE": "/secrets/s3/credentials",
		"HOME": "/work/home", "IMAGE_DIGEST": "docker.io/x@sha256:" + strings.Repeat("a", 64)} {
		if env[k] != v { t.Errorf("env %s=%q want %q", k, env[k], v) }
	}
	l := j.Labels
	if l["htrflow.riksarkivet.se/volume"] != "r0001203" || l["kueue.x-k8s.io/queue-name"] != "htr-batch" || l["app"] != "htrflow-batch" { t.Errorf("labels %v", l) }
	ps := j.Spec.Template.Spec.SecurityContext
	if ps == nil || *ps.RunAsUser != 1000 || !*ps.RunAsNonRoot || ps.SeccompProfile.Type != "RuntimeDefault" { t.Error("pod security") }
	cs := j.Spec.Template.Spec.Containers[0].SecurityContext
	if !*cs.ReadOnlyRootFilesystem || *cs.AllowPrivilegeEscalation || cs.Capabilities.Drop[0] != "ALL" { t.Error("container security") }
	if *j.Spec.Template.Spec.AutomountServiceAccountToken { t.Error("no SA token") }
	rules := j.Spec.PodFailurePolicy.Rules
	if rules[0].Action != "Ignore" || rules[1].Action != "FailJob" || rules[1].OnExitCodes.Values[0] != 13 { t.Error("podFailurePolicy") }
	legacy := BuildJob(Params{Namespace: "ra", Pipeline: "p", Volume: "v", LegacyLayout: true, MinDeadline: 1, PerPage: 1})
	for _, e := range legacy.Spec.Template.Spec.Containers[0].Env { if e.Name == "S3_PREFIX" && e.Value != "" { t.Error("legacy layout keeps S3_PREFIX empty") } }
}

func TestDeadlineMinimumWhenPagesUnknown(t *testing.T) {
	if Deadline(0, 21600, 30) != 21600 { t.Error("unknown pages → minimum") }
}
```

- [ ] **Step 2: Run → FAIL.** - [ ] **Step 3: Implement** — a line-by-line port of `jobspec.py` (`_POD_SECURITY`, `_CONTAINER_SECURITY`, `_workdir_env`, `_s3_env`, `_pod_failure_policy`, `build_job`, `build_warmup_job`) into typed `batchv1.Job`/`corev1.*` structs; `Priority != ""` adds label `kueue.x-k8s.io/priority-class`; `Owner` appended to `OwnerReferences`; ConfigMap name `htr-pipeline-<pipeline>`; resources `4 CPU / 8–16 Gi / 1 GPU` as today.

- [ ] **Step 4: Run → PASS.** - [ ] **Step 5: Commit** `feat(controller): Job and warm-up builders ported from jobspec.py, tenant S3 prefix (B63)`

---

### Task 6: Results store — resume detection and synthetic manifest upload

**Files:**
- Create: `packages/controller/internal/results/s3.go`
- Test: `packages/controller/internal/results/s3_test.go`

**Interfaces:**
- Produces: `type Store interface{ Done(ctx, key string) (found bool, pipelineSha string, pages int32, err error); PutSource(ctx, key string, body []byte) error }`; `NewS3(endpoint, bucket string, creds aws.CredentialsProvider) Store`; `ResultKey(legacy bool, ns, pipeline, volume string) string` → `"<ns>/<pipeline>/<volume>/manifest.json"` or `"<pipeline>/<volume>/manifest.json"`. `Done` does one `GetObject` of `manifest.json` (small) and reads `pipeline_sha256` and `pages`; 404 → `found=false`.

- [ ] **Step 1: Failing test** — httptest server emulating S3 path-style: `GET /bucket/ra/demo-v1/R1/manifest.json` → `{"pipeline_sha256":"abc","pages":12}`; `GET …/R2/manifest.json` → 404; `PUT /bucket/ra/sources/R3/manifest.json` records body. Assert `Done` returns `(true,"abc",12)`, `(false,…)`, and `PutSource` stored the body. Use aws-sdk-go-v2 with `BaseEndpoint` + `UsePathStyle: true` and static test credentials.

- [ ] **Step 2: Run → FAIL.** - [ ] **Step 3: Implement** (aws-sdk-go-v2 `s3.Client`; `Done` handles `*types.NoSuchKey`/404 as not found). - [ ] **Step 4: PASS.** - [ ] **Step 5: Commit** `feat(controller): S3 results store — resume detection by manifest.json, synthetic sources (B63)`

---

### Task 7: Pipeline reconciler

**Files:**
- Create: `packages/controller/internal/controller/pipeline_controller.go`, `suite_test.go` (envtest bootstrap, ginkgo)
- Modify: `internal/controller/setup.go` (register)
- Test: `internal/controller/pipeline_controller_test.go`

**Interfaces:**
- Consumes: `jobspec.BuildWarmupJob`, `jobspec.WarmupJobName`.
- Produces: `PipelineReconciler{client.Client; Scheme; Cfg Config}`; conditions `Ready` (reason `InvalidImage|NotWarmed|Ready`) and per-namespace `status.warmed[ns]`; ConfigMap `htr-pipeline-<name>` with key `pipeline.yaml` = `yaml.Marshal({"steps": spec.steps})` in every namespace that has a `TranscriptionJob` referencing the pipeline (watch TranscriptionJobs with a `handler.EnqueueRequestsFromMapFunc` on `.spec.pipeline`); `status.pipelineSha256` = sha256 of that YAML text. Warm-up Job per namespace, owned by nothing (cluster-scoped owner cannot own namespaced; label it `htrflow.riksarkivet.se/pipeline=<name>` and delete it when the Pipeline is deleted via finalizer).

- [ ] **Step 1: Failing envtest** — create `Pipeline demo-v1` with a valid digest, a `TranscriptionJob` in namespace `ra` referencing it; expect within 5 s: ConfigMap `ra/htr-pipeline-demo-v1` exists with `pipeline.yaml` containing `steps:`; Job `ra/htr-warmup-demo-v1` exists with `CUDA_VISIBLE_DEVICES=""`; `Ready=False/NotWarmed`. Mark the warm-up Job `Complete` (patch status); expect `status.warmed["ra"]=true` and `Ready=True`. Create `Pipeline bad` with image `foo:latest` → schema rejects (pattern) — assert the create error.

- [ ] **Step 2: Run → FAIL.** - [ ] **Step 3: Implement** `Reconcile`: get Pipeline → finalizer → list TranscriptionJobs by pipeline (field index `spec.pipeline`) → set of namespaces → for each: ensure ConfigMap (create-or-update), ensure warm-up Job (create if absent), read its `Complete` condition → `warmed[ns]`; compute sha; set conditions; `Status().Update`. `SetupWithManager`: `For(&Pipeline{}).Owns(nothing).Watches(&TranscriptionJob{}, mapToPipeline).Watches(&batchv1.Job{}, mapWarmupToPipeline by label)`.

- [ ] **Step 4: PASS.** - [ ] **Step 5: Commit** `feat(controller): Pipeline reconciler — ConfigMap fan-out, warm-up gate, Ready condition (B63)`

---

### Task 8: TranscriptionJob reconciler

**Files:**
- Create: `internal/controller/transcriptionjob_controller.go`, `internal/controller/planner.go`
- Modify: `setup.go`
- Test: `internal/controller/transcriptionjob_controller_test.go`, `planner_test.go`

**Interfaces:**
- Consumes: `volumes.*`, `manifest.Fetch/Synthetic`, `jobspec.*`, `results.Store`, Pipeline `Ready` condition.
- Produces: `TranscriptionJobReconciler{client.Client; Scheme; Cfg Config; Store results.Store; Fetch func(ctx, url, max) manifest.Verdict; Recorder record.EventRecorder}` and `planner.Next(jobs []htrv1.TranscriptionJob, inflightByJob map[string]int, admittedGlobal int, cfg Config) []Pick` where `Pick{Job *TranscriptionJob; Volume string}` interleaves round-robin by `CreationTimestamp` across CRs in a namespace until each CR's window and the global cap are met.

- [ ] **Step 1: Planner unit test (pure)**

```go
func TestPlannerInterleavesAndCaps(t *testing.T) {
	a := tj("a", time.Unix(1, 0), "P", "P", "P"); b := tj("b", time.Unix(2, 0), "P", "P")
	picks := Next([]htrv1.TranscriptionJob{a, b}, map[string]int{}, 0, Config{Window: 20, MaxInflight: 3})
	got := []string{picks[0].Job.Name, picks[1].Job.Name, picks[2].Job.Name}
	if !reflect.DeepEqual(got, []string{"a", "b", "a"}) || len(picks) != 3 { t.Errorf("got %v", got) }
	picks = Next([]htrv1.TranscriptionJob{a}, map[string]int{"a": 20}, 0, Config{Window: 20, MaxInflight: 40})
	if len(picks) != 0 { t.Error("per-CR window respected") }
}
```

- [ ] **Step 2: Reconciler envtest scenarios** (each a ginkgo `It`, using a fake `Store` and a fake `Fetch`):
  1. *Validate & submit*: Pipeline Ready; TJ with 3 volumes (one `images:`) → status codes become `V` then `R:<pages>`; 3 Jobs exist with names `JobName(ns,p,v)`, owned by the TJ, `S3_PREFIX="ra/"`; the `images:` volume got `PutSource` called and `IIIF_MANIFEST_URL` = the synthetic id.
  2. *Resume*: fake Store returns `found=true` with matching sha for volume `R0001203` → code `D:12`, no Job.
  3. *Outcomes*: patch Job 1 `Complete` → `D:<pages>`, counter `htrflow_volumes_total{outcome="done"}` +1; patch Job 2 `Failed` with reason `PodFailurePolicy` (exit 13) → `F:1` final, failure appended with `ExitCode=13`; patch Job 3 `Failed` with `BackoffLimitExceeded` → Job deleted, code `P`, attempt recorded; after `maxAttempts` failures → `F:3`.
  4. *In-flight truth (D11)*: set a volume to `R:5` in status with no Job → next reconcile resets to `P`.
  5. *Claim (D14)*: a second TJ in the namespace with the same (pipeline, volume) while the first runs → `I:ClaimedBy=<first>` and an Event.
  6. *Pause*: `spec.paused=true` → owned Jobs deleted, phase `Paused`; unpause → resubmitted.
  7. *Delete*: delete the TJ → finalizer removes Jobs → object gone; Store never called with a delete (fake asserts).
  8. *Pipeline not Ready* → condition `Stalled/PipelineNotReady`, no Jobs.
  9. *Global cap*: `MaxInflight=1`, two TJs → exactly one Job created; a Job `Suspend=false` (Kueue admitted) counts; a suspended one does not.
  Expected before implementation: all FAIL.

- [ ] **Step 3: Implement** `Reconcile` in the spec's §4 order: get → finalizer → deletion → paused → pipeline ready → **step 0 in-flight truth** (list owned Jobs by label `htrflow.riksarkivet.se/job=<name>`; reconcile codes) → validate up to `ValidationsPerReconcile` pending volumes (`Fetch`; permanent → `I:<format>`, transient → keep `P`, requeue 5 m; record `Pages`) → resume check via `Store.Done` once per volume (cache: a volume checked gets `V`→`P`/`D` in the same pass; never re-HEAD a `P` that was checked — track with an annotation-free rule: resume check happens only in the transition `V→P`) → planner picks → create Jobs (`jobspec.BuildJob`, set `Suspend=true` so Kueue admits) → recompute counts/phase → `Status().Patch` with `client.MergeFrom` → Events. Job outcome handling in the same reconcile from the listed Jobs: `Complete` → `D`; `Failed` + `reason==PodFailurePolicy` → final `F`; `Failed` otherwise → read termination message from the pod (`pods/log` not needed; `status.containerStatuses[].state.terminated.message`) into `Failure.Reason`, delete Job, attempts+1 → `P` or `F`. `SetupWithManager`: `For(&TranscriptionJob{}).Owns(&batchv1.Job{}).Watches(&Pipeline{}, mapPipelineToJobs)`. Metrics via `sigs.k8s.io/controller-runtime/pkg/metrics` registry: `htrflow_volumes_total{namespace,pipeline,outcome}`, `htrflow_pages_total{namespace,pipeline}`, `htrflow_volume_seconds` histogram, `htrflow_jobs_inflight` gauge.

- [ ] **Step 4: PASS** (`make test`; budget: `scripts/loc-budget.sh` controller line must be ≤ 1500 — if over, split helpers into `internal/outcome/` before committing, do not raise the budget).

- [ ] **Step 5: Commit** `feat(controller): TranscriptionJob reconciler — validation, resume, fair submission, outcomes, pause/delete (B63)`

---

### Task 9: Read API for the status page

**Files:**
- Create: `internal/api/server.go`; Modify: `setup.go` (add as `mgr.Add(manager.RunnableFunc)`)
- Test: `internal/api/server_test.go`

**Interfaces:**
- Produces: `NewServer(c client.Reader, cfg Config) http.Handler`; `GET /api/v1/jobs` → `[]JobSummary{Namespace, Name, Pipeline, Phase, Counts, CreatedAt, ResultsBase}`; `GET /api/v1/jobs/{ns}/{name}?offset=0&limit=200` → `JobDetail{JobSummary; Failures; Volumes []VolumeView{ID, Code, ManifestURL, IIIFURL, ViewerURL, LogKey}}` where URLs are `ResultsBase + "/" + id + "/manifest.json"` etc. (convention, no S3 calls); `GET /healthz`. CORS: none (same origin via nginx). Read-only: any other method → 405.

- [ ] **Step 1: Failing httptest** using a fake `client.Reader` with two TJs; assert list order (newest first), paging (`limit=1&offset=1`), 404 on unknown, 405 on POST.
- [ ] **Step 2: FAIL.** - [ ] **Step 3: Implement** with `net/http` `ServeMux` (Go 1.22 patterns `GET /api/v1/jobs/{ns}/{name}`). - [ ] **Step 4: PASS.** - [ ] **Step 5: Commit** `feat(controller): read-only /api/v1/jobs projection for the status page (B63)`

---

### Task 10: Chart — controller Deployment, RBAC, NetworkPolicy; remove reconciler/pipelines/exampleJob

**Files:**
- Create: `charts/htrflow-batch/templates/controller.yaml`
- Delete: `templates/reconciler.yaml`, `templates/pipelines.yaml`, `templates/job-example.yaml`
- Modify: `values.yaml` (replace `reconciler.*`, `pipelines`, `exampleJob` with `controller.*`), `values.schema.json`, `templates/network.yaml` (controller egress: API server, S3, IIIF sources CIDRs from `network.sources`), `templates/NOTES.txt`, `Chart.yaml` (version 0.3.0), `charts/htrflow-batch/README.md`
- Test: `charts/htrflow-batch/ci/full-values.yaml` (create if the CI values file path differs — check `.dagger/checks.go:120`), `.dagger/checks.go` kubeconform run includes `crds/`

**Interfaces:**
- Consumes: CRDs from Task 2 in `crds/`; image `docker.io/riksarkivet/htrflow-controller@sha256:…` value `controller.image`.
- Produces: Deployment `htrflow-controller` (1 replica, args from `controller.*` values, ports 8080/8081/8082, restricted pod security, RO S3 credential Secret `controller.s3Secret` mounted like Jobs), ServiceAccount, ClusterRole+Binding (`transcriptionjobs`, `pipelines` + `/status`, `jobs` CRUD, `configmaps` CRUD, `events` create, `pods` get/list, `leases`), Service `htrflow-controller:8081`, NetworkPolicy allowing egress to API server (`network.apiServer.cidr`), S3 endpoint, `network.sources` CIDRs on 443, and ingress on 8081 from the viewer pods only.

- [ ] **Step 1: Failing render test** — add to `.dagger/checks.go` a `helm template` assertion function `checkChartRenders` that greps the output for `kind: Deployment` + `name: htrflow-controller` and asserts **no** `kind: CronJob`; and a NetworkPolicy assertion that the controller policy has an egress rule for every `network.sources[]` CIDR (the 2026-08-26 bug). Run `dagger call check-chart` → FAIL (CronJob present, no controller).
- [ ] **Step 2: Write `controller.yaml`** (Deployment/SA/RBAC/Service/NetworkPolicy), delete the three templates, update values/schema/NOTES. Values:

```yaml
controller:
  image: docker.io/riksarkivet/htrflow-controller@sha256:0000000000000000000000000000000000000000000000000000000000000000
  window: 20
  maxInflight: 40
  validationsPerReconcile: 50
  minDeadlineSeconds: 21600
  perPageSeconds: 30
  legacyLayout: false
  sourceTemplate: "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
  s3Secret: htr-controller-s3     # read-only + sources/ write
  resources: {requests: {cpu: 100m, memory: 128Mi}, limits: {cpu: "1", memory: 512Mi}}
```
- [ ] **Step 3: `helm template` locally with `network.apiServer.cidr=10.16.51.10/32`** → 16 kinds incl. `CustomResourceDefinition ×2`, `Deployment`, no `CronJob`; `kubeconform -strict -ignore-missing-schemas` passes. `scripts/loc-budget.sh` chart line ≤ 700 (devstack templates are still present until Plan B — if over, move `devstack-*.yaml` to `charts/htrflow-devstack/templates/` now, values `devStack.*` with it, and `README` note; that is allowed to happen in this task).
- [ ] **Step 4: Run `dagger call check-chart` → PASS.** - [ ] **Step 5: Commit** `feat(chart): controller Deployment/RBAC/NetworkPolicy and CRDs; reconciler, pipelines and exampleJob removed; 0.3.0 (B63)`

---

### Task 11: Build, publish, PoC targets, campaign conversion

**Files:**
- Modify: `.dagger/build.go` (`BuildController`), `.dagger/publish.go` (`case "controller"`, repository `riksarkivet/htrflow-controller`; delete `case "reconciler"`), `.github/workflows/publish.yml` (matrix row `controller`, remove `reconciler`), `.github/workflows/ci.yml` (run `make -C packages/controller test` + `scripts/loc-budget.sh`), `renovate.json` (gomod manager for `packages/controller`), root `Makefile` (`campaigns-apply`, remove `poc-push` reconciler parts, `warmup` target now = `kubectl get pipeline`)
- Create: `scripts/campaigns/convert.py`, `scripts/campaigns/test_convert.py`

**Interfaces:**
- Produces: `python3 scripts/campaigns/convert.py <campaigns-dir> [--namespace ra] [--out manifests/]` → `pipelines/<id>.yaml` (Pipeline CRs) and `jobs/<campaign>[-partN].yaml` (TranscriptionJob CRs, split every 10 000 volumes); `make campaigns-apply DIR=…` = convert + `kubectl apply -f`.

- [ ] **Step 1: Failing pytest for `convert.py`** — fixture campaign with a bare id, a `manifest:` volume, an `images:` volume, and 10 001 generated ids; assert two TranscriptionJob docs (`-part1` 10 000, `-part2` 1), one Pipeline doc with `spec.image` and `spec.steps`, volume entries preserved verbatim, `metadata.namespace` set. Run: `uv run pytest scripts/campaigns -q` → FAIL.
- [ ] **Step 2: Implement `convert.py`** (PyYAML, 80 lines). PASS.
- [ ] **Step 3: Dagger `BuildController`** = `golang:1.26` container, `go test ./...` (envtest via `setup-envtest use 1.34 --bin-dir`), then multi-stage build to distroless; `publish-docker --component controller` returns the digest like the others. Run: `dagger call build-controller --source=.` → exports an image; `dagger call checks` still green.
- [ ] **Step 4: Workflows + renovate edits; `gh workflow run publish.yml -f tag=v0.2.0`** after the version bump in `packages/wrapper/pyproject.toml` (tags are immutable; 0.2.0 marks the controller). Expected: four green jobs; note the controller digest for the chart default.
- [ ] **Step 5: Commit** `ci: build/publish htrflow-controller, drop reconciler; campaign → CR converter (B63)`

---

### Task 12: Delete the reconciler package and its traces; budgets green in CI

**Files:**
- Delete: `packages/reconciler/` (all), `.docker/htrflow-reconciler.dockerfile`, `docs/reference/reconciler.md`
- Modify: `pyproject.toml` + `uv.lock` (workspace member removed), `.dagger/*` (any `reconciler` reference), `Makefile`, `docs/how-it-works/campaigns.md`, `docs/reference/campaign-yaml.md` (rewrite as CRD reference: fields from Task 2, codes from Task 3), `docs/reference/index.md`, `zensical.toml` nav, `charts/htrflow-batch/README.md`, `.github/workflows/ci.yml` (add `scripts/loc-budget.sh` as a required step)
- Test: `grep -rn reconciler --include=*.py --include=*.go --include=*.yaml --include=*.toml . | grep -v docs/audits | grep -v docs/superpowers` returns nothing; `uv sync --frozen` ok; `uvx zensical build --clean` clean; `scripts/loc-budget.sh` passes for controller/wrapper/chart (frontend passes only after Plan B — mark that row `SKIP_FRONTEND=1` env in CI until Plan B Task 1 lands, and remove the env in Plan B).

- [ ] **Step 1: Run the grep → non-empty (failing state).** - [ ] **Step 2: Delete and rewrite.** - [ ] **Step 3: `uv sync --frozen && uv run pytest && dagger call checks && scripts/loc-budget.sh`** → green. - [ ] **Step 4: Commit** `refactor: remove the CronJob reconciler; docs describe the CRDs (B63)`

---

### Task 13: E2E on the PoC node

**Files:**
- Create: `docs/development/e2e-controller.md` (the run log, filled in during the task)
- Modify: `.env` (PoC constants), `Makefile` (`e2e-controller` target that runs the steps below non-interactively where possible)

**Interfaces:** consumes everything; produces the "Klart när" evidence for B63.

- [ ] **Step 1: Quiet point** — `kubectl -n htr-batch get jobs` shows none running; `kubectl -n htr-batch delete cronjob htr-reconciler`.
- [ ] **Step 2: Install chart 0.3.0** on the PoC (`make install` with `--set controller.legacyLayout=true` so existing `<pipeline>/<volume>/` results resume, `--set network.apiServer.cidr=…`). Expected: `kubectl get crd | grep htrflow` → 2; `kubectl -n htr-batch get deploy htrflow-controller` Ready.
- [ ] **Step 3: Convert and apply** the PoC campaigns repo: `make campaigns-apply DIR=~/htr-campaigns`. Expected: `kubectl get pipelines` Ready=True after the warm-up (≈ minutes); `kubectl -n htr-batch get tj` shows the campaign(s), previously finished volumes `D:<pages>` **without new Jobs** (resume), the rest progressing.
- [ ] **Step 4: 50-volume run** — a fresh TJ with 50 unprocessed volumes; wait; expected all `D`, viewer opens each from the status page (Plan B) or via the `iiif.json` URL; `aws s3 ls s3://htr-results/status/` shows **no new** `status.json`/`attempts.json` writes (compare mtimes).
- [ ] **Step 5: Pause/delete** — `kubectl patch tj <name> --type merge -p '{"spec":{"paused":true}}'` mid-run: Jobs gone within 60 s, done pages remain in S3, phase `Paused`; unpause resumes without redoing done volumes; `kubectl delete tj` GCs Jobs, S3 untouched.
- [ ] **Step 6: Record results in `docs/development/e2e-controller.md`, commit** `docs: controller E2E on the PoC — resume, pause, delete, no status files (B63)`; link the commit on #2978 (`Custom.Commits` + Hyperlink, see the using-ra-azure-devops skill).

---

## Self-review (done while writing)

- Spec coverage: D1–D21 → Tasks 1–12; §4 steps 0–9 → Task 8; §5 → Task 9; §6 → Tasks 10–11; §7 (wrapper/frontend) → **Plan B**; §8 → each task's tests + Task 13; §9 rollout → Task 13 and Plan B docs; §1 budgets → Task 1 script, enforced in Task 12.
- Type consistency: `Config` fields (Task 1) = flags = `jobspec.Params` inputs (Task 5) = chart values (Task 10). `volumes.Code` string format (Task 3) is what Task 8 writes and Task 9 exposes.
- Gaps: none known. Plan B: `docs/superpowers/plans/2026-08-31-transcriptionjob-controller-plan-b.md`.
