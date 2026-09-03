# Kueue: using it better, and whether to add KAI Scheduler (2026-09-02)

Assessment and handoff. Companion to the other `2026-09-02-*.md` files in
this directory. Against HEAD `9e074e0` on `b63-indexed` and the live PoC
cluster (one arm64 GB10 node, one GPU, k3s v1.35.5, Kueue v0.18.1 with
only the `batch/job` integration enabled).

Verdict in one line: Kueue is wired correctly but used as a single FIFO
gate; five of its features would pay for themselves, in the order below.
GPU time-slicing was considered and deferred (decision 2026-09-02, item
3). KAI Scheduler is not worth adding now; the conditions under which it
would be are at the end.

---

## 0. How the pieces fit (background)

**Kueue's model.** A Job carries the label `kueue.x-k8s.io/queue-name`.
Kueue's webhook sets `spec.suspend: true` on creation, creates a
`Workload` object describing the Job's pod sets and their resource
requests, and admits the Workload when a `ClusterQueue` (via the
namespace's `LocalQueue`) has enough *nominal quota* in some
`ResourceFlavor`. Admission means Kueue flips `suspend` to `false` and the
Kubernetes Job controller starts pods; the pod scheduler (kube-scheduler)
then places them. Kueue never places pods itself. Quota is counted from the
pod template's requests times `parallelism`, so an Indexed Job with
`parallelism: 4` and one GPU per pod needs four GPUs of quota to be
admitted at all.
→ https://kueue.sigs.k8s.io/docs/concepts/ (concepts index),
https://kueue.sigs.k8s.io/docs/concepts/workload/

**Why suspend is safe for this workload.** Suspending a Job deletes its
running pods but keeps `status.completedIndexes`; resuming runs only the
remaining indexes. The wrapper resumes a volume at page level from S3. So
a preempted or paused campaign loses at most the pages in flight per pod.
→ https://kubernetes.io/docs/concepts/workloads/controllers/job/#suspending-a-job

**What the repo already does right.**
- Pause is enforced by patching `Workload.spec.active=false`
  (`packages/converter/src/htrflow_converter/cli.py:_pause_sync`), which
  is the lever Kueue honours; `spec.suspend` on the Job is intent only.
- The warm-up Job has no queue label and requests no GPU
  (`CUDA_VISIBLE_DEVICES=""`), so it never competes for GPU quota and
  cannot deadlock against the campaign that waits for its marker.
- `ClusterQueue.namespaceSelector` pins the quota to the release namespace
  (audit S9).
- Partial admission (`kueue.x-k8s.io/job-min-parallelism`) is off on
  purpose: Kueue rewrites `spec.parallelism` on the live Job and its
  webhook then rejects re-applying the unchanged rendered file
  (`docs/how-it-works/campaigns.md`, "No job-min-parallelism").

**What is provisioned today.** `charts/htrflow-batch/templates/kueue.yaml`:
one `ResourceFlavor` (`default-flavor`), one `ClusterQueue`
(`htr-batch-cq`, nominal quota from `values.queue.resources`, 1 GPU on the
PoC), one `LocalQueue` (`htr-batch`). No cohort, no
`WorkloadPriorityClass`, no preemption policy, no fair sharing, no
`stopPolicy`.

---

## 1. Finish the priority lanes (D13 is half built)

**Today.** `render.py:142` puts `kueue.x-k8s.io/priority-class: <name>` on
the Job when a campaign sets `priority:`. Nothing in the chart creates a
`WorkloadPriorityClass`, so on the PoC the label is dangling: Kueue cannot
resolve the class when it builds the Workload, the Job stays suspended,
and a campaign with `priority:` set does not run at all (confirm on the
PoC: set `priority: htr-bulk` on a test campaign and watch
`kubectl -n htr-batch describe job` events). `docs/roadmap/open-items.md`
row D13 says "a Kueue WorkloadPriorityClass of that name must exist" —
nothing makes it exist.

**Do.** In the chart, ship two classes and enable in-queue preemption:

```yaml
apiVersion: kueue.x-k8s.io/v1beta2   # same API version as kueue.yaml
kind: WorkloadPriorityClass
metadata: { name: htr-interactive }
value: 1000
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: WorkloadPriorityClass
metadata: { name: htr-bulk }
value: 100
```

and on the ClusterQueue:

```yaml
spec:
  preemption:
    withinClusterQueue: LowerPriority
```

Names and values in `values.yaml` (`queue.priorityClasses`), not in code.
Make the converter's `validate` refuse a `priority:` that is not in the
configured list, so the dangling-label failure cannot recur.

**Effect.** An `htr-interactive` volume arriving while an `htr-bulk`
campaign holds the GPU makes Kueue evict the bulk Workload (Job suspended,
pods deleted), admit the interactive one, and re-admit the bulk campaign
when quota frees. Completed indexes are kept; the interrupted volume
resumes at page level. The eviction sets a `DisruptionTarget` condition on
the pod, which the Job's `podFailurePolicy` already ignores, so preemption
does not spend an index retry.

**Verify.** Two campaigns on the PoC, bulk first; the interactive one's
pod starts within Kueue's reconcile period; the bulk Job shows
`suspend: true` and later resumes; no `failedIndexes`.

→ https://kueue.sigs.k8s.io/docs/concepts/workload_priority_class/
→ https://kueue.sigs.k8s.io/docs/concepts/preemption/

## 2. Global pause with `stopPolicy`

**Today.** Pausing is per campaign in git, enforced per Workload. There is
no "stop everything for maintenance" lever except pausing every campaign.

**Do.** Document and, if useful, expose in `values.yaml`:

```yaml
spec:
  stopPolicy: Hold          # stop admitting new workloads
  # stopPolicy: HoldAndDrain  # also evict the admitted ones
```

on the ClusterQueue (cluster-wide) or the LocalQueue (this namespace).
Removing the field resumes. The converter's per-campaign pause stays as
is; the two compose.

**Explanation.** `Hold` lets running pods finish and admits nothing new;
`HoldAndDrain` also suspends the admitted Jobs (same page-level recovery
as above). Both are reversible with one field change and need no converter
run.

→ https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/ ("StopPolicy")
→ https://kueue.sigs.k8s.io/docs/concepts/local_queue/

## 3. GPU time-slicing — deferred (decision 2026-09-02)

**Not now.** Do not configure device-plugin time-slicing or raise the GPU
quota above the physical count. Quota stays 1 GPU on the PoC; a small
volume waits for the running one (item 1's priority preemption is the
lever for urgent work, not sharing).

**Kept for the record.** The NVIDIA device plugin can advertise one GPU
as N `nvidia.com/gpu` units (`sharing.timeSlicing.resources[].replicas`),
and Kueue would count them unchanged. It buys latency for small jobs, not
throughput, and gives no memory or fault isolation between pods. If it is
ever revisited, measure first: the same campaign at replicas 1 and 2,
comparing `pages_per_second` and `gpu_stall_seconds` in `manifest.json`.
→ https://github.com/NVIDIA/k8s-device-plugin (README, "Shared Access to
GPUs with CUDA Time-Slicing")

## 4. Fairness between organisations (T05) with cohorts and fair sharing

**Today.** One queue for everyone. Story T05 asks for per-organisation
quotas, borrowing, reclaim, and proportional sharing.

**Do.** Kueue 0.18 has all of it natively:
- One `ClusterQueue` per organisation, all with `spec.cohort: htr`
  (or a `Cohort` object for a hierarchy), each with its nominal quota;
  unused quota is borrowable within the cohort.
- `spec.preemption.reclaimWithinCohort: LowerPriority` (or `Any`) so an
  organisation gets its nominal share back when it needs it.
- Enable fair sharing in the Kueue configuration
  (`fairSharing.enable: true`) and give each ClusterQueue a
  `spec.fairSharing.weight`; pending workloads are then ordered by
  dominant-resource share, not arrival time.
- One `LocalQueue` per organisation namespace; the converter already
  selects the queue via `converter.yaml`'s `queue:` (`models.py:225`), so
  per-organisation converter configs are the only change on that side.

Monthly page quotas (T05) are not a Kueue concept; count them in the
converter at render (page counts come from the pre-flight in the wrapper
ideas file) or in the API.

→ https://kueue.sigs.k8s.io/docs/concepts/cohort/
→ https://kueue.sigs.k8s.io/docs/concepts/preemption/ (reclaim, borrowing,
fair sharing sections)
→ https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/ (cohort, borrowing
limits, flavor fungibility)

## 5. Put the queue on the dashboard (B59)

**Today.** `values.yaml` warns that a dead Kueue "looks exactly like a busy
GPU" (O17). Nothing shows queue state.

**Do.** Kueue exports Prometheus metrics from the controller manager:
`kueue_pending_workloads`, `kueue_admitted_active_workloads`,
`kueue_cluster_queue_resource_usage`, `kueue_cluster_queue_nominal_quota`,
`kueue_admission_wait_time_seconds`, `kueue_evicted_workloads_total`.
Add a row to story B59's Grafana dashboard-as-code: pending vs admitted,
GPU quota usage, admission wait p50/p95, evictions by reason. Also alert
on `up{job="kueue-controller-manager"} == 0`.

→ https://kueue.sigs.k8s.io/docs/reference/metrics/

## 6. Things to hold off, and one trap

- **Partial admission / elastic jobs.** Keep it off for the documented
  reason. Kueue's newer "elastic jobs" (workload slices, alpha) allow
  changing `parallelism` of an admitted Job without the rewrite problem;
  revisit when the production cluster has more than one GPU node.
  → https://kueue.sigs.k8s.io/docs/tasks/run/jobs/ (partial admission)
- **`waitForPodsReady` trap.** This Kueue option evicts and requeues a
  Workload whose pods are not Ready within a timeout. Campaign pods sit in
  the `warmup-wait` init container until `/data/warmup/<pipeline>.done`
  exists — a first warm-up can take many minutes. Enabling it with the
  default timeout would requeue every campaign during a cold warm-up. Do
  not enable it, or set its timeout well above the warm-up time.
  → https://kueue.sigs.k8s.io/docs/tasks/manage/setup_wait_for_pods_ready/
- **`kueue.x-k8s.io/max-exec-time-seconds`.** Exists, but it budgets the
  whole Workload (the campaign), not a volume; the per-volume budget is
  the pod's `activeDeadlineSeconds` already in use. Not needed.
- **AdmissionChecks / ProvisioningRequest.** For cluster autoscalers on
  cloud. Not applicable on-prem.
  → https://kueue.sigs.k8s.io/docs/concepts/admission_check/
- **MultiKueue.** When a production cluster exists, this PoC (or a small
  management cluster) can dispatch Workloads to it and keep the converter
  and read API unchanged in front. The upgrade path, not a task now.
  → https://kueue.sigs.k8s.io/docs/concepts/multikueue/

---

## 7. KAI Scheduler: what it is, and why not now

**What it is.** NVIDIA's open-source (Apache-2) Kubernetes *pod
scheduler* for AI workloads, derived from Run:ai's scheduler. It replaces
kube-scheduler for pods that name it (`schedulerName: kai-scheduler`) and
"can run alongside other schedulers installed on the cluster". Features:
hierarchical queues with quotas and over-quota weights, dominant-resource
fairness and reclaim across queues, gang scheduling (all pods of a
PodGroup or none), workload priority and preemptibility as separate
policies, bin-packing or spread, topology-aware placement, and GPU sharing
(fractional GPUs by memory fraction, injected via a reservation pod and
environment; no hardware isolation). Installed by Helm into the
`kai-scheduler` namespace.
→ https://github.com/NVIDIA/KAI-Scheduler
→ https://github.com/NVIDIA/KAI-Scheduler/tree/main/docs (queues, gang
scheduling, GPU sharing, priority pages)

**Overlap with Kueue.** Kueue decides *whether and when* a Job may start
(quota, priority, fair share, preemption at Workload level) and then hands
pods to whatever scheduler places them. KAI decides *where and when* a pod
is placed, and also does quota, fair share and preemption — at pod-group
level, with its own `Queue` CRDs. Running both is technically possible
(Kueue unsuspends, KAI places), but quota and priority would then live in
two systems with two vocabularies, and an admission that Kueue grants can
still starve in KAI's queue, or the reverse. Neither project documents an
integration with the other.

**Why not now, concretely.**
1. Every campaign pod is a single independent pod with one GPU: gang
   scheduling, bin-packing and topology awareness have nothing to act on.
2. The one KAI feature that applies, fractional GPUs, is GPU sharing —
   which is deferred on this cluster (item 3). If sharing is ever wanted,
   the device plugin's time-slicing gives it with zero new components;
   KAI's fractional GPUs are time-sliced underneath too, the difference
   being memory-fraction bookkeeping in the scheduler.
3. Fairness and priority are already covered by Kueue features the chart
   does not yet use (items 1 and 4). Adding a second system before using
   the first is the wrong order.
4. The PoC node is arm64. Neither the README nor the release notes mention
   arm64 or multi-arch images. **Verify** (`docker manifest inspect` on the
   chart's images) before spending any time; if there is no arm64 build,
   the question is moot on this cluster.
5. Two schedulers doubles what an operator must understand when "the GPU
   is idle but nothing runs" (already flagged as O17).

**When to revisit.** A production cluster that is a multi-node, multi-team
GPU pool, and either (a) the platform team already runs KAI or Run:ai
there, or (b) you need fractional GPUs with memory-aware packing across
many cards, or (c) multi-pod jobs appear (distributed inference, training).
The decision then is KAI's queues for fairness with Kueue reduced to Job
admission and suspend, or dropping Kueue and giving the converter KAI's
`Queue` and PodGroup labels instead. Both are workable; choose against the
platform team's standard, not in isolation.

---

## Suggested order and sizes

| # | Item | Where | Size |
|---|------|-------|------|
| 1 | Priority classes + `withinClusterQueue: LowerPriority`; converter validates `priority:` | chart, converter | small |
| 5 | Kueue metrics on the B59 dashboard | dashboard JSON | small |
| 2 | `stopPolicy` documented / exposed in values | chart, docs | tiny |
| 3 | GPU time-slicing | — | deferred (2026-09-02); quota stays at the physical GPU count |
| 4 | Per-organisation ClusterQueues in a cohort, fair sharing | chart, converter configs | medium; gated on T05 being scheduled |
| 6 | Elastic jobs, MultiKueue | — | later, production cluster |
| 7 | KAI | — | not now; verify arm64 if ever |

Verification for chart changes: `make helm-lint helm-template`, then on the
PoC `kubectl get clusterqueue htr-batch-cq -o yaml` shows the new fields
and `kubectl get workloads -A` shows the expected admission order in a
two-campaign test (`docs/development/e2e-indexed-jobs.md` has the harness).
