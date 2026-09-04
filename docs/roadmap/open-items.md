# Open Items

Discussion queue — what was designed in, what is built, and what is still
to be confirmed on a real cluster. The audit of 2026-08-26 and its
remediation are tracked separately in
[the audit report](../audits/2026-08-26-repo-audit.md).

| # | Item | State |
|---|------|---|
| D6 | Output store: S3 — durable bucket (HCP / real S3) vs the PoC's RustFS | S3 settled; the PoC runs on an unreplicated RustFS PVC — pick and confirm the durable target before any archive-scale run |
| D9 | Resume-from-partial-results | **built** — `page/` + `alto/` both required, `page_sources` compared ([The Wrapper](../how-it-works/wrapper.md#stages-around-the-streaming-loop)); kill-and-resume verified on the k3s PoC |
| D10 | Deterministic naming as idempotency key | **superseded** — a campaign's Job name is the campaign file's stem (`-part2`, … past 10 000 volumes); Kubernetes' own duplicate-create (`AlreadyExists`) plus render-time append-only enforcement do what the old per-volume hash scheme did ([Campaign & Pipeline YAML](../reference/campaign-yaml.md)) |
| D11 | Provenance manifest contents | **built** — `manifest.json` ([S3 Layout](../reference/s3-layout.md#manifestjson-completion-marker)) |
| D12 | Structured failure via termination-log | **built** — incl. the SIGTERM path ([Failure Handling](../how-it-works/failure-handling.md)) |
| D13 | Priority lanes (`htr-interactive` > `htr-bulk`) | **built** — a campaign's `priority:` renders `kueue.x-k8s.io/priority-class`; a Kueue `WorkloadPriorityClass` of that name must exist |
| D14 | Pod security + egress NetworkPolicy | **built** — restricted pod specs, namespace default-deny + per-role allowlists, read-only offline model cache with a converter-rendered warm-up Job, digest-gated control-plane images, optional Kyverno `verifyImages` ([Security](../development/security.md)); a sandboxed runtime is the open follow-up |
| D15 | Submit dry-run | proposed — there is no pre-validation step any more (`htrflow-campaigns validate` checks shape, not manifest reachability or page count); a runtime estimate needs a CLI or an API that reads manifests ahead of render |
| — | `htrq` CLI (submit/status/logs/retry/report/pipeline deploy) | **not built, superseded for campaigns** by the converter + git; kept as a proposal for hand-run volumes ([Evolution](evolution.md#htrq-cli-proposal-not-built)) |
| — | Campaigns as Indexed Jobs + read-only status API | **built and running** on the k3s PoC (release `htr`, ns `htr-batch`) — no CronJob, no controller; converter renders manifests, Kueue/Kubernetes own retries, the read API + campaign browser show progress, live run log unchanged ([Campaigns (Indexed Jobs)](../how-it-works/campaigns.md), [Local k3s development](../development/local-k3s.md)) |
| — | Two S3 principals (Job creds scoped to their prefix; a second principal for status writes) | **moot** — the read API never touches S3 at all (read-only Kubernetes RBAC only), so there is only one S3-credentialed principal left: the batch/warm-up Jobs |
| — | Target cluster for the PoC | the GB10 arm64 k3s node; a production cluster is **unresolved** |
| — | Quota N, memory numbers, width default | quota 1 GPU on the PoC; requests 8 Gi / limits 16 Gi; width 2500 — tune on the production cluster |

## Active next step

The PoC has been validated end to end on single volumes, a handful of
concurrent volumes and a 480-spread volume (see the
[test log](../development/test-log.md)), but not yet at archive scale. The
active next step is an **archive-scale campaign**: enough volumes, run for
long enough, to produce a trustworthy aggregate
`gpu_stall_seconds / wall_seconds` figure — the number that actually gates
[Phase 2](phase-2-cache.md). The machinery exists — declare the volumes in
the campaigns repo and render + apply the campaign
([Running a Campaign](../getting-started/campaigns.md)); aggregating the
figure is a script over the bucket's `manifest.json`s (no `htrq report`
exists). Before that run: a durable bucket (D6), `security.allowedImageRepos` set
with `security.policies.enabled` on the release (Kyverno installed), and
`main` protected on the campaigns repo.
