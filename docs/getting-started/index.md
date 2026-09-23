# Prerequisites

What a cluster needs before [Deploy](deploy.md). To look at the wrapper and
the viewer without a cluster first, see [Try it](try-it.md).

## Kubernetes cluster

- **Indexed Jobs with per-index retries.** A campaign is a `batch/v1` Job
  with `completionMode: Indexed`, `backoffLimitPerIndex` and a
  `podFailurePolicy` that uses `FailIndex`. The cluster's Kubernetes release
  must support all three.
- **A CNI that enforces NetworkPolicy.** The chart renders a namespace-wide
  default deny with explicit allows. A CNI that ignores NetworkPolicy leaves
  every pod unrestricted without an error.
- **A StorageClass for the model cache.** The chart renders a PVC
  (`modelCache.*`, `ReadWriteOnce` by default). `ReadWriteOnce` pins every
  campaign pod to the node that holds the volume, which is fine with one GPU
  node. With more than one, use a `ReadWriteMany` class.

## Kueue

Kueue owns queueing and GPU quota. The chart renders the `ResourceFlavor`,
`ClusterQueue` and `LocalQueue` objects, but not the Kueue controller or its
CRDs:

```bash
make install-kueue
```

This installs or upgrades Kueue's official Helm chart (version
`KUEUE_VERSION` in the `Makefile`), so it is safe to re-run. A Kueue
installed from the upstream manifests must be moved to the chart once, since
Helm will not adopt objects it did not create.
[Queueing](../how-it-works/queueing.md) explains how a campaign is admitted.

## Kyverno

The chart's `security.policies` are Kyverno `ClusterPolicy` objects. They are
the only thing that enforces digest pins, the image allow-list and model
revisions on everything the namespace admits:

```bash
make install-kyverno
```

This installs the Kyverno Helm chart into its own `kyverno` namespace. The
version is `KYVERNO_CHART_VERSION` in the `Makefile`. Without Kyverno, leave
`security.policies.enabled` off, but know that then nothing enforces those
rules ([Security](../how-it-works/security.md)).

## GPU nodes

- **An NVIDIA GPU that the wrapper image's torch build supports** (torch is
  pinned per architecture, see [Releasing](../development/releasing.md#one-dockerfile-every-architecture)).
- **The NVIDIA device plugin**, so nodes advertise `nvidia.com/gpu`.
- **A RuntimeClass** for GPU pods. The converter sets `runtimeClassName`
  from `runtime_class` in the campaigns repo's `converter.yaml` (default
  `nvidia`). An empty value leaves the field out. `node_selector` and
  `tolerations` in the same file steer the pods onto GPU nodes.

## S3-compatible bucket

- One results bucket, and a Secret in the release namespace that holds its
  credentials. The wrapper is the only writer.
- Anonymous `GetObject` on the result keys, plus CORS for the web front's
  origin. The browser fetches manifests, ALTO and run logs straight from the
  bucket.
- A **results base URL** that browsers can reach. Every link in a published
  manifest is built from it.

[Deploy](deploy.md#s3-secret-bucket-policy-and-cors) has the Secret format,
the key prefixes and the CORS rule.

## Container registry

Everything the namespace runs is pinned by digest. The published images are
`docker.io/riksarkivet/htrflow-batch` (the wrapper),
`docker.io/riksarkivet/htrflow-web` (the web front) and
`docker.io/riksarkivet/htrflow-campaigns` (the converter, for the Argo CD
apply hook). To build and push your own, see
[Releasing](../development/releasing.md). Whichever registry you use goes
into `security.allowedImageRepos`.

## IIIF source

Volumes come from IIIF Presentation manifests (v2 or v3) over http(s), or
from plain lists of image URLs. Campaign pods fetch pages from the source
directly, so its address range goes into `network.iiifCidrs`.

## Network egress

- **Campaign pods** reach DNS, S3 and the IIIF source, and nothing else.
- **Warm-up pods** download models from the Hugging Face Hub, so they need
  public egress. The chart grants it while still blocking the pod, service
  and node ranges.

## Tools

`kubectl`, `helm` and `make` on the machine you deploy from. `uv` wherever
the converter runs: your machine or the campaigns repo's CI. Docker only to
build images or run [the compose stack](try-it.md#without-a-cluster-docker-compose).
