# Deploy

`charts/htrflow-batch` deploys everything the platform runs besides the
campaigns: the Kueue queue objects, the model-cache PVC, the web front
(campaign browser, Universal Viewer and read API in one Deployment), the
Kyverno policies and the NetworkPolicies. Campaigns come from a campaigns
repo ([Run a campaign](campaigns.md)).

This is the production-shaped install, with your own S3 and the policies on.
For a disposable dev cluster, see [Try it](try-it.md).

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

`values-prod.yaml` holds only the switches: Kyverno policies on and
enforcing, the image allow-list, model revisions required, signatures
verified, Pod Security at `restricted`. The chart's defaults leave these off
and refuse to render that way unless `security.policies.allowDisabled` is
true ([Security](../how-it-works/security.md) explains why). The `--set`
lines are what one cluster cannot know for another; an install that forgets
one fails asking for it.

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

## Required values

Each is described in full in [Chart values](../reference/chart.md).

| Value | What to set it to |
|---|---|
| `publicResultsBase` | The URL base browsers reach the results bucket at. Every URL in a published manifest is built from it. |
| `web.image` | The web front image, by digest (default: the published image). A tag needs `security.allowTagImages`. |
| `network.apiServer.cidr` | The kube-apiserver address as pods reach it after DNAT. List further HA API servers in `network.apiServer.cidrs`; looked up at install time when both are empty. |
| `network.iiifCidrs` | Your IIIF source, and any host `images:` volumes point at. No default; an empty list is refused. |
| `network.s3Cidrs` | The S3 endpoint, on `network.s3Ports` (default 443). Refused empty under `values-prod.yaml`. |
| `network.clusterCidrs` | Your pod and service CIDRs, which warm-up pods' public egress excludes. Refused empty under `values-prod.yaml`. |
| `network.privateCidrs` | Private ranges no wide egress range reaches. The default suits most networks. |
| `network.nodeCidrs` | Node addresses; looked up at install time. |
| `web.internalResultsBase` | Where the read API reaches the bucket when `publicResultsBase` does not resolve in-cluster ([View results](viewing.md#exposing-the-web-front)). |
| `network.web.ingressCidrs` | Who may reach the web front; see [Web front ingress](#web-front-ingress). |
| `security.allowedImageRepos`, `security.policies.enabled` | Your registry prefixes, and the policies on ([Hardening](#hardening-the-chart-cannot-do-alone)). |

For `helm template` without a cluster, also set `network.nodeCidrs` and
`network.apiServer.cidr`, which cannot be looked up.

The web image carries the read API, the campaign browser and the Universal
Viewer, listens on port 8081, and is served on NodePort `web.nodePort`
(default 30800). To build your own, see
[Releasing](../development/releasing.md).

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
dropped. Platform pods run as uid 1000: a volume first written by a root
pod, on a plugin that ignores `fsGroup`, needs a one-time
`chown -R 1000:1000` from a throwaway pod before the first warm-up (or start
with a fresh PVC). If the cluster already has a PVC of that name, either set
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

The [S3 layout](../reference/s3-layout.md) lists every key, and
[Security](../how-it-works/security.md#the-bucket-policy) why it is this
split. CORS must allow `GET` and `HEAD` from the web front's origin:

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

## Cache source images

Campaign pods can keep every page image they download in a second,
**private** S3 bucket. A volume run again, by a retry, another pipeline or
another campaign, then reads its pages from there and asks the IIIF server
for nothing. It is optional and off by default.

1. **Create the bucket** on the same S3 store as the results, reachable with
   the same `s3.existingSecret` credentials, which need `s3:GetObject`,
   `s3:PutObject` and `s3:ListBucket` on it. Without `s3:ListBucket`, S3
   answers a key that is not there with 403 instead of 404, and the wrapper
   logs the bucket once as unreadable. It must be a separate, private
   bucket, **never the results bucket**: that one is public-read, and a pod
   told to cache there switches the cache off and logs why. Give it **no**
   public policy and no CORS: nothing links to it, and source images can
   carry access rules the ALTO does not. The wrapper never creates it.

    ```bash
    aws s3api create-bucket --bucket images-batch --endpoint-url <s3-endpoint-url>
    ```

2. **Name it in `converter.yaml`** in the campaigns repo. Every campaign
   rendered after that carries it
   ([Campaign & Pipeline YAML](../reference/campaign-yaml.md)):

    ```yaml
    image_cache:
      bucket: images-batch
    ```

3. **Read the counts.** Each volume's run log has one line for the cache,
   after its pages are through:

    ```text
    [R0001203] image cache images-batch: 12 hits, 3 misses, 3 stored
    ```

    A hit is a page read from the bucket. A miss is a page downloaded
    instead: not there yet, or there but not a usable image. Stored counts the
    downloads written back. Fewer stored than misses means a download or a
    write failed, and the log says which above. The same counts are in
    `manifest.json`'s `image_cache`, while `bytes_fetched` counts only what
    the IIIF server sent ([S3 Layout](../reference/s3-layout.md)).

A cached image is reused at whatever width first stored it, since the key
has no width. An image stored by a run with a smaller `MAX_IMAGE_WIDTH` (a
local run can set one) stays that size for every later run until its object
is deleted; the volume's objects share one prefix
([S3 Layout → Image cache bucket](../reference/s3-layout.md#image-cache-bucket)). Nothing is ever evicted; the bucket's own
lifecycle rules can expire objects if wanted. What a hit, a miss or a
cache error does inside the pod is in
[The Wrapper → Image cache](../how-it-works/wrapper.md#image-cache).

On the devstack chart, `s3.imageCacheBucket` creates the bucket, private,
next to the results bucket. Then set `image_cache.bucket` to the same name.
The compose stack has no converter: set `HTR_IMAGE_CACHE_BUCKET` instead,
and its init creates the bucket and its wrapper uses it.

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

The converter renders it as `HF_TOKEN` into that pipeline's warm-up Job only;
campaign pods run offline against the cache and never see the token. Leave
the key unset for public models
([The model cache](../how-it-works/wrapper.md#the-model-cache)).

## Hardening the chart cannot do alone

- **Namespace labels.** Helm cannot label a namespace it did not create, so
  run `make psa-labels` after each install or upgrade. It sets Pod Security
  `enforce` to `security.psaEnforce` (`baseline` by default, `restricted`
  under `values-prod.yaml`) and `warn`/`audit` to `restricted`, which the
  platform's pods meet. `PSA_ENFORCE=…` overrides the level before the first
  install.
- **Trust boundary.** Without `values-prod.yaml`, set
  `security.policies.enabled`, the allow-list, `requireModelRevision` and
  `verifyImages` by hand; an empty allow-list lets any image run on the GPU.
  `values.yaml` has the signing-identity example to copy
  ([Security → Trust boundary](../how-it-works/security.md#trust-boundary)).

## Web front ingress

The web front has no authentication of its own, so who may reach it is set
here. In the default NodePort mode, `network.web.ingressCidrs` lists the
client ranges allowed on its port. The chart refuses to render the default
(every address), an empty list, or any entry wider than `/8` unless
`network.web.allowPublicIngress=true` says that is intended.

The Service uses `externalTrafficPolicy: Local`, so the policy sees each
client's own address:

- List your browsers' ranges, not the node range; the node range would match
  every client.
- Only the node running the web front's pod answers on the NodePort
  (`kubectl -n <namespace> get pod -l app=htrflow-web -o wide`).
- A browser arriving through an SSH forward to the node comes from the
  node's own address, so list that address (`/32`).

### Behind an ingress controller

An ingress-nginx controller in front of the web front replaces the NodePort
with a ClusterIP Service and an Ingress. Four values switch the mode on, and
the chart refuses the mode unless all are set:

- `web.service.type=ClusterIP`
- `web.ingress.enabled=true`
- `web.ingress.host`, the hostname routed
- `network.web.ingressFrom`, the NetworkPolicy peers (`namespaceSelector` /
  `podSelector`) naming the controller's pods, in place of
  `network.web.ingressCidrs`. Selectors only: an address range, or a
  selector that selects everything, is refused.

`web.ingress.className` and `web.ingress.tlsSecretName` (TLS at the Ingress)
are optional.

**In this mode the address guards above do not apply**: the pod only sees
the controller. Put the client allow-list on the controller, per Ingress:

```yaml
web:
  ingress:
    annotations:
      nginx.ingress.kubernetes.io/whitelist-source-range: "192.0.2.0/24,198.51.100.0/24"
```

or controller-wide in its ConfigMap. The controller must see the browser's
own address (its Service with `externalTrafficPolicy: Local`, or the PROXY
protocol from the load balancer), or the allow-list matches the wrong one.

## Upgrading

```bash
helm upgrade htr charts/htrflow-batch -n <namespace> --reset-then-reuse-values [--set …]
make psa-labels
```

Always use `--reset-then-reuse-values`, or pass a full values file: plain
`--reuse-values` keeps the old chart's defaults, so new values never arrive.
A release running with the policies off must carry
`security.policies.allowDisabled=true`. Breaking changes between chart
releases are listed under "Upgrading" in the
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md).
