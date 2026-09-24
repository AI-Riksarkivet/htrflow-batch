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

With the Argo CD hook ([htrflow-campaigns CLI](../reference/cli.md))
the boundary is wider. Argo CD syncs `argocd/*.yaml` from the campaigns
repo, so its writers also write the hook Job's whole pod spec: its
ServiceAccount, the Secrets it mounts (bucket credentials, Hugging Face
token, git token) and what it runs. Neither the image policies (they check
which image runs, not what it is told to do) nor the job-shape policy (Argo
CD creates the hook under its own identity) bound that. So closing this is a
requirement of running the hook, and it takes one or more of:

- **Review on the repo**: branch protection with required review on the
  campaigns repo, and a required reviewer from the platform's operators for
  `argocd/`.
- **Admission on the hook**: an Argo CD AppProject for the Application that
  allows only the release namespace as its destination and only `Job` and
  `ConfigMap` as the kinds it may create (the hook and `rendered/sync.yaml`
  are all it syncs), together with a Kyverno rule on the hook Job that pins
  the ServiceAccount, the Secrets and volumes it may reference, and its
  command. An AppProject limits kinds and destinations, not what a Job's pod
  spec says, so on its own it does not close the gap.
- **The hook out of the repo**: keep the hook manifest in the platform's own
  deployment repo instead of the campaigns repo, so the campaigns repo's
  writers write campaigns and nothing else.

The hook's `REPO_URL` carries no credentials: the token lives only in the
Secret, and the clone hands it to dulwich from its environment. A token in the
URL would be written into the checkout's `.git/config`, where the apply
container can read it.

The converter renders in the campaigns repo's CI. In the cluster it runs
only in the hook's Job: `validate --rendered`, which refuses a checkout whose
`rendered/` is not its own render, and then `apply`, into the hook's own
namespace. That makes Kyverno the admission step between a merged commit and
a running pod. With `security.policies.enabled`, the API server applies the
chart's `ClusterPolicy` objects to every Job, Pod and pipeline ConfigMap in
the namespace, whoever wrote it, and the campaigns repo's CI can run the same
policies over `rendered/` with the Kyverno CLI. The image rules also cover
ephemeral containers — a rule of their own matches the
`pods/ephemeralcontainers` subresource that `kubectl debug` goes through, and
checks the added container even though the Pod it joins was admitted before —
and image volumes
(`volumes[].image.reference`), which the kubelet pulls like a container
image and which can carry model weights.
The revision rule applies to every ConfigMap with a `pipeline.yaml` key.

RBAC grants a verb over a resource *type*, not one object, so two grants are
wider than the code needs: the read API's `create`/`patch` on ConfigMaps, and
the apply identity's `create`, `patch` and `delete` on Jobs and ConfigMaps.
The `rbac-scope` policy narrows both by matching on the requesting
ServiceAccount (`background: false`, since a background scan has no
requester to match).

| Control | Where | What it closes |
|---|---|---|
| **Digest pin**: `security.policies.enabled` | Kyverno `htrflow-batch-images-pinned-<namespace>`, at admission for every Job and Pod, and in CI | A mutable tag changing what a pipeline id means. `validate` also refuses an `image:` that is not `@sha256:`-pinned |
| **Image allow-list**: `security.allowedImageRepos` | Kyverno `htrflow-batch-images-allowed-<namespace>`, in the same places. An empty list renders no policy | Images from any registry. The match is a path prefix, so an organisation-wide prefix admits every repository in it; name exact repositories when that organisation has other writers |
| **Model revision**: `security.requireModelRevision` | Kyverno `htrflow-batch-model-revision-<namespace>`, on any ConfigMap with a `pipeline.yaml` key, and in CI; `validate` and the wrapper check the same shape ([Pipeline file](../reference/campaign-yaml.md)) | An unpinned Hugging Face repo swapping its weights, or its processor's, under the same pipeline id |
| **Read API write scope**: `security.policies.enabled` | Kyverno `htrflow-batch-rbac-scope-<namespace>`, on every ConfigMap write by the web ServiceAccount | The read API overwriting a pipeline ConfigMap, and so choosing the weights the next campaign loads |
| **Apply identity scope**: `security.policies.enabled` with `apply.rbac.enabled` | The same policy, on every Job or ConfigMap the apply ServiceAccount writes or deletes (it must carry the converter's `managed-by` label, before and after), and on Workloads (only `spec.active`, only on a converter Job's Workload) | A pruning identity reaching an object it never rendered, or patching any Workload field but the pause |
| **Job shape**: `security.policies.enabled` | Kyverno `htrflow-batch-job-shape-<namespace>` (`charts/htrflow-batch/templates/policies/_job-shape.tpl`), on every converter-labelled or campaign/warm-up-labelled Job and every Job the apply identity writes, and in CI. A Job must match the converter's render exactly, so the chart and the converter must come from the same release. It also refuses a keyless `operator: Exists` toleration and host namespaces, and any key but `volumes.txt` / `pipeline.yaml` in the converter's ConfigMaps | A stolen apply token running a pod of its own choosing, a `converter.yaml` naming another Secret or volume, or a pipeline read from where the revision rule never looked |
| **Signed images**: `security.verifyImages.*` (cosign keyless, Sigstore bundles) | Kyverno, at admission for every Pod, image volumes included. CI runs the rule against the published digests ([CI](../development/ci.md)) | Images not built by the CI identity you name. Needs the image signed at publish time |
| **Control-plane digest gate**: `web.image` must be `@sha256:`-pinned unless `security.allowTagImages` is set | The chart template | Anyone with push access to the registry replacing the web front in place |
| **http(s)-only sources, byte caps, redirect caps** | `parse_pipeline`/`parse_campaign`, and the wrapper ([From image to transcription](page-flow.md#the-width-capped-get)) | SSRF and denial of service driven by campaign data |
| **No runtime path to the campaigns repo**, except the Argo CD hook's | The campaigns repo's own CI, outside this system; with the hook, the chart's `apply.gitCidrs` egress rule and the `htrflow-campaigns-git` Secret | Without the hook, nothing in the cluster clones the campaigns repo or holds a credential for it. With it, only the hook pod does: a read-only token from a Secret, and egress to the git host alone |
| **URL redaction** | Wrapper and warm-up logs, the termination log, `page_sources`, and `validate`'s problem lines: userinfo and signing query parameters are removed | A tokenised private IIIF URL ending up in a world-readable log or a pull request comment. It does **not** hide the URL itself — see [Source URLs are not secrets](#source-urls-are-not-secrets) |

These policies are all off by default (`security.policies.enabled: false`),
because a policy nothing reconciles is worse than none, and the chart refuses
to render with them off unless `security.policies.allowDisabled` says so
([Deploy](../getting-started/deploy.md) has the switch). They are the only
thing that enforces the allow-list and the revision rule: with them off, or
without Kyverno, any registry and any unpinned model is admitted, and only
`validate`'s check on the shape of `image:` remains.

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
| `[<namespace>/]<pipeline>/<volume>/*` (results, `iiif.json`, `manifest.json`, `progress.json`), `[<namespace>/]sources/*` | Always. The browser fetches these directly from the results base, never through the platform |
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

### Source URLs are not secrets

A volume's `manifest:` and `images:` URLs are stored **verbatim**, in four
places: the campaign file in git, the rendered campaign under `rendered/`
(committed by the campaigns repo's CI), the `campaign-<name>` ConfigMap in
the cluster, and `page_sources` in each volume's `manifest.json`. So a
presigned URL put into a campaign publishes its signature to everyone who
can read the repo, everyone who can read the namespace, and everyone who can
read the results.

Treat a source URL as public. If the source needs a credential, give it one
the platform holds, not one written into the URL. The converter redacts a URL
only when it echoes one back in a problem line (CI logs, pull request
comments), never the stored value.

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

Nobody, unless a pipeline needs one. Then only the warm-up pod, the one pod
with egress to the Hub, gets it as `HF_TOKEN`, with **read** scope; campaign
pods run `HF_HUB_OFFLINE=1` and never see it. The Secret is the operator's
object ([Deploy](../getting-started/deploy.md#hugging-face-token-for-a-private-model)).

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
  and warm-up pods the workdir `/work` holds `HOME`, `TMPDIR` and
  `YOLO_CONFIG_DIR`, which is where ultralytics settings and temp files land.
  The image carries no compiler and generates no code at run time: it keeps
  torch on its precompiled kernels (`TORCH_DISABLE_NATIVE_JIT=1`), so nothing
  JIT-compiles into these directories. A pipeline that opts into compilation
  fails for want of a compiler. It is a **tmpfs** (`emptyDir` with
  `medium: Memory`, counted against the pod's memory limit) in a campaign
  pod, and a **plain, size-limited `emptyDir` on the node's disk** in a
  warm-up pod, which downloads model weights and has no reason to spend RAM
  on them. Both Jobs' `sh -c` prologue creates those directories before it
  execs. The web front gets an emptyDir at `/tmp`.
- **Service account tokens.** `automountServiceAccountToken: false` is set
  on every pod except the **web front**. It is the one pod that needs an API
  credential: a namespace-scoped Role that reads `jobs`, `pods` and
  `configmaps` and may `create` and `patch` ConfigMaps, for the per-campaign
  status record ([The record a campaign leaves](campaigns.md#the-record-a-campaign-leaves)).
  The name scope on that grant is the `rbac-scope` policy. The web front has
  no authentication of its own, and code execution in it could write
  ConfigMaps as far as admission allows: one status object with the policies
  on, any ConfigMap (pipelines included) without them. So it belongs behind
  an authenticated proxy before anyone outside a trusted network can reach
  it, with `security.policies.enabled` on.
- **Secrets are files, not environment variables.** The S3 Secret's
  `credentials` key (AWS ini format) is mounted at `/secrets/s3` (mode `0440`)
  and reaches boto3 through `AWS_SHARED_CREDENTIALS_FILE`. Only the non-secret
  `S3_ENDPOINT` and `S3_BUCKET` are passed as env. Nothing uses `envFrom` on a
  Secret. The one credential that does travel as env is the optional
  `HF_TOKEN`, because `huggingface_hub` reads its token from the environment;
  it is confined to the warm-up pod, which mounts no S3 Secret, holds no
  campaign data and exits when its download is done.
- **The model cache is read-only for campaign pods, and split by recipe.**
  The warm-up is the only writer, so a compromised campaign pod cannot
  poison later runs' weights, and each recipe's directory is mounted alone
  (`subPath`), so one pipeline's model code (a YOLO `.pt` file is a pickle)
  cannot touch another's ([The Wrapper](wrapper.md#the-model-cache)).

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

Before the first install there is no release to read, so `enforce` is the
chart default, `baseline`; `PSA_ENFORCE=restricted` sets it explicitly. The
target refuses when it cannot read the release, and it refuses any level
other than `baseline` or `restricted`.

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

A wide egress range is never the whole internet. Every egress range the
chart renders — the warm-up's `0.0.0.0/0`, and whatever `network.iiifCidrs`,
`network.s3Cidrs` and `apply.gitCidrs` name — carves out each of these that
lies inside it: the pod, service and node ranges, the API server (by `network.apiServer`, or the looked-up endpoints), link-local
(`169.254.0.0/16`, where a cloud serves instance credentials to any process
that asks), loopback, and `network.privateCidrs` — the three private blocks
by default, which is where the cluster's own network lives. Set that value
if your private plan is a different one. So `0.0.0.0/0`, or the same space
split into halves, reaches none of them. A range that lies inside one of
them, or is one of them, carves nothing out of itself: a range you name is
its own rule, and egress rules are a union, so an on-premises IIIF origin or
S3 endpoint keeps working by being named.

| Pod | Ingress | Egress (besides kube-dns) | Cannot reach |
|---|---|---|---|
| campaign pod (`app=htrflow-batch`) | none | S3 (the in-namespace `app=rustfs` pod on 9000, or `network.s3Cidrs` on `network.s3Ports`); the IIIF origins in `network.iiifCidrs` on 443/80 | Hugging Face Hub, the API server, the registry, anything else in-cluster, the rest of the internet |
| warm-up pod (`app=htrflow-warmup`) | none | the public internet on 443, minus the carve-out above (Hugging Face Hub is a CDN, so there is no CIDR to pin) | S3, the API server, anything in-cluster, link-local and private addresses |
| web front (`app=htrflow-web`) | `network.web.ingressCidrs` on 8081, matched on the client's own address (the Service's `externalTrafficPolicy: Local` keeps it); in ingress mode (`web.ingress.enabled`), the peers in `network.web.ingressFrom` instead: selectors on the ingress controller's pods only, never an address range | every API server (`network.apiServer.cidr` / `cidrs`, or all `kubernetes` Endpoints addresses); S3 (same targets as the campaign pod) for its `progress.json` reader | the IIIF origin, Hugging Face Hub, anything else in-cluster |
| apply pod (`app=htrflow-campaigns`, only with `apply.rbac.enabled`) | none | every API server (`network.apiServer.cidr` / `cidrs`); the git host in `apply.gitCidrs` on `apply.gitPorts` (443 by default), which the Argo CD hook clones the campaigns repo from over HTTPS. Empty by default | S3, the IIIF origin, Hugging Face Hub, anything else in-cluster |
| RustFS (`app=rustfs`, devstack) | 9000 from anywhere (and 9001 when the console is on) | none | — |
| rustfs-init hook (`app=rustfs-init`, devstack) | none | RustFS on 9000 | — |

The web front's ingress list defaults to every address, because the dev
stack and the compose stack are reached from wherever the operator's browser
is. That default is in front of a NodePort with no authentication, so the
chart refuses to render it unless `network.web.allowPublicIngress` says the
exposure is deliberate. So do an empty list, which a NetworkPolicy reads
as every source, and any entry wider than `/8`. Listing the ranges that may
reach it needs no such flag. In ingress mode none of these guards apply:
the NetworkPolicy admits the controller, and who may reach the front is the
controller's allow-list ([Behind an ingress controller](../getting-started/deploy.md#behind-an-ingress-controller)).

Under the default deny, anything applied by hand in the namespace has no
network access unless it gets its own policy. The Argo CD hook is not by
hand: it is `argocd/apply.yaml`, which `htrflow-campaigns init` writes into
the campaigns repo and Argo CD syncs. Its pod carries
`app=htrflow-campaigns`, so the chart's policy lets it reach the API server
it was given an identity for and, with `apply.gitCidrs`, the git host. Any
other pod that runs `htrflow-campaigns apply` in-cluster gets the same by
the same label. `images:` volumes hosted
somewhere other than the IIIF origin need their host added to
`network.iiifCidrs`. A wide range there still excludes everything in the
carve-out above that lies inside it.

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

The warm-up writes the cache as uid 1000. On a provisioner that ignores
`fsGroup`, the PVC needs a one-time `chown` first
([Deploy](../getting-started/deploy.md#model-cache)).

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
