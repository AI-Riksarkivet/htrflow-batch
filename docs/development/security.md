# Security

Two questions this page answers: what a pod on this platform *can* do
(posture, network), and who can make it do something — the trust boundary.

## Trust boundary

**Write access to the campaigns repo equals cluster operator.** A pipeline
file names a container image that runs on the GPU with the results bucket's
write credentials and the model cache, and a Hugging Face model repo whose
weights are pickles loaded in the warm-up pod (which has internet egress).
There is no admission step between a merged commit and a running pod
beyond what the reconciler checks. Treat the repo like CI config — protected
`main`, required review, the immutability guard on pull requests — and turn
on the controls the chart offers:

| Control | Where | What it closes |
|---|---|---|
| **Image allow-list** — `security.allowedImageRepos` → `RECONCILER_ALLOWED_IMAGE_REPOS` | reconciler `parse_pipeline`, before any ConfigMap or Job exists | any digest-pinned image from any registry. Empty = anything runs, and the reconciler says so in `status.json` warnings |
| **Digest pin** on `image:` (always) | `parse_pipeline` | a mutable tag changing what an id means |
| **Model revision** — `security.requireModelRevision` → `RECONCILER_REQUIRE_MODEL_REVISION` | `parse_pipeline` | an unpinned HF repo swapping its weights under the same pipeline id |
| **Signed images** — `security.verifyImages.*` (Kyverno `ClusterPolicy`, cosign keyless) | admission, per Pod in the namespace | an image that was not built by the CI identity you name. Needs Kyverno installed and `publish.yml` signing ([CI](ci.md#workflows)); off by default |
| **Control-plane digest gate** — `reconciler.image` / `viewer.image` must be `@sha256:` unless `devStack.allowTagImages` | chart template | anyone with registry push replacing the reconciler or viewer in place |
| **http(s)-only sources, byte caps, redirect caps** | `parse_pipeline`/`parse_campaign`, the reconciler's `fetch_json` (16 MiB, 3 redirects, 10 s), the wrapper (`MANIFEST_MAX_BYTES`, `FETCH_MAX_BYTES`, 5 redirects, raster-only acceptance) | SSRF/DoS driven by campaign data |
| **Transport** — `https://` with a read-only token (`reconciler.gitTokenSecret` → `GIT_TOKEN`), or the in-cluster `git://` daemon | reconciler `gitrepo` (dulwich; no `git` binary in the image) | an unauthenticated clone from the open internet. `git://` is fine in-cluster; over the network it is neither authenticated nor encrypted |
| **URL redaction** | wrapper logs, termination log, failure metrics, `page_sources` | a tokenised private IIIF URL landing in the world-readable log |

What the pod can then do is bounded by the posture and the policies below —
non-root, no capabilities, read-only rootfs, a read-only model cache, egress
to DNS, S3 and the IIIF origin only, no API credential.

### The bucket policy

The results bucket is read anonymously by browsers, so the question is
*which keys*. The devStack `rustfs-init` hook renders a single `Allow` on
`s3:GetObject` for `*` with `NotResource` (RustFS applies a `Deny` to the
root credential too and ignores anonymous-only conditions — verified
2026-08-26), and listing is never allowed:

| Keys | Anonymous read |
|---|---|
| `<pipeline>/<volume>/*` (results, `iiif.json`, `manifest.json`), `sources/*`, `status/status.json` | always — the viewer and the campaign browser fetch them directly |
| `status/logs/*` (run logs) | **yes while `devStack.rustfs.publicLogs=true`** (default). The campaign browser links them; a run log can carry the redacted form of a private IIIF URL and whatever htrflow prints. Set it to `false` once the log viewer sits behind an authenticated proxy |
| `status/attempts.json`, `status/validation.json`, `status/failures/*`, `status/warmup/*` | never — reconciler state and failure evidence |

A real bucket (HCP, AWS) needs the same shape, written by hand.
`scripts/compose_init.py` mirrors it for the compose stack.

### Open: two S3 principals

Today one credential (the `credentials` file in `s3.existingSecret`) serves
the reconciler and every Job. The intended end state is two: Job
credentials scoped to `<pipeline>/<volume>/*` plus its run-log key, and
reconciler credentials scoped to `status/*` and `sources/*`. It needs IAM
users/policies created at bucket init and a second Secret consumed by
`jobspec.py`; the bucket-policy split above only covers the anonymous side.
Tracked in [Open Items](../roadmap/open-items.md).

## Pod security posture (D14 — enforced by the chart)

Every pod the platform runs — the reconciler-built volume Jobs, the
per-pipeline warm-up Jobs, the reconciler CronJob, the chart's example Job,
the viewer, and the devStack RustFS, its init Job and the registry — meets
Pod Security **`restricted`**:

- `runAsNonRoot` (`USER` in the images *and* `runAsUser` in the pod spec, so
  neither side can regress alone): uid 1000 for the Jobs, reconciler, init
  Job and registry (`devStack.registry.runAsUser`), 101 for the viewer
  (nginx-unprivileged), 10001 for RustFS; `fsGroup` likewise.
- `capabilities.drop: [ALL]`, `allowPrivilegeEscalation: false`,
  `seccompProfile: RuntimeDefault`.
- `readOnlyRootFilesystem`. Writable paths are explicit: the tmpfs workdir
  (`/work`) carries `HOME`, `TMPDIR` and `YOLO_CONFIG_DIR` — where ultralytics
  settings, triton/inductor JIT caches and temp files land — and the wrapper
  creates them before any model is built. The reconciler gets an emptyDir at
  `/tmp` (campaigns clone); the viewer `/tmp` and `/var/cache/nginx`.
- `automountServiceAccountToken: false` on every pod except the reconciler,
  which creates Jobs and is the one pod that legitimately holds an API
  credential (namespace-scoped Role: jobs, configmaps get/create, pods,
  pods/log, and the `htr-reconciler` Lease).
- **Secrets are files, not env.** The S3 Secret's `credentials` key (AWS ini
  format) is mounted at `/secrets/s3` (mode `0440`) and reaches boto3 through
  `AWS_SHARED_CREDENTIALS_FILE`; only the non-secret `S3_ENDPOINT` /
  `S3_BUCKET` are env. Nothing does `envFrom` a Secret.
- **The model cache is read-only for Jobs.** Batch Jobs mount the cache PVC
  `readOnly` and run `HF_HUB_OFFLINE=1`; the per-pipeline warm-up Job is the
  single writer (see [Model handling](../how-it-works/wrapper.md#model-handling)).
  A compromised Job cannot poison the weights every later Job loads.

**The one exception is the devStack git daemon** (`devStack.gitDaemon`):
`alpine/git` ships no `git-daemon`, so the container installs it at start —
root and a writable root filesystem, with capabilities dropped and no
privilege escalation. That is why `security.psaEnforce` defaults to
`baseline`; a purpose-built image running as uid 1000 is the fix
([Evolution](../roadmap/evolution.md#other-items)).

The GPU still arrives through `runtimeClassName: nvidia` — runc plus the
NVIDIA hooks, **not** a sandbox. A kernel-isolating runtime (gVisor `nvproxy`,
Kata with GPU passthrough) on the arm64 GPU node is unproven and out of scope
here; it is the next hardening step if one is wanted.

### Namespace labels

Helm cannot label a namespace it did not create, so the Pod Security
Admission labels are applied once by the operator (`make psa-labels`):
`enforce=<security.psaEnforce>`, `warn=restricted`, `audit=restricted`.
Enforcement is `baseline` only while the git daemon runs as root; the
platform's own pods are restricted-clean, and the `warn` label is what
proves it — a regression shows up as an admission warning on Job creation.

## NetworkPolicy (D14 — enforced by the chart)

`templates/network.yaml` (values: `network.*`) renders a namespace-wide
**default deny** (ingress and egress, `network.defaultDeny`), a DNS allow
for every pod, and one policy per pod role. Rules match by CIDR and selector
only — kube-router (k3s) has no FQDN rules, and it evaluates egress *after*
service DNAT, so in-cluster targets are matched by their backing pod and the
apiserver by the node address it resolves to (auto-detected with Helm
`lookup`, overridable via `network.apiServer` / `network.nodeCidrs`).

| Pod | Ingress | Egress (besides kube-dns) | Notably cannot reach |
|---|---|---|---|
| batch Job (`app=htrflow-batch`) | none | S3 (RustFS pod or `network.s3Cidrs`); the IIIF origin(s) `network.iiifCidrs` on 443/80 (default `lbiiif.riksarkivet.se`) | HF Hub, the apiserver, the registry, Harbor, anything else in-cluster, the internet |
| warm-up Job (`app=htrflow-warmup`) | none | the public internet on 443 minus pod/service/node ranges (HF Hub is a CDN — there is no CIDR to pin) | S3, the apiserver, anything in-cluster |
| reconciler (`app=htr-reconciler`) | none | the apiserver; S3; `network.reconciler.egressCidrs` on 443/22/9418 (GitHub's `git` ranges by default); the devStack git daemon on 9418 when enabled; `network.reconciler.extraEgress` | everything else |
| viewer (`app=uv4-viewer`) | `network.viewer.ingressCidrs` on 8080 | none | — |
| RustFS (`app=rustfs`) | 9000 from anywhere (and 9001 with the console) | none | — |
| rustfs-init hook (`app=rustfs-init`) | none | RustFS 9000 | — |
| git daemon (`app=git-daemon`) | 9418 from the reconciler | RustFS 9000 (seed clone); public 443/80 (the Alpine CDN, for the `apk add`) | — |

Anything hand-applied in the namespace loses network access under the
default deny unless it gets its own policy.

**Known limitation — policy sync window.** kube-router applies a new pod's
policies asynchronously after the pod gets its IP: measured ~1 s on the k3s
PoC node in steady state, tens of seconds right after the policies were first
created. A pod can egress freely for that window. The batch wrapper does
nothing network-facing in its first seconds except fetch the (allowed) IIIF
manifest, so the practical exposure is a compromised image's first second;
closing it needs a CNI with synchronous enforcement (Cilium, Calico) — not a
chart change.

Verified on the k3s PoC (2026-08-25, [test log](test-log.md)): a probe pod
labelled `app=htrflow-batch` reaches only the IIIF origin and RustFS; HF Hub,
the apiserver, the in-cluster registry, Harbor, the node and the public
internet are rejected, while an unlabelled control pod reaches all of them.

## Cache PVC migration

Pods used to run as root, so a cache PVC warmed before this change is
root-owned and the non-root warm-up cannot write into it. Once, before the
first warm-up: `chown -R 1000:1000` the PVC from a throwaway pod (or start
with a fresh PVC). `local-path` volumes do not honour `fsGroup`, which is why
this is not automatic. The same applies to the devStack registry's data PVC
when it was written by an older root-running registry.

## devStack caveats

The chart's optional `devStack.*` components exist purely to stand up a
disposable single-node PoC ([Local k3s development](local-k3s.md)) and are
off by default:

- **RustFS** credentials are generated on first install (32 random chars,
  re-read from the Secret on upgrade) or set via
  `devStack.rustfs.{accessKey,secretKey}`; the S3 API is a NodePort
  (30900) reachable by anyone on the node's network, the admin console is
  off. Its data is one unreplicated `local-path` PVC. Never enable it next to
  real data; point `s3.existingSecret` at a real Secret instead.
- **The registry** is unauthenticated by design (PoC image-iteration
  convenience; Harbor is the real registry on the cluster). Anyone on the
  network can push over a tag — which is exactly why the chart refuses tag
  references for the control-plane images unless `devStack.allowTagImages`.
- **The git daemon** is the one non-restricted pod (above) and serves the
  campaigns repo unauthenticated over `git://` inside the cluster only
  (ingress from the reconciler alone).
- The compose smoke stack uses throwaway `rustfsadmin` credentials from
  `.env.example`; never reuse them for a cluster.
