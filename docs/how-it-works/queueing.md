# Queueing (Kueue)

## Topology

Namespace `htr-batch`. Standard three objects; the chart renders them from
`queue.*` ([Chart Values](../reference/chart.md#queue-queue)). The YAML below
is **illustrative** — a two-GPU, Ada-flavored layout — not what the chart
renders by default (one flavor `default-flavor`, quota cpu 4 / 8 Gi / 1 GPU):

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: gpu-ada
spec:
  nodeLabels:
    gpu-group: ada          # HTR owns ada; Gemma owns blackwell
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: ClusterQueue
metadata:
  name: htr-batch-cq
spec:
  namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: htr-batch
  resourceGroups:
  - coveredResources: [cpu, memory, nvidia.com/gpu]
    flavors:
    - name: gpu-ada
      resources:
      - name: nvidia.com/gpu
        nominalQuota: 2       # tunable: max concurrent volumes
      - name: cpu
        nominalQuota: 8
      - name: memory
        nominalQuota: 32Gi    # 2 × 16 Gi limits — streaming keeps pods small
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: LocalQueue
metadata:
  name: htr-batch
  namespace: htr-batch
spec:
  clusterQueue: htr-batch-cq
```

Jobs carry `kueue.x-k8s.io/queue-name: htr-batch`; Kueue's webhook
suspends them on creation and unsuspends as quota frees. Submit 200 volumes → exactly N run, the rest
wait in FIFO order (`kubectl get workloads -n htr-batch`). If Jobs sit
`queued` while the GPU is idle, check the Kueue controller first — a dead
Kueue looks exactly like a busy GPU.

No preemption, no cohorts in Phase 1 — first knobs to turn when sharing with
other tenants.

