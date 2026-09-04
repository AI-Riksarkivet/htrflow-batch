# Security

Two questions this page answers: what a pod on this platform *can* do
(posture, network), and who can make it do something — the trust boundary.

## Trust boundary

**Write access to the campaigns repo equals cluster operator.** A pipeline
file names a container image that runs on the GPU with the results bucket's
write credentials and the model cache, and a Hugging Face model repo whose
weights are pickles loaded in the warm-up pod (which has internet egress).
The converter itself never runs in the cluster, so the admission step
between a merged commit and a running pod is Kyverno's: the chart ships
`ClusterPolicy` objects (`security.policies.enabled`) that the API server
applies to every Job, Pod and pipeline ConfigMap in the namespace, whoever
wrote it — and the campaigns repo's CI runs the same policies over its
rendered output with the Kyverno CLI, so an author fails in the pull
request instead of at apply time. That reach is uniform for the two image
rules (every Job and Pod, unconditionally), narrower for the revision rule
(only ConfigMaps carrying the converter's own `managed-by=converter` label,
and only their pipeline's top-level `steps:` — see the table below). Treat
the repo like CI config — protected `main`, required review — and turn on
the controls it and the chart offer:

| Control | Where | What it closes |
|---|---|---|
| **Digest pin** — `security.policies.enabled` | Kyverno `ClusterPolicy` `htrflow-batch-images-pinned-<ns>`: admission, every Job and Pod in the namespace, plus the Kyverno CLI in the campaigns repo's CI. Message: `image must be pinned by digest: <image>` | a mutable tag changing what an id means. `htrflow-campaigns validate` also refuses a pipeline whose `image:` is not `@sha256:`-pinned — the renderer needs that digest — but only for what it renders |
| **Image allow-list** — `security.allowedImageRepos` (+ `policies.enabled`) | Kyverno `ClusterPolicy` `htrflow-batch-images-allowed-<ns>`, same two places. Message: `image is not from an allowed repository: <image> — allowed: <list>` | any image from any registry. Empty list = the policy is not rendered and nothing is checked |
| **Model revision** — `security.requireModelRevision` (+ `policies.enabled`) | Kyverno `ClusterPolicy` `htrflow-batch-model-revision-<ns>`: admission of the pipeline ConfigMap, same CLI. Message: `models not pinned to a revision: <models> — add revision: <40-character commit hash> under model_settings (YOLO) or model_settings.model_kwargs (TrOCR and other Hugging Face models)` | an unpinned HF repo swapping its weights under the same pipeline id |
| **Signed images** — `security.verifyImages.*` (Kyverno `ClusterPolicy`, cosign keyless) | admission, per Pod in the namespace | an image that was not built by the CI identity you name. Needs Kyverno installed and `publish.yml` signing ([CI](ci.md#workflows)); off by default |
| **Control-plane digest gate** — `web.image` must be `@sha256:` unless `security.allowTagImages` | chart template | anyone with registry push replacing the web front in place |
| **http(s)-only sources, byte caps, redirect caps** | `parse_pipeline`/`parse_campaign`, the wrapper (`MANIFEST_MAX_BYTES`, `FETCH_MAX_BYTES`, 5 redirects, raster-only acceptance) | SSRF/DoS driven by campaign data |
| **Transport to the campaigns repo** — ordinary `git`/HTTPS, review-gated CI | the campaigns repo's own CI, outside this system entirely | there is no in-cluster clone or credential for it any more — nothing here reaches the campaigns repo at runtime |
| **URL redaction** | wrapper logs, termination log, `page_sources` | a tokenised private IIIF URL landing in the world-readable log |

!!! warning "With `security.policies.enabled` off — or no Kyverno — nothing enforces these"

    The three policies above are the only enforcement point for the image
    allow-list and the model-revision rule: the converter dropped both in
    B63 Task 22 (a rule the converter applies only ever sees what the
    converter rendered, and a hand-made Job walks past it). Leaving
    `security.policies.enabled` at `false`, or enabling it in a cluster
    with no Kyverno, means any registry and any unpinned model is admitted.
    What survives without Kyverno is the *shape* check on a pipeline's
    `image:`, which `htrflow-campaigns validate` still makes because the
    renderer builds ids out of that digest.

    `make install-kyverno` installs it on the PoC (chart 3.9.0, app
    v1.19.0, its own `kyverno` namespace). On ai-dev this is story I04.
    Note that Kyverno v1.19 marks the `kyverno.io` `ClusterPolicy` kind
    deprecated in favour of the CEL-based `policies.kyverno.io`
    `ValidatingPolicy`; the chart still ships `ClusterPolicy`, which is what
    v1.19 runs, and the migration is a later task.

What the pod can then do is bounded by the posture and the policies below —
non-root, no capabilities, read-only rootfs, a read-only model cache, egress
to DNS, S3 and the IIIF origin only, no API credential (except the read API
itself, see below).

### The bucket policy

The results bucket is read anonymously by browsers, so the question is
*which keys*. The devStack `rustfs-init` hook renders a single `Allow` on
`s3:GetObject` for `*` with `NotResource` (RustFS applies a `Deny` to the
root credential too and ignores anonymous-only conditions — verified
2026-08-26), and listing is never allowed:

| Keys | Anonymous read |
|---|---|
| `[<namespace>/]<pipeline>/<volume>/*` (results, `iiif.json`, `manifest.json`), `sources/*` | always — the browser fetches them directly from the results base, never through the platform |
| `status/logs/*` (run logs) | **yes while `devStack.rustfs.publicLogs=true`** (default). The campaign browser links them; a run log can carry the redacted form of a private IIIF URL and whatever htrflow prints. Set it to `false` once the run-log view sits behind an authenticated proxy |

A handful of `status/attempts.json`-era key paths are still explicitly
excluded by the rendered policy — nothing writes them any more, so they are
harmless dead entries left over from before B63; they will simply never
exist. A real bucket (HCP, AWS) needs the plain "everything except
`status/logs/*` when private" shape above, written by hand.
`scripts/compose_init.py` mirrors it for the compose stack.

### Two S3 principals: resolved by removal

Through 0.2.0 one credential served the old CronJob controller and every
batch Job, with a second, more narrowly scoped principal proposed
as future work. As of B63 that open item is moot: the read API never
touches S3 at all (it is a pure Kubernetes API client, read-only RBAC on
Jobs/Pods/ConfigMaps), so the only S3-credentialed pods left are the
campaign and warm-up pods themselves, both scoped by convention to their own
`[<namespace>/]<pipeline>/<volume>/*` prefix.

## Pod security posture (D14 — enforced by the chart)

Every pod the platform runs — the converter-rendered campaign pods, the
per-pipeline warm-up pods, the web front, and (PoC only) the
devStack RustFS, its init Job and the registry — meets Pod Security
**`restricted`**:

- `runAsNonRoot` (`USER` in the images *and* `runAsUser` in the pod spec, so
  neither side can regress alone): uid 1000 for the campaign/warm-up pods,
  the web front and the registry (`registry.runAsUser`), 10001 for RustFS;
  `fsGroup` likewise.
- `capabilities.drop: [ALL]`, `allowPrivilegeEscalation: false`,
  `seccompProfile: RuntimeDefault`.
- `readOnlyRootFilesystem`. Writable paths are explicit: the tmpfs workdir
  (`/work`) carries `HOME`, `TMPDIR` and `YOLO_CONFIG_DIR` — where ultralytics
  settings, triton/inductor JIT caches and temp files land — and both Jobs'
  `sh -c` prologue `mkdir -p`s them before the wrapper is exec'd. The web
  front gets an emptyDir at `/tmp`.
- `automountServiceAccountToken: false` on every pod except the **web
  front**, which is the one pod that legitimately holds an API credential — a
  namespace-scoped Role: get/list/watch on `jobs`, `pods`, `configmaps`,
  nothing else, nothing cluster-wide. Since 0.4.0 that pod is also the one on
  the NodePort: the token-holding process is directly browser-facing rather
  than behind an nginx proxy. The reachable surface is unchanged — `/api/v1`
  was already unauthenticated and anyone who could reach the proxy could
  reach it — but it does mean an RCE in this process reads the token, which
  is why the Role stays read-only and namespace-scoped, and why T03's auth
  layer is the precondition for exposing it beyond the PoC.
- **Secrets are files, not env.** The S3 Secret's `credentials` key (AWS ini
  format) is mounted at `/secrets/s3` (mode `0440`) and reaches boto3 through
  `AWS_SHARED_CREDENTIALS_FILE`; only the non-secret `S3_ENDPOINT` /
  `S3_BUCKET` are env. Nothing does `envFrom` a Secret.
- **The model cache is read-only for campaign pods.** They mount the cache
  PVC `readOnly` and run `HF_HUB_OFFLINE=1`; the per-pipeline warm-up pod is
  the single writer (see [Model handling](../how-it-works/wrapper.md#model-handling)).
  A compromised campaign pod cannot poison the weights every later run loads.

Every pod in both charts is restricted-clean as of B63 — there is no
exception any more (the old devStack git daemon, the one pod that ran
un-restricted, was removed along with the CronJob controller it served:
nothing in the platform reads `git://` at runtime any more). `security.psaEnforce`
still defaults to `baseline`, kept as the default rather than flipped in
this pass, but `restricted` is worth trying now.

The GPU still arrives through `runtimeClassName: nvidia` — runc plus the
NVIDIA hooks, **not** a sandbox. A kernel-isolating runtime (gVisor `nvproxy`,
Kata with GPU passthrough) on the arm64 GPU node is unproven and out of scope
here; it is the next hardening step if one is wanted.

### Namespace labels

Helm cannot label a namespace it did not create, so the Pod Security
Admission labels are applied once by the operator (`make psa-labels`):
`enforce=<security.psaEnforce>`, `warn=restricted`, `audit=restricted`. The
platform's own pods are restricted-clean, and the `warn` label is what
proves it — a regression shows up as an admission warning on Job creation.

## NetworkPolicy (D14 — enforced by the chart)

`templates/network.yaml` (values: `network.*`) plus the read API's own
policy in `templates/web.yaml` renders a namespace-wide **default deny**
(ingress and egress, `network.defaultDeny`), a DNS allow for every pod, and
one policy per pod role. Rules match by CIDR and selector only —
kube-router (k3s) has no FQDN rules, and it evaluates egress *after* service
DNAT, so in-cluster targets are matched by their backing pod and the
apiserver by the node address it resolves to (auto-detected with Helm
`lookup`, overridable via `network.apiServer` / `network.nodeCidrs`).

| Pod | Ingress | Egress (besides kube-dns) | Notably cannot reach |
|---|---|---|---|
| campaign pod (`app=htrflow-batch`) | none | S3 (RustFS pod or `network.s3Cidrs`); the IIIF origin(s) `network.iiifCidrs` on 443/80 (default `lbiiif.riksarkivet.se`) | HF Hub, the apiserver, the registry, Harbor, anything else in-cluster, the internet |
| warm-up pod (`app=htrflow-warmup`) | none | the public internet on 443 minus pod/service/node ranges (HF Hub is a CDN — there is no CIDR to pin) | S3, the apiserver, anything in-cluster |
| web front (`app=htrflow-web`) | `network.web.ingressCidrs` on 8081 (browsers; NodePort traffic arrives SNAT'd from the node) | the apiserver only (`network.apiServer.cidr`) | S3, the IIIF origin, HF Hub, anything else in-cluster |
| RustFS (`app=rustfs`, devStack) | 9000 from anywhere (and 9001 with the console) | none | — |
| rustfs-init hook (`app=rustfs-init`, devStack) | none | RustFS 9000 | — |

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

Verified on the k3s PoC (2026-08-25, [test log](test-log.md), pre-B63): a
probe pod labelled `app=htrflow-batch` reaches only the IIIF origin and
RustFS; HF Hub, the apiserver, the in-cluster registry, Harbor, the node and
the public internet are rejected, while an unlabelled control pod reaches
all of them. The shape is unchanged by B63 — only the pod roles that emit it
moved from the old CronJob controller to the converter's render output.

## Cache PVC migration

Pods used to run as root, so a cache PVC warmed before this change is
root-owned and the non-root warm-up cannot write into it. Once, before the
first warm-up: `chown -R 1000:1000` the PVC from a throwaway pod (or start
with a fresh PVC). `local-path` volumes do not honour `fsGroup`, which is why
this is not automatic. The same applies to the devStack registry's data PVC
when it was written by an older root-running registry.

## devStack caveats

`charts/htrflow-devstack`'s components exist purely to stand up a disposable
single-node PoC ([Local k3s development](local-k3s.md)) and are off by
default:

- **RustFS** credentials are generated on first install (32 random chars,
  re-read from the Secret on upgrade) or set via
  `rustfs.{accessKey,secretKey}`; the S3 API is a NodePort (30900) reachable
  by anyone on the node's network, the admin console is off. Its data is one
  unreplicated `local-path` PVC. Never enable it next to real data; point
  `s3.existingSecret` (in `charts/htrflow-batch`) at a real Secret instead.
- **The registry** is unauthenticated by design (PoC image-iteration
  convenience; Harbor is the real registry on the cluster). Anyone on the
  network can push over a tag — which is exactly why the batch chart refuses
  tag references for the control-plane images unless `security.allowTagImages`.
- The compose smoke stack uses throwaway `rustfsadmin` credentials from
  `.env.example`; never reuse them for a cluster.
