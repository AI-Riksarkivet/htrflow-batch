# Security

This page covers two questions. What can a pod on this platform do (its
posture and its network)? And who can make it do something (the trust
boundary)?

Every setting on every surface, with what it exposes and who enforces it, is
listed in the generated [Configuration reference](../reference/configuration.md).

## Trust boundary

Anyone who can write to the campaigns repo chooses the container image and
the models that run with the results bucket's write credentials. A pipeline
file names the image and the Hugging Face repos whose weights the warm-up pod
loads, and those weights can be pickles. Three things enforce the limits: the
chart's Kyverno policies (allowed repositories, digest pins, model revisions,
and optional signature verification), the pod posture, and the
NetworkPolicies described below. Give the campaigns repo the same care as CI
configuration.

The converter never runs in the cluster. That makes Kyverno the admission step
between a merged commit and a running pod. When `security.policies.enabled`
is set, the chart ships `ClusterPolicy` objects, and the API server applies
them to every Job, Pod and pipeline ConfigMap in the namespace, whoever wrote
it. The campaigns repo's CI can run the same policies over its rendered output
with the Kyverno CLI, so a bad change fails in the pull request, not at apply
time. The two image rules apply to every Job and Pod, and on a Pod they
cover the ephemeral containers a debugging session attaches as well as the
ones it was created with. The revision rule
applies to every ConfigMap carrying a `pipeline.yaml` key — that key is what
makes a ConfigMap a pipeline, where a label is only a claim about one — and
reads the top-level `steps:` in it.

Admission is also where the platform's own identities are scoped. RBAC
grants a verb over a resource *type* and has no way to name one object, so
two grants the code needs are necessarily wider than the code: the read
API's `create`/`patch` on ConfigMaps, and the apply identity's `delete` on
Jobs and ConfigMaps. The `rbac-scope` policy narrows both by matching on the
requesting ServiceAccount, which only the API server can see. It runs with
`background: false`, because a background scan replays stored objects with
no requester to match.

| Control | Where | What it closes |
|---|---|---|
| **Digest pin**: `security.policies.enabled` | Kyverno `ClusterPolicy` `htrflow-batch-images-pinned-<namespace>`, at admission for every Job and Pod in the namespace, and in the campaigns repo's CI through the Kyverno CLI. Message: `image must be pinned by digest: <image>` | A mutable tag changing what a pipeline id means. `htrflow-campaigns validate` also refuses a pipeline whose `image:` is not `@sha256:`-pinned, because the renderer needs the digest, but it only sees what it renders |
| **Image allow-list**: `security.allowedImageRepos` (with `policies.enabled`) | Kyverno `ClusterPolicy` `htrflow-batch-images-allowed-<namespace>`, in the same two places. Message: `image is not from an allowed repository: <image> — allowed: <list>` | Images from any registry. With an empty list the policy is not rendered and nothing is checked |
| **Model revision**: `security.requireModelRevision` (with `policies.enabled`) | Kyverno `ClusterPolicy` `htrflow-batch-model-revision-<namespace>`, at admission of any ConfigMap with a `pipeline.yaml` key, and in the CLI. Message: `models not pinned to a revision: <models> — add revision: <40-character commit hash> under model_settings (YOLO) or model_settings.model_kwargs (TrOCR and other Hugging Face models)` | An unpinned Hugging Face repo swapping its weights under the same pipeline id |
| **Write scope of a ServiceAccount**: `security.policies.enabled` | Kyverno `ClusterPolicy` `htrflow-batch-rbac-scope-<namespace>`, at admission, on every ConfigMap write by the web ServiceAccount. Message: `the read API may only write a campaign's own status ConfigMap (campaign-<name>-status), not <name>` | The read API using a Role that cannot be scoped to one object name to overwrite a pipeline ConfigMap, and so choose the weights the next campaign loads |
| **Delete scope of the apply identity**: `security.policies.enabled` with `apply.rbac.enabled` | Kyverno `ClusterPolicy` `htrflow-batch-rbac-scope-<namespace>`, at admission, on every Job or ConfigMap the apply ServiceAccount deletes. Message: `the apply identity may only delete objects the converter rendered` | A pruning identity reaching a Job or ConfigMap it never rendered — a running campaign's, or another workload's in the same namespace |
| **Signed images**: `security.verifyImages.*` (Kyverno `ClusterPolicy`, cosign keyless) | At admission, for every Pod in the namespace | Images not built by the CI identity you name. Off by default. Needs the image to be signed at publish time ([CI](../development/ci.md)) |
| **Control-plane digest gate**: `web.image` must be `@sha256:`-pinned unless `security.allowTagImages` is set | The chart template | Anyone with push access to the registry replacing the web front in place |
| **http(s)-only sources, byte caps, redirect caps** | `parse_pipeline`/`parse_campaign`, and the wrapper (`MANIFEST_MAX_BYTES`, `FETCH_MAX_BYTES`, at most 5 redirects, raster images only) | SSRF and denial of service driven by campaign data |
| **No runtime path to the campaigns repo** | The campaigns repo's own CI, outside this system | Nothing in the cluster clones the campaigns repo or holds a credential for it |
| **URL redaction** | Wrapper logs, the termination log, `page_sources` | A tokenised private IIIF URL ending up in a world-readable log |

These policies are all off by default (`security.policies.enabled: false`),
because a policy nothing reconciles is worse than none. They are the only
thing that enforces the image allow-list and the model-revision rule. A rule
inside the converter would only ever see what the converter rendered, and a
Job made by hand would get straight past it. So if the policies stay
disabled, or are enabled in a cluster without Kyverno, any registry and any
unpinned model is admitted. Without Kyverno, the one check left is on the
*shape* of a pipeline's `image:`, which `htrflow-campaigns validate` still
makes. `make install-kyverno` installs Kyverno for a dev cluster. Newer
Kyverno releases deprecate the `kyverno.io` `ClusterPolicy` kind in favour of
the CEL-based `ValidatingPolicy`. The chart still ships `ClusterPolicy`.

Beyond admission, the posture and policies below bound what a pod can do. It
runs as non-root, with no capabilities, a read-only root filesystem and a
read-only model cache. Its egress is limited to DNS, S3 and the IIIF origin,
and it holds no API credential. The read API is the one exception, covered
below.

### The bucket policy

Browsers read the results bucket anonymously, so the real question is which
keys they may read. Listing is never allowed.

| Keys | Anonymous read |
|---|---|
| `[<namespace>/]<pipeline>/<volume>/*` (results, `iiif.json`, `manifest.json`, `progress.json`), `sources/*` | Always. The browser fetches these directly from the results base, never through the platform |
| `status/logs/*` (run logs) | Only if your policy allows it. The campaign browser links to run logs, but a log can carry the redacted form of a private IIIF URL and anything htrflow prints. Keep them private once the run-log view sits behind an authenticated proxy |

That is the complete list. `status/logs/<pipeline>/<volume>.txt` is the only
key anything writes under `status/` (`ResultStore.run_log_key`), so the
private set is either empty or that one prefix. A production bucket needs the
same shape, written for your S3 implementation.

The devstack chart's `rustfs-init` hook renders this policy from
`rustfs.publicLogs`, which defaults to `true`:

- With `publicLogs` true, the policy is a plain `Allow` on `s3:GetObject`
  for `*`.
- With `publicLogs` false, it is an `Allow` with
  `NotResource: status/logs/*`.

It uses `NotResource` because RustFS applies a `Deny` statement to the
credentialed principals as well, and ignores a condition that targets only
anonymous callers. `scripts/compose_init.py` mirrors the same policy for the
compose stack.

### Who holds S3 credentials

One S3 Secret is mounted, and only into campaign pods. Each campaign pod
writes under its own `[<namespace>/]<pipeline>/<volume>/` prefix and
`status/logs/`. That scoping is a convention, because the credential itself
covers the bucket.

- **Warm-up pods** mount no S3 Secret.
- **The read API** holds no S3 credential. It reads `progress.json` with
  anonymous HTTP GETs through `HTRFLOW_INTERNAL_RESULTS_BASE`, using the same
  public-read policy a browser relies on.

### Who holds a Hugging Face token

Nobody, unless a pipeline needs one. A private or gated model is downloaded
by the warm-up pod, which is the only pod with egress to the Hub, and the
only pod that can hold the token: `converter.yaml`'s `hf_token_secret` names
a Secret with a `token` key, and the converter renders it as `HF_TOKEN` on
the warm-up container alone
([The model cache](wrapper.md#the-model-cache)). The token needs **read**
scope and nothing more. Campaign pods get neither the env nor the Secret
name — they run `HF_HUB_OFFLINE=1` against the filled cache — so the
credential never reaches the long-lived pod that runs model code over
fetched images. No chart template creates or reads this Secret; it is the
operator's object, like the S3 one.

## Pod security posture

Every pod the platform runs meets Pod Security **`restricted`**: the
converter-rendered campaign pods, the per-pipeline warm-up pods, the web
front, and the devstack chart's RustFS and its init Job. The devstack registry
is the one exception. Its registry container is restricted, but with
`registry.fixOwnership` (the default) a `fix-ownership` init container runs
as root, with `CHOWN`, `FOWNER` and `DAC_OVERRIDE`, to chown the registry's
data volume.

- **Non-root.** `runAsNonRoot` is set both as `USER` in the images and as
  `runAsUser` in the pod spec, so neither side can regress alone. The campaign
  and warm-up pods, the web front and the registry run as uid 1000
  (`registry.runAsUser`). RustFS runs as uid 10001. `fsGroup` matches in each
  case.
- **Capabilities.** `capabilities.drop: [ALL]`,
  `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`.
- **Read-only root filesystem.** Writable paths are explicit. In campaign
  and warm-up pods, the tmpfs workdir (`/work`) holds `HOME`, `TMPDIR` and
  `YOLO_CONFIG_DIR`, which is where ultralytics settings, triton/inductor JIT
  caches and temp files land. Both Jobs' `sh -c` prologue creates those
  directories before it execs the wrapper. The web front gets an emptyDir at
  `/tmp`.
- **Service account tokens.** `automountServiceAccountToken: false` is set
  on every pod except the **web front**. It is the one pod that needs an API
  credential: a namespace-scoped Role, nothing cluster-wide. It reads `jobs`,
  `pods` and `configmaps` — and it is no longer only a reader. It also holds
  `create` and `patch` on ConfigMaps, because the per-campaign status record
  it writes is what still answers for a campaign once the Job behind it is
  past its TTL. RBAC cannot scope a verb to one object name, so that grant
  covers every ConfigMap in the namespace; the name scope is the `rbac-scope`
  policy above instead. The web front is also the pod browsers reach, and it
  has no authentication of its own. Remote code execution in that process
  would read the token and could then write ConfigMaps as far as admission
  allows: one campaign's status object with the policies on, any ConfigMap in
  the namespace — the pipelines a Job mounts included — without them. So the
  web front belongs behind an authenticated proxy before anyone outside a
  trusted network can reach it, and `security.policies.enabled` counts for
  more now that this pod writes at all.
- **Secrets are files, not environment variables.** The S3 Secret's
  `credentials` key (AWS ini format) is mounted at `/secrets/s3` (mode `0440`)
  and reaches boto3 through `AWS_SHARED_CREDENTIALS_FILE`. Only the non-secret
  `S3_ENDPOINT` and `S3_BUCKET` are passed as env. Nothing uses `envFrom` on a
  Secret. The one credential that does travel as env is the optional
  `HF_TOKEN`, because `huggingface_hub` reads its token from the environment;
  it is confined to the warm-up pod, which mounts no S3 Secret, holds no
  campaign data and exits when its download is done.
- **The model cache is read-only for campaign pods.** They mount the cache
  PVC `readOnly` and run with `HF_HUB_OFFLINE=1`. The per-pipeline warm-up pod
  is the only writer ([The Wrapper](wrapper.md#the-model-cache)), so a
  compromised campaign pod cannot poison the weights that every later run
  loads.

`security.psaEnforce` defaults to `baseline`. Every pod in `htrflow-batch`,
and every campaign and warm-up pod, is restricted-clean, so `restricted` also
works in a namespace without the devstack registry.

The GPU reaches the pod through `runtimeClassName: nvidia`: runc plus the
NVIDIA hooks. That is not a sandbox. The next hardening step, if you want one,
is a kernel-isolating runtime with GPU support, such as gVisor `nvproxy` or
Kata with GPU passthrough. The project has not tested either.

### Namespace labels

Helm cannot label a namespace it did not create, so the operator applies the
Pod Security Admission labels once with `make psa-labels`:

- `enforce=<security.psaEnforce>`
- `warn=restricted`
- `audit=restricted`

The `warn` label keeps the restricted-clean claim honest: a regression shows
up as an admission warning when a Job is created.

## NetworkPolicy

`templates/network.yaml` (values: `network.*`) renders the policies,
together with the read API's own policy in `templates/web.yaml`:

- a namespace-wide **default deny** for ingress and egress
  (`network.defaultDeny`)
- a DNS allow for every pod
- one policy per pod role

Rules match by CIDR and selector only. Standard NetworkPolicy has no FQDN
rules. Some CNIs, kube-router among them, also evaluate egress *after*
service DNAT. So the policies match in-cluster targets by their backing pod,
and the API server by its endpoint address. The chart finds that address with
Helm `lookup`, and you can override it with `network.apiServer` and
`network.nodeCidrs`. `network.clusterCidrs` must hold your cluster's pod and
service ranges, because the warm-up pod's public egress excludes them.

A catch-all egress is never the whole internet. Every rule that allows
`0.0.0.0/0` carves out the pod, service and node ranges, link-local
(`169.254.0.0/16`, where a cloud serves instance credentials to any process
that asks), loopback, and `network.privateCidrs` — the three private blocks
by default, which is where the cluster's own network lives. Set that value
if your private plan is a different one. The carve-out is of the catch-all,
not of the address: a range you name in `network.iiifCidrs` or
`network.s3Cidrs` is its own rule, and egress rules are a union, so an
on-premises IIIF origin or S3 endpoint keeps working by being named.

| Pod | Ingress | Egress (besides kube-dns) | Cannot reach |
|---|---|---|---|
| campaign pod (`app=htrflow-batch`) | none | S3 (the in-namespace `app=rustfs` pod on 9000, or `network.s3Cidrs` on `network.s3Ports`); the IIIF origins in `network.iiifCidrs` on 443/80 | Hugging Face Hub, the API server, the registry, anything else in-cluster, the rest of the internet |
| warm-up pod (`app=htrflow-warmup`) | none | the public internet on 443, minus the carve-out above (Hugging Face Hub is a CDN, so there is no CIDR to pin) | S3, the API server, anything in-cluster, link-local and private addresses |
| web front (`app=htrflow-web`) | `network.web.ingressCidrs` on 8081 (NodePort traffic arrives SNAT'd from the node, so include the node range) | the API server (`network.apiServer.cidr`); S3 (same targets as the campaign pod) for its `progress.json` reader | the IIIF origin, Hugging Face Hub, anything else in-cluster |
| apply pod (`app=htrflow-campaigns`, only with `apply.rbac.enabled`) | none | the API server (`network.apiServer.cidr`) | S3, the IIIF origin, Hugging Face Hub, anything else in-cluster. It reads its campaigns from a directory, never from a network |
| RustFS (`app=rustfs`, devstack) | 9000 from anywhere (and 9001 when the console is on) | none | — |
| rustfs-init hook (`app=rustfs-init`, devstack) | none | RustFS on 9000 | — |

The web front's ingress list defaults to every address, because the dev
stack and the compose stack are reached from wherever the operator's browser
is. That default is in front of a NodePort with no authentication, so the
chart refuses to render it unless `network.web.allowPublicIngress` says the
exposure is deliberate. Listing the ranges that may reach it needs no such
flag.

Under the default deny, anything applied by hand in the namespace has no
network access unless it gets its own policy. That includes the pod that
runs `htrflow-campaigns apply` in-cluster: label it `app=htrflow-campaigns`
and the chart's policy lets it reach the API server it was given an identity
for. `images:` volumes hosted
somewhere other than the IIIF origin need their host added to
`network.iiifCidrs`. A catch-all range there still excludes everything in
the carve-out above.

**Known limitation: the policy sync window.** Some CNIs apply a new pod's
policies asynchronously, after the pod already has its IP, and until they do
the pod can send traffic anywhere. In its first seconds the wrapper does
nothing on the network except fetch the (allowed) IIIF manifest, so the
practical exposure is a compromised image's first moments. Closing the window
needs a CNI with synchronous enforcement. A chart change cannot close it.

To check the policies on your own cluster, run a probe pod labelled
`app=htrflow-batch` in the namespace. It should reach only the IIIF origin and
S3. Hugging Face Hub, the API server, the registry, the node addresses and the
wider internet should all be refused.

## Cache PVC ownership

The warm-up pod runs as uid 1000 and writes into the model cache PVC.
`fsGroup: 1000` normally makes a fresh volume writable. Some provisioners,
hostPath-based local provisioners among them, do not apply `fsGroup`. On
those, a volume that anything wrote as root stays root-owned, and the warm-up
fails when it tries to write. The fix is to run `chown -R 1000:1000` over the
PVC once, from a throwaway pod, before the first warm-up, or to start with a
fresh PVC. The devstack registry's data PVC has the same problem, and the
registry's root `fix-ownership` init container (above) re-chowns that PVC on
every start.

## devstack caveats

`charts/htrflow-devstack` stands up a disposable single-node dev cluster
([Dev cluster](../development/dev-cluster.md)). Its components are off by
default:

- **RustFS.** Credentials are generated on first install (32 random
  characters, re-read from the Secret on upgrade), or you set them with
  `rustfs.{accessKey,secretKey}`. The S3 API is exposed on a NodePort that
  anyone on the node's network can reach. The admin console is off. Data lives
  on one unreplicated PVC. Never enable RustFS next to real data. Point
  `s3.existingSecret` (in `charts/htrflow-batch`) at a real Secret instead.
- **The registry** is unauthenticated, for fast image iteration. Anyone on
  the network can push over a tag. That is why the batch chart refuses tag
  references for control-plane images unless `security.allowTagImages` is set.
- **The compose smoke stack** uses throwaway credentials from `.env.example`.
  Never reuse them for a cluster.
