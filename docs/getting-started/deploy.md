# Deploy

The production install: your own S3, the security policies on. Work down
the steps in order. Campaigns come afterwards, from a campaigns repo
([Run a campaign](campaigns.md)). For a disposable one-node cluster instead,
see [Dev cluster](../development/dev-cluster.md).

## 1. Check the cluster

- **Kubernetes with Indexed Jobs and per-index retries**:
  `completionMode: Indexed`, `backoffLimitPerIndex`, and a
  `podFailurePolicy` with `FailIndex`.
- **A CNI that enforces NetworkPolicy.** The chart renders a default
  deny; a CNI that ignores it leaves every pod unrestricted, silently.
- **A StorageClass for the model cache.** `ReadWriteOnce` (the default)
  pins every campaign pod to one node; with more than one GPU node, use a
  `ReadWriteMany` class (`modelCache.accessModes`).
- **GPU nodes** with an NVIDIA GPU the wrapper image's CUDA build
  supports, the NVIDIA device plugin (nodes advertise `nvidia.com/gpu`),
  and a RuntimeClass for GPU pods (`nvidia`, or whatever the campaigns
  repo's `runtime_class` names).
- **An S3-compatible bucket** that browsers can reach at a stable URL,
  the *results base URL*. It is written into every published manifest.
- **A IIIF source** (Presentation 2 or 3 manifests, or plain image URLs)
  that campaign pods can reach, and its address range.
- **Public egress for the warm-up pods**, which download models from
  the Hugging Face Hub. Campaign pods reach only DNS, S3 and the IIIF
  source.
- **Tools** on the machine you deploy from: `git`, `kubectl`, `helm`,
  `make` and `uv`.

## 2. Install Kueue and Kyverno

The chart renders the queue objects and the policies, not the controllers
that act on them. Install both from a checkout of the release you deploy:

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch && cd htrflow-batch
git checkout <release-tag>
make install-kueue      # Kueue's Helm chart; safe to re-run
make install-kyverno    # Kyverno's Helm chart, in namespace kyverno
```

A Kueue installed from the upstream manifests must be moved to the Helm
chart once; Helm does not adopt objects it did not create.

## 3. Prepare the bucket

The wrapper writes with credentials from a Secret in the release namespace
that you create; no chart does. It has three keys:

- `credentials`: an AWS ini file (pods mount it as a file):

    ```ini
    [default]
    aws_access_key_id = …
    aws_secret_access_key = …
    ```

- `S3_BUCKET`: the bucket name.
- `S3_ENDPOINT`: the endpoint URL, for anything but AWS itself.

```bash
kubectl create namespace <namespace>
kubectl -n <namespace> create secret generic htr-batch-s3 \
  --from-file=credentials=<credentials-file> \
  --from-literal=S3_BUCKET=<bucket> \
  --from-literal=S3_ENDPOINT=<s3-endpoint-url>
```

Browsers fetch manifests, ALTO and run logs straight from the bucket, so it
needs anonymous `s3:GetObject` on these keys, with listing denied:

| Keys | Anonymous read |
|---|---|
| `<namespace>/<pipeline>/<volume>/*` (results, `iiif.json`, `progress.json`, `manifest.json`) | always |
| `<namespace>/sources/*` (manifests for `images:` volumes) | always |
| `status/logs/*` (run logs) | only if the campaign browser should link them. A run log can carry the redacted form of a private IIIF URL and whatever htrflow prints; otherwise keep it private and serve logs through an authenticated proxy |

And a CORS rule that allows `GET` and `HEAD` from the web front's origin:

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

[S3 layout](../reference/s3-layout.md) lists every key;
[Security](../how-it-works/security.md#the-bucket-policy) explains the split.

## 4. Install the chart

```bash
helm install htr charts/htrflow-batch -n <namespace> \
  -f charts/htrflow-batch/values-prod.yaml \
  --set publicResultsBase=<results-base-url> \
  --set network.apiServer.cidr=<apiserver-address>/32 \
  --set network.iiifCidrs='{<iiif-source-cidr>}' \
  --set network.s3Cidrs='{<s3-endpoint-cidr>}' \
  --set network.clusterCidrs='{<pod-cidr>,<service-cidr>}' \
  --set network.web.ingressCidrs='{<client-cidr>}'
make psa-labels HTR_RELEASE=htr HTR_NAMESPACE=<namespace>
```

| Value | What to set it to |
|---|---|
| `publicResultsBase` | The results base URL: where browsers reach the bucket, with the bucket in the path. |
| `network.apiServer.cidr` | The kube-apiserver address as pods reach it. Further HA API servers go in `network.apiServer.cidrs`; with both empty, it is looked up from the cluster. |
| `network.iiifCidrs` | Your IIIF source, and any host `images:` volumes point at. |
| `network.s3Cidrs` | The S3 endpoint, on `network.s3Ports` (default 443). |
| `network.clusterCidrs` | Your pod and service CIDRs, which the warm-up pods' public egress excludes. |
| `network.web.ingressCidrs` | Your browsers' address ranges, not the node range. See [Web front access](#web-front-access). |

`values-prod.yaml` turns on everything that enforces the trust boundary:
the Kyverno policies, the image allow-list (the published images only),
revision-pinned models, signature verification, and Pod Security
`restricted`. An install that misses a value fails asking for it. Every
other value, and its default, is in [Chart values](../reference/chart.md).
If `publicResultsBase` does not resolve from inside the cluster, also set
`web.internalResultsBase` to an address that does
([View results](viewing.md#exposing-the-web-front)).

`make psa-labels` sets the namespace's Pod Security labels, which Helm
cannot set on a namespace it did not create. Run it after every install and
upgrade.

## 5. Check it

```bash
kubectl -n <namespace> get deploy htrflow-web
kubectl -n <namespace> get localqueue
kubectl get clusterqueue
curl -s http://<node-address>:30800/healthz
```

`htrflow-web` is `1/1` ready, the LocalQueue and ClusterQueue exist, and
`/healthz` answers `{"ok": true}`. `http://<node-address>:30800/` is the
campaign browser, empty until the first campaign. The model-cache PVC may
stay `Pending` until the first warm-up if its StorageClass binds on first
use.

Next: [Run a campaign](campaigns.md). Its `converter.yaml` names objects
this chart created, and they must agree:

| `converter.yaml` | Chart value | Default |
|---|---|---|
| `namespace` | the release namespace | `htr-batch` |
| `queue` | `queue.name` | `htr-batch` |
| `s3_secret` | `s3.existingSecret` | `htr-batch-s3` |
| `data_pvc` | `modelCache.name` | `htr-test-data` |
| `public_results_base` | `publicResultsBase` | none |

## Web front access

The web front has no authentication. In the default NodePort mode
(`web.nodePort`, default 30800) `network.web.ingressCidrs` is who may reach
it. The chart refuses every address, an empty list, or an entry wider than
`/8`, unless `network.web.allowPublicIngress=true` says that is intended.

- The Service keeps each client's own address
  (`externalTrafficPolicy: Local`), so list the browsers' ranges.
- Only the node running the web front's pod answers on the NodePort.
- A browser coming through an SSH forward to the node arrives from the
  node's own address: list that address as a `/32`.

**Behind an ingress controller**, set all four of `web.service.type=ClusterIP`,
`web.ingress.enabled=true`, `web.ingress.host` and `network.web.ingressFrom`
(selectors for the controller's pods, never an address range).
`web.ingress.className` and `web.ingress.tlsSecretName` are optional. The
pod then only sees the controller, so put the client allow-list on the
controller, for example per Ingress:

```yaml
web:
  ingress:
    annotations:
      nginx.ingress.kubernetes.io/whitelist-source-range: "192.0.2.0/24,198.51.100.0/24"
```

The controller must see the browser's own address (its Service with
`externalTrafficPolicy: Local`, or the PROXY protocol), or the allow-list
matches the wrong one.

## Options

- **More volumes at once.** The default queue quota admits one campaign
  pod (cpu 4, memory 8 Gi, 1 GPU). Raise `queue.resources`, and keep the
  campaigns repo's `window` at what it admits
  ([Queueing](../how-it-works/queueing.md)):

    ```yaml
    queue:
      resources:
        - {name: cpu, quota: 8}
        - {name: memory, quota: 16Gi}
        - {name: nvidia.com/gpu, quota: 2}
    ```

- **A private or gated model.** Create a Secret with a Hugging Face read
  token and name it in `converter.yaml` as `hf_token_secret`. Only the
  warm-up Job sees it
  ([The model cache](../how-it-works/wrapper.md#the-model-cache)):

    ```bash
    kubectl -n <namespace> create secret generic htr-batch-hf \
      --from-literal=token=<hugging-face-read-token>
    ```

- **Your own images.** Add your registry to `security.allowedImageRepos`
  and pin the web image by digest in `web.image`
  ([Releasing](../development/releasing.md)).
- **An existing model-cache PVC.** Set `modelCache.create=false`, or adopt
  it into the release ("Adopting hand-applied resources" in the
  [chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md)).
  The cache is never evicted, and platform pods run as uid 1000: a volume a
  root pod wrote to first, on a plugin that ignores `fsGroup`, needs a
  one-time `chown -R 1000:1000`.
- **Without `values-prod.yaml`** the chart's defaults leave the policies
  off and refuse to render that way unless
  `security.policies.allowDisabled=true`. Then nothing enforces digest pins
  or the image allow-list
  ([Security → Trust boundary](../how-it-works/security.md#trust-boundary)).

## Upgrading

```bash
helm upgrade htr charts/htrflow-batch -n <namespace> --reset-then-reuse-values [--set …]
make psa-labels HTR_RELEASE=htr HTR_NAMESPACE=<namespace>
```

Use `--reset-then-reuse-values` or a full values file, never plain
`--reuse-values`, which keeps the old chart's defaults. Breaking changes are
under "Upgrading" in the
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md).
