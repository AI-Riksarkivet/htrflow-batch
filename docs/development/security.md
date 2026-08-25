# Security

## Pod security posture (D14 — enforced by the chart)

Every pod the platform runs — the reconciler-built volume Jobs, the
per-pipeline warm-up Jobs, the reconciler CronJob and the chart's example Job
— meets Pod Security **`restricted`**:

- `runAsNonRoot` as uid/gid 1000 (`USER 1000` in the images *and*
  `runAsUser` in the pod spec, so neither side can regress alone),
  `fsGroup: 1000`.
- `capabilities.drop: [ALL]`, `allowPrivilegeEscalation: false`,
  `seccompProfile: RuntimeDefault`.
- `readOnlyRootFilesystem`. Writable paths are explicit: the tmpfs workdir
  (`/work`) carries `HOME`, `TMPDIR` and `YOLO_CONFIG_DIR` — where ultralytics
  settings, triton/inductor JIT caches and temp files land — and the wrapper
  creates them before any model is built. The reconciler gets an emptyDir at
  `/tmp` (campaigns clone, git `HOME`).
- `automountServiceAccountToken: false` on every pod except the reconciler,
  which creates Jobs and is the one pod that legitimately holds an API
  credential (namespace-scoped Role: jobs, configmaps, pods, pods/log).
- **Secrets are files, not env.** The S3 Secret's `credentials` key (AWS ini
  format) is mounted at `/secrets/s3` and reaches boto3 through
  `AWS_SHARED_CREDENTIALS_FILE`; only the non-secret `S3_ENDPOINT` /
  `S3_BUCKET` are env. Nothing does `envFrom` a Secret any more.
- **The model cache is read-only for Jobs.** Batch Jobs mount the cache PVC
  `readOnly` and run `HF_HUB_OFFLINE=1`; the per-pipeline warm-up Job is the
  single writer (see [Model handling](../how-it-works/wrapper.md#model-handling)).
  A compromised Job cannot poison the weights every later Job loads.

The GPU still arrives through `runtimeClassName: nvidia` — runc plus the
NVIDIA hooks, **not** a sandbox. A kernel-isolating runtime (gVisor `nvproxy`,
Kata with GPU passthrough) on the arm64 GPU node is unproven and out of scope
here; it is the next hardening step if one is wanted.

### Namespace labels

Helm cannot label a namespace it did not create, so the Pod Security
Admission labels are applied once by the operator (`make psa-labels`):
`enforce=baseline`, `warn=restricted`, `audit=restricted`. Enforcement stays
at `baseline` only because the optional devStack services (RustFS) are not
restricted-clean; the platform's own pods are, and the `warn` label is what
proves it — a regression shows up as an admission warning on Job creation.

## Egress NetworkPolicy (D14 — enforced by the chart)

`templates/network.yaml` (values: `network.*`) renders one policy per pod
role. Ingress is denied for all three; egress is an allowlist. Rules match by
CIDR and selector only — kube-router (k3s) has no FQDN rules, and it
evaluates egress *after* service DNAT, so in-cluster targets are matched by
their backing pod and the apiserver by the node address it resolves to
(auto-detected with Helm `lookup`, overridable via `network.apiServer` /
`network.nodeCidrs`).

| Pod | May reach | Notably cannot reach |
|---|---|---|
| batch Job (`app=htrflow-batch`) | kube-dns; S3 (RustFS pod or `network.s3Cidrs`); the IIIF origin(s) `network.iiifCidrs` (default `lbiiif.riksarkivet.se`) | HF Hub, the apiserver, the registry, Harbor, anything else in-cluster, the internet |
| warm-up Job (`app=htrflow-warmup`) | kube-dns; the public internet on 443 minus pod/service/node ranges (HF Hub is a CDN — there is no CIDR to pin) | S3, the apiserver, anything in-cluster |
| reconciler (`app=htr-reconciler`) | kube-dns; the apiserver; S3; `network.reconciler.egressCidrs` on 443/22/9418 (the campaigns host) plus `network.reconciler.extraEgress` (raw rules, e.g. an in-cluster git daemon) | everything else |

The viewer and the devStack services are matched by no policy and are
unaffected.

## Cache PVC migration

Pods used to run as root, so a cache PVC warmed before this change is
root-owned and the non-root warm-up cannot write into it. Once, before the
first warm-up: `chown -R 1000:1000` the PVC from a throwaway pod (or start
with a fresh PVC). `local-path` volumes do not honour `fsGroup`, which is why
this is not automatic.

## RustFS PoC-creds caveat

The chart's optional `devStack.rustfs` component (`templates/devstack-rustfs.yaml`)
hardcodes `rustfsadmin` / `rustfsadmin` as both the RustFS server credentials
and the `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the generated S3
Secret. This is **intentional and PoC-only** — `devStack.rustfs` exists purely
to stand up a disposable in-cluster S3 for replaying the PoC without external
dependencies (see [Deploy](../getting-started/deploy.md)), and is off by
default. Never enable `devStack.rustfs` against a deployment that holds real
data; point `s3.existingSecret` at a real Secret with real credentials
instead. The same caveat covers the devStack in-cluster registry, which is
unauthenticated by design (PoC image-iteration convenience only).
