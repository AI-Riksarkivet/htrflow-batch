# Queueing

[Kueue](https://kueue.sigs.k8s.io/) is the only part of this system that
decides **when** a campaign may run. It owns admission and GPU quota, and
knows nothing about HTR, IIIF or S3. A campaign is one Indexed Job, and so
**one Kueue Workload, not one per index**. Most of this page follows from
that fact.

Kueue is a job-level admission controller and quota manager. It is **not** a
pod scheduler. It never picks a node or binds a pod, and campaign pods keep
`schedulerName: default-scheduler`. **Kueue decides when, kube-scheduler
decides where.** The Kueue release the Makefile installs is `KUEUE_VERSION`.

## Topology

![The objects: LocalQueue, ClusterQueue and ResourceFlavor from the chart, the campaign Job from the converter, its Workload in Kueue, and the pods](../assets/diagrams/queue-objects.svg)

The chart renders three queue objects from `queue.*`, plus one priority
class per `queue.priorityClasses` entry
([Chart Values](../reference/chart.md)):

| Object | Name | What it carries |
|---|---|---|
| `ResourceFlavor` | `queue.flavor` (default `default-flavor`) | Nothing. With no `nodeLabels` or `nodeTaints`, Kueue injects no `nodeSelector` at admission |
| `ClusterQueue` | `<queue.name>-cq` (default `htr-batch-cq`) | One resource group, one flavor, `nominalQuota` per covered resource, and a `namespaceSelector` on `kubernetes.io/metadata.name`. A ClusterQueue is cluster-scoped, so the selector keeps any other namespace from pointing a LocalQueue at this quota |
| `LocalQueue` | `queue.name` (default `htr-batch`), in the release namespace | `spec.clusterQueue` pointing at the ClusterQueue. Jobs name this queue in their `queue-name` label |
| `WorkloadPriorityClass` | one per `queue.priorityClasses` entry (default `htr-interactive` 1000, `htr-bulk` 0, `htr-idle` -10) | `value` and a `description`. Cluster-scoped, so the names are the same for every namespace. A campaign names one in its `priority-class` label |

The default quota is **cpu 4, memory 8 Gi, `nvidia.com/gpu` 1**, which is
exactly one wrapper pod. Kueue marks a Workload inadmissible unless the
ClusterQueue covers every resource its pod *requests*. So the covered list
and the pod's requests have to change together. Raise the quotas to run more
volumes at once.

Everything else on the ClusterQueue is Kueue's own default:

- `queueingStrategy: BestEffortFIFO`
- `preemption.withinClusterQueue: Never`
- `stopPolicy: None`
- no `cohort`

Priority is where the classes come in. Kueue orders the queue by class value
first, higher first, and by creation time within a value, so a campaign on
`htr-interactive` is admitted before every waiting campaign on `htr-bulk`.
None of this evicts a running campaign: with `withinClusterQueue: Never` a
higher class goes ahead of what is *waiting*, never of what is *running*.

The converter puts two Kueue **labels** on each campaign Job
(`render._campaign_job`), and no annotations:

- `kueue.x-k8s.io/queue-name: <queue from converter.yaml>`, always.
- `kueue.x-k8s.io/priority-class: <priority>`, only when the campaign sets
  `priority:`. A Job with no label ranks at 0, which is why `htr-bulk` sits
  at 0: naming it is the same as leaving the field out.

A cluster with several kinds of GPU node gives each group its own flavor with
`nodeLabels` and covers the flavors separately. That is a values change, not
a chart change.

## What a Workload holds

Kueue creates one Workload per Job, named `job-<campaign>-<hash>`, owned by
the Job. Its label `kueue.x-k8s.io/job-uid` is the link `cluster.py` selects
it by. `spec.active` is the pause lever. `spec.podSets[0]` copies the pod
template with a `count` equal to the Job's **`parallelism`**, not its
`completions`: a podSet describes the pods that exist at once. Quota counts
pod **requests**: the wrapper requests 8 Gi of memory with a 16 Gi limit, and
8 Gi is what the quota sees.

## The webhooks and reconcilers Kueue adds

- **`mjob.kb.io`** (mutating, CREATE, `failurePolicy: Fail`) sets
  `spec.suspend: true` on a Job with a queue label, so it cannot run before
  Kueue has seen it.
- **`vjob.kb.io`** (validating) rejects changes Kueue cannot honour on a
  managed Job. It does **not** check the `priority-class` label: a Job naming
  a class that does not exist gets no Workload and no event, and stays
  "Queued" for ever. That is why `htrflow-campaigns validate` refuses a
  `priority:` outside `converter.yaml`'s `priority_classes`, the list that
  mirrors the chart's.
- **The Job reconciler** creates the Workload, sets `spec.suspend: false` on
  admission, and marks the Workload `Finished` when the Job ends.
- **The scheduler/quota reconciler** picks the next Workload per
  ClusterQueue, assigns flavors and reserves quota.

## The admission cycle

![A campaign's life: rendered, applied, queued, running, then done or failed, and paused and back to queued](../assets/diagrams/campaign-lifecycle.svg)

Step by step, with the controller that acts at each step:

![The admission cycle: apply, the webhook suspends the Job, Kueue creates and admits the Workload, the Job controller creates pods, the scheduler binds them, the kubelet runs them, and the quota is released](../assets/diagrams/seq-admission.svg)

Notes on the steps that are not obvious from the diagram:

- **Queue order.** With `BestEffortFIFO`, an older Workload that cannot be
  admitted does **not** block a newer one that fits.
- **`QuotaReserved`, then `Admitted`.** Quota reservation is the scheduling
  decision, and admission is the authorisation that follows it.
- **Binding.** `kube-scheduler` places each pod using `runtimeClassName`, the
  GPU request, and any `nodeSelector` and `tolerations` that
  `render._scheduling` wrote. The empty flavor adds nothing.

**Admission is per Job, not per index.** Steps 8 to 11 repeat without asking
Kueue again: Kubernetes replaces each finished pod with the next index. The
campaign at the front of the queue therefore holds its GPU until its last
index is done.

The warm-up Job is outside all of this. `manifests/warmup-job.yaml` carries
no `queue-name` label, so Kueue never sees it. It requests `cpu: 2`,
`memory: 4Gi` and **no `nvidia.com/gpu`**, so it runs alongside an admitted
campaign pod. It still gets the campaign Job's `runtimeClassName`,
`nodeSelector` and `tolerations` from `render._scheduling`, so it lands where
the model cache PVC can be mounted
([The Wrapper](wrapper.md#the-model-cache)).

## Pause

`suspend: true` in the campaign file renders `spec.suspend: true`, but that
field cannot be the lever. **Kueue owns `spec.suspend` on a Job it manages**,
and sets it back within seconds. The lever that holds is the Workload's
`spec.active`. Setting it to `false` evicts a running Workload and stops it
being requeued.

So every apply runs `cluster.sync_pause` after it has applied the objects
and before it prunes:

1. It finds the Workload by `kueue.x-k8s.io/job-uid`.
2. Where `spec.active` disagrees with the intent in git, it sends the merge
   patch `{"spec": {"active": <want>}}`. A Workload that already agrees gets
   no patch, so re-applying an unchanged repo changes nothing.
3. A brand-new paused campaign has no Workload for a moment, and that moment
   is exactly when Kueue would admit it. So the apply polls for
   `--pause-wait` seconds and exits non-zero if no Workload appears.

How the apply pauses a Job the API server refused, and what it does when a
Workload cannot be patched, is in
[htrflow-campaigns CLI](../reference/cli.md).

Deactivating a Workload evicts its pods and keeps every completed index. The
Job then reads `suspend: true`. Reactivating continues from the next index.

Resuming has one trap in server-side apply. A campaign paused before Kueue
ever admitted it (a full queue) has `spec.suspend: true` owned by the
apply's field manager alone. The resuming render no longer carries the
field, and a field its last owner stops sending is removed, so the API
server would put the default back: `false`, a Job that starts at once with
no admission. The apply therefore hands the field to a second field manager
of its own, `htrflow-campaigns-suspend`, first. That apply sends the same
value and is never forced, so the field stays `true` until Kueue admits the
reactivated Workload and flips it.

Pausing costs `list` and `patch` on `workloads` (`templates/apply-rbac.yaml`,
when the apply runs in-cluster). It also relies on Kueue behaviour that Kueue
does not promise to keep. The campaign-file side is in
[Campaign & Pipeline YAML](../reference/campaign-yaml.md).

## The window

Kueue supports partial admission. With the annotation
`kueue.x-k8s.io/job-min-parallelism` on a Job, Kueue may start it with fewer
pods than it asked for, once borrowing and preemption are exhausted. The
converter does not use this. Partial admission rewrites `spec.parallelism` on
the live Job, and the validating webhook then refuses every later apply of
the unchanged rendered file.

The converter clamps at render time instead:
`parallelism = min(campaign window, converter.yaml window)`, with
`converter.yaml`'s `window` as the per-cluster cap. The whole podSet `count`
must fit the quota, or nothing starts. Set `converter.yaml`'s `window` so
that `window × per-pod requests` fits `nominalQuota`.

Changing the window of a campaign that is running restarts it. Kueue counts
an admitted Job's pods as `min(parallelism, completions)`, and a Job whose
count no longer matches its Workload has every pod stopped and is queued
again. So `apply` compares that count on each live campaign Job that is not
suspended and has not ended with the render's, and when they differ it sends
nothing. The way through is to pause the campaign, change its window, then
resume it: a suspended Job's Workload is updated in place.

## Many campaigns at once

Fifty campaigns against a one-GPU quota make fifty Jobs and fifty
Workloads: one admitted, forty-nine counted in `pendingWorkloads`. With a
podSet of one nothing smaller can slip through, so it behaves as a plain
queue, and the campaign at the front owns the GPU until its last index
finishes, whether that takes minutes or weeks.

## Preemption and cohorts

Both are off. The ClusterQueue keeps Kueue's defaults (every preemption
policy `Never`, `stopPolicy: None`) and has no `spec.cohort`, so there is no
quota to borrow or lend.

With preemption on, a higher-priority Workload can evict an admitted one. The
victim gets `Evicted` with reason `Preempted`, and a `Preempted` condition
naming what displaced it. For this system that means stopping a running
volume mid-transcription. The volume survives, because the wrapper resumes
from its published pages, but it is a policy choice rather than a switch. A
cohort would let the queue borrow another tenant's idle quota. The chart
ships the `WorkloadPriorityClass` objects preemption would rank by; what it
does not do is turn preemption on.

## Who owns which field

| Field | Owner | Note |
|---|---|---|
| `job.spec.suspend` | **Kueue** | Set `true` by the webhook at CREATE, and `false` by the reconciler at admission. The apply sets it `true` for a paused campaign, and holds it there through a resume (see [Pause](#pause)) |
| Job label `kueue.x-k8s.io/queue-name` | converter | Effectively immutable once admitted: removing it releases no quota and blocks resuming |
| `completions`, `parallelism`, `backoffLimitPerIndex`, `maxFailedIndexes`, `podFailurePolicy`, `ttlSecondsAfterFinished` | converter | Kueue reads `parallelism` into the podSet and ignores the rest |
| `pod.spec.containers[*].resources.requests` | converter | The numbers quota is counted in |
| `job.status.completedIndexes`, `failedIndexes`, conditions | Kubernetes Job controller | The only progress the status page reads |
| `workload.spec.active` | `htrflow-campaigns apply`, by patch | The pause lever |
| `workload.spec.podSets`, `status.admission`, conditions | Kueue | Nothing else writes these |
| Pod placement and binding | kube-scheduler | Kueue contributes only flavor `nodeLabels`, and the default flavor has none |

## Failure interplay

Kueue watches a Job's completion and failure, and nothing finer.

- **A pod failure** is invisible to Kueue. The Job controller retries the
  index under `backoffLimitPerIndex`, and the Workload keeps its admission,
  and its GPU, throughout.
- **`FailIndex`** (the `podFailurePolicy` rules for exit 13 from `wrapper`
  or `warmup-wait`) fails the index with no retry. Kueue does not see it
  unless `maxFailedIndexes` is exceeded and the Job goes `Failed`. The
  Workload is then `Finished` and the quota is released.
- **`activeDeadlineSeconds`** sits on the **pod template**, so the kubelet
  kills the pod at the deadline and the Job retries the index. It is not
  Kueue's `maximumExecutionTimeSeconds`, which is not set.
- **TTL.** `ttlSecondsAfterFinished` deletes the Job a week after it
  finishes — `converter.yaml`'s default, which a pipeline may set for
  itself — and the Workload, as an owned child, goes with it.

The whole failure model is in [Failure Handling](failure-handling.md).

## Reading the queue

The commands for a campaign that is not admitted, the Workload conditions to
look for, and what "Queued" means on a campaign card are in
[Troubleshooting](../getting-started/troubleshooting.md).

## Known limits

- **The pause patches Kueue's Workload directly.** `sync_pause` needs
  `patch` on `workloads` and relies on eviction by `spec.active`, which Kueue
  does not promise to keep. If a Kueue release changes that, the symptom is a
  campaign git says is paused that keeps running.
- **Priority orders the queue; preemption is off.** A campaign's
  `priority:` decides who is admitted *next* and never evicts a running
  campaign. While one campaign holds the whole quota, "next" is when that
  campaign's quota comes back. Preemption would stop a running volume
  mid-transcription (resume makes that survivable), and turning it on is a
  product decision rather than a switch.
- **`window` is not checked against the quota.** Without partial admission
  the whole podSet must fit. The shipped defaults (converter `window` 20
  against a one-GPU quota) render `parallelism: 20`, which is inadmissible
  forever and reads only as "Queued".
- **A reaped campaign is remembered by a ConfigMap, not by Kueue.** The
  Workload is deleted with the Job, so the queue itself remembers nothing.
  What stops the next apply from running every index again is the campaign's
  status ConfigMap: `apply` writes how the Job ended before it applies
  anything, and then leaves a finished campaign alone
  ([Campaigns](campaigns.md#the-record-a-campaign-leaves)). A campaign's
  volume list is append-only, so a finished campaign is never run again: new
  volumes go in a new campaign.
