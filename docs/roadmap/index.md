# Roadmap

What is still open, and what could come next. Nothing on this page is built.
What exists is described under How it Works, starting from
[Architecture](../how-it-works/architecture.md). Each item says what it would
add and what stands in the way. The groups are not a schedule.

## What a production deployment still has to prove

The platform runs end to end on single volumes, a handful of concurrent
volumes and volumes of several hundred pages. It has not yet run an
archive-scale campaign. Such a campaign would run enough volumes, for long
enough, to settle the questions a single node cannot:

- **Throughput and GPU stall.** `manifest.json` records `wall_seconds`,
  `gpu_stall_seconds` and `pages_per_second` for every volume
  ([S3 Layout](../reference/s3-layout.md)). Nothing aggregates them yet: a
  script over the bucket's `manifest.json` objects would. The aggregate stall
  fraction decides whether the [cache layer](cache-layer.md) is worth
  building.
- **Sizing.** The GPU quota, the pod memory request and limit, the
  campaign `window` and `MAX_IMAGE_WIDTH` still have to be tuned on the
  target nodes ([The Wrapper](../how-it-works/wrapper.md),
  [Queueing](../how-it-works/queueing.md)).
- **A durable results bucket.** The devstack bucket is a single unreplicated
  volume. Losing it means recomputing every campaign. Before the bucket is
  treated as an archive, it has to live on replicated, backed-up
  S3-compatible storage, with the bucket policy written by hand
  ([Security](../how-it-works/security.md)).
- **The controls switched on.** The chart's Kyverno policies enabled with an
  image allow-list, and the campaigns repo run as the trust model in
  [Security](../how-it-works/security.md) describes.

## Scale and durability

- **Shared model cache across nodes.** The model cache is one
  `ReadWriteOnce` volume, so every warm-up and campaign pod lands on the node
  that holds it. Scaling past one GPU node needs either one
  `ReadWriteMany` volume on a shared filesystem (warm-up and the read-only
  mount stay as they are, only the storage class changes) or one cache per
  node ([The Wrapper](../how-it-works/wrapper.md)).
- **Retention and size guards.** Nothing prunes the model cache, the run
  logs or old results. A retired pipeline's snapshots and warm-up marker stay
  on the cache volume until someone removes them by hand.
- **Durable failure history.** A completed volume is remembered forever by
  its `manifest.json`. A failed one is not: once the Job's TTL reaps it, the
  run log is all that is left, and the next attempt overwrites it. A durable
  failure record, or a database, is only worth it if failure analytics
  demand one.
- **Finished campaigns after the TTL.** The Workload goes with the reaped
  Job, so nothing remembers that a campaign already ran. A later apply
  creates the Job again. Resume skips the pages already in the bucket, but
  every volume still takes a GPU slot and a model load to find that out.
- **Small-volume batching.** A model load costs about the same for every
  volume. That cost disappears in a volume of hundreds of pages, but it
  can dominate a volume of ten. If tiny volumes become common, the converter
  could pack several into one index: one model load, several volumes through
  the resident pipeline, still one `manifest.json` each. That changes the
  wrapper's one-index-one-volume contract, not the queue.
- **Intra-volume sharding.** Splitting one volume's pages across several
  indexes would cut latency for urgent volumes. It needs an assembly step,
  and it breaks the one-index-one-volume contract the whole read path
  assumes.
- **A cache in front of the IIIF origin.** Only if measurements demand it:
  [Cache layer](cache-layer.md).

## Queueing and fairness

The current queue, and the reasoning behind each setting, is in
[Queueing](../how-it-works/queueing.md).

- **Priority lanes.** A campaign's `priority:` renders Kueue's priority-class
  label, but the chart ships no `WorkloadPriorityClass` and preemption is
  off. Usable lanes need the classes, a decision on preemption, and an answer
  for what "next" means while one campaign holds the whole quota. Preemption
  kills a running volume mid-transcription. Resume makes that survivable, but
  it is still a product decision, not a switch.
- **Cohorts and borrowing.** Once the GPU pool is shared with another tenant,
  a cohort lets either side borrow the other's idle quota.
- **A pause Kueue owns.** Pausing is the converter patching the Workload's
  `spec.active`, through an older, still-served version of the Workload API
  than the chart renders. A pause expressed in Kueue itself would remove
  that dependency.
- **The window against the quota.** Without partial admission a campaign's
  whole `parallelism` must fit the quota, or it never starts. `apply` does
  not yet warn when the window cannot fit.
- **A declarative skip.** A volume that fails for good can only be removed
  from its campaign file. It runs again only when it is added to a new
  campaign file, because a capped index gets no fresh retry budget.
- **Metrics.** Kueue exports Prometheus metrics, and kube-state-metrics
  exposes a Job's `completedIndexes` and `failedIndexes`. The platform itself
  publishes none.

## Supply chain and isolation

The trust model and the controls that exist are in
[Security](../how-it-works/security.md).

- **Kyverno policy kind.** The chart ships `kyverno.io` `ClusterPolicy`
  objects. Current Kyverno deprecates that kind in favour of the CEL-based
  `policies.kyverno.io` `ValidatingPolicy`, and warns on every apply.
  Migrating is a self-contained change to the chart's policy templates.
- **A sandboxed GPU runtime.** The GPU arrives through the NVIDIA container
  runtime, which is not a sandbox. A kernel-isolating runtime with GPU
  support is the next hardening step, and it is unproven on this workload.
- **A narrower S3 credential.** Campaign and warm-up pods share one bucket
  credential, and only convention scopes it to their own
  `<namespace>/<pipeline>/<volume>/` prefix. A credential per prefix (plus
  the run-log key) needs IAM users and policies created with the bucket, and
  a second Secret named in `converter.yaml`.
- **Authentication in front of the web front.** The web front and its
  read API are unauthenticated, and the process holds a read-only API token.
  Before exposing it beyond a trusted network, put it behind an
  authenticating proxy (OIDC at the ingress). Then the run logs can stop
  being anonymously readable too
  ([View results](../getting-started/viewing.md)).
- **Upstream page-failure propagation.** The stock htrflow CLI submits pages
  to a thread pool and never collects the futures, so page failures do not
  reach its exit code. The wrapper runs htrflow in-process and does not
  depend on this. A fix upstream still helps other users of the CLI.

## Interfaces

Today a commit to the campaigns repo is the submission, and the web front
is read-only ([Campaigns](../how-it-works/campaigns.md),
[Frontend](../reference/frontend.md)).

- **Submit dry-run.** `htrflow-campaigns validate` checks shape, and
  `apply --dry-run` prints the objects, but neither reads a IIIF manifest. A
  dry-run that resolves each manifest could report reachability, page counts
  and a runtime estimate before anything is rendered. Wild-web manifests
  (hotlink blocks, auth walls) would then fail there, not as failed indexes.
- **A CLI for hand-run volumes.** One volume, right now, without a commit:
  - `submit` renders a one-index Job the way the converter renders a
    campaign, and applies it.
  - `status`, `logs` and `retry` wrap the read API and `kubectl`.
  - `report` aggregates stall fraction and throughput across `manifest.json`
    objects.
  - `pipeline deploy` validates a pipeline, creates its ConfigMap and runs
    the warm-up.

  The converter's `parse`, `models` and `render` modules would do the work.
  Hand-run Jobs keep `app=htrflow-batch`, so the NetworkPolicies apply, but
  not the converter's managed-by label. The read API then does not list them
  as campaigns, and `apply --prune` does not delete them.
- **A submitting API and frontend.** The write half is the converter behind
  HTTP: render and apply become a `POST`, or a commit to the campaigns repo
  that the existing flow then applies. It is stateless: the cluster and the
  bucket stay the state. Live progress and the viewer already exist, so the
  new UI is a submit form (reference codes, a pipeline picked from the
  deployed ones, a priority) next to the campaign browser.
- **Search in the viewer.** The viewer's search panel hides itself when a
  manifest has no search service. A IIIF Content Search endpoint backed by a
  full-text index of the transcribed lines would light it up.
- **An external orchestrator.** Another system can drive campaigns by
  committing to the campaigns repo, or through the submitting API above. The
  wrapper and the queue stay unchanged either way.
- **A CRD, only if a machine consumer demands one.** Everything a
  `Transcription` custom resource would own already has a cheaper owner:
  - Kueue owns queueing.
  - The Indexed Job owns lifecycle and retries, and its `completions` are
    the volume list.
  - ConfigMaps hold pipelines.
  - Git holds the desired state.

  If an in-cluster system ever needs a declarative contract rather than a
  git repo, make it **one resource per campaign, not per volume**:
  - Hundreds of thousands of per-volume objects in etcd is a known
    anti-pattern.
  - Per-volume truth stays where it is: `completedIndexes` while the Job
    lives, `manifest.json` after.
  - The controller creates the objects the converter renders today, with
    the converter as a library.

  A pipeline CRD would add admission-time validation and automatic warm-up.
  The Kyverno policies already validate pipeline ConfigMaps at admission, and
  the warm-up Job already does the rest.
