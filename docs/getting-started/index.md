# Prerequisites

## Kubernetes cluster

You need a Kubernetes cluster with **Kueue CRDs already installed** —
the [helm chart](deploy.md) renders `ResourceFlavor` / `ClusterQueue` /
`LocalQueue` objects but does not install the Kueue controller or its CRDs
itself. A GPU node pool with the NVIDIA device plugin (and, for the stock
htrflow image, a GPU no newer than Ada — see
[How it Works](../how-it-works/architecture.md)) is assumed.

## Bare-k3s PoC path: host gotchas

The 2026-07-27/28 PoC ran on bare k3s on a shared host and hit three host
issues before it stayed up. If you're replaying the PoC on similar
infrastructure (a single systemd k3s node, possibly shared with other
users' Docker workloads), apply these up front — they are already
persisted on the PoC host.

### inotify limits

`fs.inotify.max_user_instances=128` (the Linux default) was exhausted by
root's other services, so kubelet silently never registered the node — no
error, just an absent node. Fix, persisted in
`/etc/sysctl.d/99-k3s-inotify.conf`:

```
fs.inotify.max_user_instances=1024
fs.inotify.max_user_watches=1048576
```

### node-ip pin (IPv6-only hostname)

If the node's hostname resolves IPv6-only, k3s needs an explicit IPv4
`node-ip`. Persisted in `/etc/rancher/k3s/config.yaml`:

```yaml
node-ip: 10.16.51.53
```

### Eviction thresholds (DiskPressure on big shared disks)

kubelet's *default* eviction threshold is percentage-based
(`nodefs.available<10%`). On a large shared disk (e.g. 7.4 TB) that's
hundreds of gigabytes of slack that can never be freed by evicting your
own pods — the node gets tainted `disk-pressure:NoSchedule` and stays
that way for hours. Use absolute thresholds instead, persisted in
`/etc/rancher/k3s/config.yaml`:

```yaml
kubelet-arg:
  - "eviction-hard=nodefs.available<25Gi,imagefs.available<25Gi"
```

Data on host-path PVCs survives a DiskPressure eviction, but evicted-pod
husks need a manual delete before their Deployments respawn them.

## Local smoke stack (no cluster needed)

If you just want to exercise the wrapper and the web front without a
Kubernetes cluster at all, see the `make compose-up` / `make
compose-smoke` path in [Deploy](deploy.md#local-compose-smoke-stack) —
it needs only Docker.
