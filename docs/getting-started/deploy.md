# Deploy

`charts/htrflow-batch` deploys everything the platform runs besides the
campaigns: the Kueue queue objects, the model-cache PVC, the web front (the
campaign browser, Universal Viewer and the read-only status API in one
Deployment), the Kyverno policies and the NetworkPolicies. Campaigns are not
part of the chart. The converter renders them from a campaigns repo, and
`htrflow-campaigns apply` or a GitOps tool applies them
([Run a campaign](campaigns.md)). Every chart value is in
[Chart values](../reference/chart.md).

This page is the production-shaped install, with your own S3 and the
policies on. For a disposable dev cluster where one extra chart provides S3,
a registry and the device plugin, see [Try it](try-it.md).

## Install

Check the [prerequisites](index.md) first: Kueue, Kyverno, GPU nodes with the
device plugin, a bucket and its Secret. Then:

```bash
helm install htr charts/htrflow-batch -n <namespace> --create-namespace \
  -f charts/htrflow-batch/values-prod.yaml \
  --set publicResultsBase=<results-base-url> \
  --set web.image=<registry>/htrflow-web@sha256:<digest> \
  --set network.iiifCidrs='{<iiif-source-cidr>}' \
  --set network.s3Cidrs='{<s3-endpoint-cidr>}' \
  --set network.clusterCidrs='{<pod-cidr>,<service-cidr>}' \
  --set network.apiServer.cidr=<apiserver-address>/32 \
  --set network.web.ingressCidrs='{<range-that-may-reach-the-web-front>}'
make psa-labels
```

`values-prod.yaml` is the profile to start from, and it is only the
switches: the Kyverno policies on and enforcing, the image allow-list set to
what the release publishes, model revisions required, image signatures
verified, and Pod Security at `restricted`. The chart's own defaults leave
all of those off, because a policy nothing reconciles is worse than no
policy on a cluster without Kyverno — but that means a default install
enforces none of it. So the chart does not render that silently: an install
with `security.policies.enabled` false fails with one sentence unless
`security.policies.allowDisabled` is true, the explicit statement that the
namespace is meant to run with no admission policy.

Everything on the `--set` lines is what one cluster cannot know for
another. The profile deliberately does not guess them, so an install that
forgets one fails asking for it rather than rendering something plausible.

The chart is installed from a checkout of this repository. `make psa-labels`
and the other cluster targets take the release name and namespace from
`HTR_RELEASE` and `HTR_NAMESPACE` in the repo-root `.env` (defaults `htr` and
`htr-batch`), so set those to match.

The campaigns repo's `converter.yaml` names three objects this chart creates,
and the names must agree:

| `converter.yaml` | Chart value | Default |
|---|---|---|
| `queue` | `queue.name` | `htr-batch` |
| `s3_secret` | `s3.existingSecret` | `htr-batch-s3` |
| `data_pvc` | `modelCache.name` | `htr-test-data` |

Its `namespace` is the release namespace, and its `public_results_base` is
the same URL as `publicResultsBase`.

To render without a cluster, run `helm template` with the same flags, plus
`network.nodeCidrs` and `network.apiServer.cidr`. Without a cluster, the
chart cannot look these up.

## Required values

| Value | What to set it to |
|---|---|
| `publicResultsBase` | The URL base browsers use to reach the results bucket. Every result URL in a published manifest is built from it, so it must be set. |
| `web.image` | The web front image, pinned by digest. The default is the published image. The chart refuses a tag unless `security.allowTagImages` is true, because anyone who can push to the registry could otherwise replace the web front in place. Tag images are pulled on every rollout. |
| `network.apiServer.cidr` | The kube-apiserver address as a pod reaches it after service DNAT (port `network.apiServer.port`, default 6443). The read API's NetworkPolicy needs it. With several API servers (an HA control plane), list the others in `network.apiServer.cidrs`: after DNAT a connection may land on any of them, and each one left out drops a share of the calls. When both are empty, every address of the `kubernetes` Endpoints is looked up at install time, if the kubeconfig may read them. Set them explicitly otherwise, and always for `helm template`. |
| `network.iiifCidrs` | The address ranges of your IIIF source, and of any host that `images:` volumes point at. Campaign pods may reach these on ports 80 and 443. A catch-all range covering every IPv4 address is accepted and still excludes the cluster, node, API server, link-local, loopback and `network.privateCidrs` ranges. The default names one specific IIIF host, so always set this. |
| `network.s3Cidrs` | The S3 endpoint's address ranges. Campaign pods and the web front's read API both reach S3 through this, on `network.s3Ports` (default 443) and no other port. Set that too if your endpoint answers elsewhere. |
| `network.clusterCidrs` | Your cluster's pod and service CIDRs. Warm-up pods get public egress except to these ranges and the node addresses. The default is one distribution's defaults, so set yours. |
| `network.privateCidrs` | The private ranges no catch-all egress may reach, on top of the cluster and node ranges and the link-local and loopback blocks, which are always excluded. The default is the three private blocks; set it only if your network is planned differently. A range you name in `iiifCidrs` or `s3Cidrs` stays reachable. |
| `network.nodeCidrs` | Node addresses. Looked up at install time, like the API server address. |
| `web.internalResultsBase` | Where the read API pod itself reaches the bucket, whenever `publicResultsBase` does not resolve to the bucket from inside the cluster. See [Exposing the web front](viewing.md#exposing-the-web-front). |
| `network.web.ingressCidrs` | The address ranges that may reach the web front. It defaults to every address, in front of a NodePort with no authentication of its own, and the chart refuses to render that default unless `network.web.allowPublicIngress` also says so. Either list your clients' ranges here (their own addresses, not the node range), or set that flag to accept the catch-all. An empty list, or any entry wider than `/8`, needs the flag too. |
| `security.allowedImageRepos`, `security.policies.enabled` | Your registry prefixes, and the Kyverno policies on. See [Hardening](#hardening-the-chart-cannot-do-alone). |

The web image carries the read API, the campaign browser and the Universal
Viewer. Build it with `make build-web` or `dagger call build-web`, and
publish it with `dagger call publish-docker --component web`
([Releasing](../development/releasing.md)). It runs unprivileged and listens
on port 8081. The Service is a NodePort on `web.nodePort` (default 30800),
answered on the node that runs the pod (see *Web front ingress* below).

### Queue quota

`queue.resources` is a plain list of covered resources for the ClusterQueue.
Every resource a Job requests must be covered, or Kueue marks the Job
inadmissible. The default admits exactly one campaign index as the converter
renders it (requests: cpu 4, memory 8 Gi, 1 GPU). Raise the quotas to run
more volumes in parallel:

```yaml
queue:
  resources:
    - name: cpu
      quota: 8
    - name: memory
      quota: 16Gi
    - name: nvidia.com/gpu
      quota: 2
```

Keep the campaigns repo's `window` at what this quota can actually admit
([Queueing](../how-it-works/queueing.md)).

### Model cache

The chart renders the model-cache PVC (`modelCache.*`, default 30 Gi,
`ReadWriteOnce`, kept on uninstall). The chart itself never writes to it.
The converter's warm-up Jobs fill it, and campaign Jobs mount it read-only.
The cache is never evicted: a pipeline's models stay until the PVC is
dropped. If the cluster already has a PVC of that name, either set
`modelCache.create=false` or adopt it into the release. The commands are
under "Adopting hand-applied resources" in the
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md).

## S3 Secret, bucket policy and CORS

The chart never creates the S3 Secret. The converter's Jobs expect a Secret
named `s3.existingSecret` in the release namespace with three keys:

- `credentials`: an AWS ini file. Pods mount it as a file and never read
  credentials from the environment.
- `S3_BUCKET`: the bucket name.
- `S3_ENDPOINT`: the endpoint URL, for anything but AWS itself.

```ini
[default]
aws_access_key_id = …
aws_secret_access_key = …
```

```bash
kubectl -n <namespace> create secret generic htr-batch-s3 \
  --from-file=credentials=<credentials-file> \
  --from-literal=S3_BUCKET=<bucket> \
  --from-literal=S3_ENDPOINT=<s3-endpoint-url>
```

The browser fetches manifests, ALTO, source manifests and run logs straight
from the bucket, and gets everything else from `GET /api/v1/jobs` on the web
front. The bucket therefore needs anonymous `s3:GetObject` on these keys,
with listing denied:

| Keys | Anonymous read |
|---|---|
| `<namespace>/<pipeline>/<volume>/*`: results, `iiif.json`, `progress.json`, `manifest.json` | always |
| `<namespace>/sources/*`: synthetic manifests for `images:` volumes | always |
| `status/logs/*`: per-volume run logs | only if the campaign browser should link them. A run log can carry the redacted form of a private IIIF URL and whatever htrflow prints, so keep it private and serve logs through an authenticated proxy instead |

CORS must allow `GET` and `HEAD` from the web front's origin:

```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["<web-front-origin>"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3600
    }
  ]
}
```

## Hugging Face token, for a private model

Only needed when a pipeline pulls a model that is **private or gated** on
Hugging Face Hub. No chart creates this Secret either. Make one in the
campaign namespace with a single `token` key, holding a Hub token with
**read** scope:

```bash
kubectl -n <namespace> create secret generic htr-batch-hf \
  --from-literal=token=<hugging-face-read-token>
```

Then name it in the campaigns repo's `converter.yaml`:

```yaml
hf_token_secret: htr-batch-hf
```

The converter renders it as `HF_TOKEN` into that pipeline's **warm-up Job**
and nowhere else — the warm-up is the only pod the NetworkPolicy lets reach
the Hub, and it downloads the models once into the cache PVC. Campaign pods
run `HF_HUB_OFFLINE=1` against that cache and never see the token. Leave the
key unset for public models
([The model cache](../how-it-works/wrapper.md#the-model-cache)).

The dev cluster's `rustfs-init` hook applies the same shape
(`charts/htrflow-devstack/templates/_helpers.tpl`, `bucketPolicy`). The
[S3 layout](../reference/s3-layout.md) lists every key. The
[Security](../how-it-works/security.md) page explains why it is this split.

## Hardening the chart cannot do alone

- **Namespace labels.** Helm cannot label a namespace it did not create, so
  run `make psa-labels` once after each install or upgrade. It sets Pod
  Security Admission `enforce` to the release's `security.psaEnforce`
  (`baseline` by default, `restricted` under `values-prod.yaml`), and `warn`
  and `audit` to `restricted`. The platform's pods are restricted-clean, so
  it is a level they actually meet.
  `PSA_ENFORCE=…` overrides the level before the first install.
- **Trust boundary.** `values-prod.yaml` turns all of this on:
  `security.policies.enabled`, the allow-list, `requireModelRevision` and
  `verifyImages`. The Kyverno policies are the only thing that enforces the
  allow-list and the model-revision rule, and an empty list lets any image
  run on the GPU. Installing without the profile means setting each by hand,
  and leaving the policies off means setting `security.policies.allowDisabled`
  as well: without either, the chart refuses to render.
  The subject is the signing workflow's own identity, and publishing is a
  manual dispatch, so it carries the branch the run started from and never a
  tag; `values.yaml` has the example to copy
  ([Security → Trust boundary](../how-it-works/security.md#trust-boundary)).
- **Model-cache ownership.** Platform pods run as uid 1000. A cache volume
  first written by a root-running pod, on a volume plugin that ignores
  `fsGroup`, needs a one-time `chown -R 1000:1000` from a throwaway pod
  before the first warm-up. Starting with a fresh PVC also works.
- **Run logs.** Keep `status/logs/*` private if run logs may carry anything
  sensitive (see the table above).
- **Web front ingress.** `network.web.ingressCidrs` limits who can reach
  the web front's port. The default allows every address, so the chart will
  not render it unless `network.web.allowPublicIngress` is also set — an
  install that says nothing about ingress fails with that sentence rather
  than quietly opening the port. An empty list is refused the same way: a
  rule with no sources admits every address, so it would open the port it
  looks like it closes, and so is any entry wider than `/8`
  (`0.0.0.0/1` plus `128.0.0.0/1` is every address in two lines). List the
  ranges your browsers are in. The Service uses
  `externalTrafficPolicy: Local`, so the policy sees each client's own
  address, not the node's: do not add the node range for the NodePort's
  sake, because every client that reaches a node would then match it. Two
  consequences. Only the node that runs the web front's pod answers on the
  NodePort (`kubectl -n <namespace> get pod -l app=htrflow-web -o wide`
  names it). And a browser that comes in through an SSH forward to the
  node itself arrives from that node's own address, so list that
  one address (`/32`) if that is how you reach it.

### Behind an ingress controller

An ingress-nginx controller in front of the web front is a ClusterIP
Service and an Ingress instead of the NodePort above. Four values switch
the mode on:

- `web.service.type=ClusterIP` -- the Service the controller routes to.
- `web.ingress.enabled=true` -- renders the Ingress.
- `web.ingress.host` -- the hostname the Ingress routes.
- `network.web.ingressFrom` -- the NetworkPolicy peers (namespaceSelector /
  podSelector) allowed to reach the web port, in place of
  `network.web.ingressCidrs`. Behind a controller the pod only ever sees
  the controller's own address, never the browser's, so the client ranges
  the section above describes belong on the controller's own allow-list
  now, not here.

`web.ingress.className` and `web.ingress.tlsSecretName` are optional; set
the latter for TLS terminated at the Ingress. The chart refuses to render
`web.ingress.enabled` without `web.service.type=ClusterIP`, `web.ingress.host`
and `network.web.ingressFrom` all set, each with its own sentence.
`network.web.ingressFrom` takes selectors only: an address range is
refused, and so is a selector that selects everything (`{}`, or an empty
`matchLabels`).

**In this mode the chart's address guards do not apply.**
`network.web.ingressCidrs` and its refusals are skipped, and the
NetworkPolicy admits the controller, not any address. The web front has no
authentication of its own, so the Ingress is open to anyone who can reach
the controller unless the controller is told who may use it. Set that on
the Ingress, through `web.ingress.annotations`:

```yaml
web:
  ingress:
    annotations:
      nginx.ingress.kubernetes.io/whitelist-source-range: "192.0.2.0/24,198.51.100.0/24"
```

or controller-wide, with the same key in the ingress-nginx controller's
ConfigMap. Either way the controller must see the browser's own address
(its Service with `externalTrafficPolicy: Local`, or the PROXY protocol
from the load balancer in front of it); if it sees a node's or a load
balancer's address instead, the allow-list matches that.

## Upgrading

```bash
helm upgrade htr charts/htrflow-batch -n <namespace> --reset-then-reuse-values [--set …]
make psa-labels
```

Always use `--reset-then-reuse-values`, or pass a full values file. Plain
`--reuse-values` keeps the old chart's defaults, so new values such as the
`network.*` block never arrive, and the chart fails when `network` is
missing. A release that runs with the policies off has to carry
`security.policies.allowDisabled=true` from here on, or the upgrade fails
with the sentence that names it. Breaking changes between chart releases, and what to do about each,
are listed under "Upgrading" in the
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md).
