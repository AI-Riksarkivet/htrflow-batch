# Prerequisites

## Kubernetes cluster

You need a Kubernetes cluster with **Kueue installed** (`make install-kueue`
applies the upstream v0.18.1 release manifests server-side; the version is
`KUEUE_VERSION` in the `Makefile`) —
the [helm chart](deploy.md) renders `ResourceFlavor` / `ClusterQueue` /
`LocalQueue` objects but does not install the Kueue controller or its CRDs
itself. A GPU node pool with the NVIDIA device plugin (and, for the stock
htrflow image, a GPU no newer than Ada — see
[How it Works](../how-it-works/architecture.md)) is assumed.

## Local smoke stack (no cluster needed)

If you just want to exercise the wrapper and the web front without a
Kubernetes cluster at all, see the `make compose-up` / `make
compose-smoke` path in [Deploy](deploy.md#local-compose-smoke-stack) —
it needs only Docker.
