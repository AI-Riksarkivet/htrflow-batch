# htrflow-devstack (Helm chart)

**PoC-only.** This chart renders in-cluster stand-ins for infrastructure a
real deployment gets from elsewhere: an S3-compatible object store
(RustFS) and a container image registry, plus the NVIDIA device plugin +
`RuntimeClass`. Every component is off by default (`rustfs.enabled`,
`registry.enabled`, `nvidiaDevicePlugin.enabled`) — nothing renders on a
plain `helm install`.

It exists to reproduce the [htrflow-batch](../htrflow-batch) chart's PoC on
a bare k3s node without hand-applied manifests. **Do not install this on a
cluster with real data or real S3 credentials.** None of these objects are
production-shaped: the registry is unauthenticated, RustFS's console (when
enabled) has no auth in front of it beyond the root credential.

## Split from htrflow-batch (0.3.0)

Through htrflow-batch 0.2.0 these objects (`templates/devstack-*.yaml`) lived
in the main chart behind `devStack.*` values. They moved here in 0.3.0 (B63)
because they have nothing to do with what the platform actually runs
(campaigns are Indexed Jobs rendered by `packages/converter` and applied
outside any chart) — bundling PoC scaffolding with the production chart's
values and size budget was the wrong shape. `devStack.allowTagImages` moved
the other way, into htrflow-batch's `security.allowTagImages`, because it
gates htrflow-batch's own control-plane image (`web.image`),
not anything here.

**No git daemon.** The original `devstack-gitdaemon.yaml` served a bare
campaigns repo over `git://` for the old GitOps CronJob controller to poll.
That controller is gone as of htrflow-batch 0.3.0 (B63): campaigns are Indexed
Jobs rendered by `packages/converter` and applied with `kubectl apply` (or
committed to a repo Argo CD watches) — nothing in the platform reads `git://`
any more, so the daemon had no consumer left and was deleted rather than
carried forward as dead weight (it was briefly re-added with a broadened,
consumer-less NetworkPolicy in an earlier draft of this chart; removed on
review). If you need an in-cluster clone of a campaigns repo again for some
other reason, reintroduce it deliberately with an actual consumer in mind,
not by reverting this chart.

## 0.2.0: credentials nobody chose are refused

`devStack.insecureDefaults` (new, required, default `false`). RustFS's root
credentials are the S3 credentials every batch Job and the results bucket
get, so the render now refuses an empty `rustfs.accessKey`/`secretKey` — Helm
generates those afresh on every render with no cluster to `lookup`, so the
manifest you reviewed is not the one you install and a `template | apply`
flow rotates the keys out from under every running pod — or a value
published in this repo. Set credentials of your own, or set
`devStack.insecureDefaults: true` to say this stack is a toy. `make
install-devstack`, `ci/full-values.yaml` and the PoC command below all set it
explicitly.

## Installing

Install into the **same namespace** as the htrflow-batch release — the two
charts are not linked by shared Helm values (each has its own `Chart.yaml`),
only by naming convention:

- This chart's `s3.bucket` / `s3.secretName` must match htrflow-batch's
  `s3.bucket` / `s3.existingSecret` (both default to `htr-results` /
  `htr-batch-s3`) so the Secret and bucket RustFS creates are the ones the
  platform's pods already expect.
- htrflow-batch's NetworkPolicies (`network.defaultDeny`) apply
  namespace-wide; this chart renders its own ingress/egress allows for each
  pod it creates in that namespace (RustFS, RustFS-init) so they still work
  under that default deny. The registry lives in its own `registry`
  namespace and is unaffected by it.

```bash
helm install htr-devstack charts/htrflow-devstack -n htr-batch --create-namespace \
  --set rustfs.enabled=true --set registry.enabled=true \
  --set nvidiaDevicePlugin.enabled=true \
  --set devStack.insecureDefaults=true   # accepts generated RustFS credentials
```

`make install-devstack` wraps this with the repo-root `.env` constants
(`.env.example` has the PoC defaults) and `NVIDIA_DEVICE_PLUGIN` (default
`true`) in place of the `--set nvidiaDevicePlugin.enabled` above. See
[docs/getting-started/deploy.md](../../docs/getting-started/deploy.md) for
the full PoC replay flow (wrapper/web image builds, warm-up, the
smoke campaign).

**The target refuses `NVIDIA_DEVICE_PLUGIN=false` while GPU pods are
running.** Disabling the device plugin deletes the chart-managed `nvidia`
RuntimeClass and the `nvidia-device-plugin` DaemonSet — every GPU pod
depends on both, and a prior E2E run took a live pod down for two minutes
by deleting them out from under it (`docs/development/e2e-indexed-jobs.md`,
"A failed Helm install still owns what it applied"). `make install-devstack
NVIDIA_DEVICE_PLUGIN=false` first checks the cluster for any pod (outside
`kube-system`, so the device plugin's own pod doesn't block disabling
itself) that is Running or Pending and uses `runtimeClassName: nvidia` or requests `nvidia.com/gpu`,
and exits non-zero with one sentence if it finds one. `FORCE=1` skips the
check.

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. On a cluster where the
`nvidia` RuntimeClass or the kube-system device-plugin DaemonSet were
applied by hand, either keep them outside this chart
(`nvidiaDevicePlugin.enabled=false`) or adopt them once before enabling the
value:

```bash
kubectl -n kube-system annotate daemonset nvidia-device-plugin \
  meta.helm.sh/release-name=htr-devstack meta.helm.sh/release-namespace=htr-batch --overwrite
kubectl -n kube-system label daemonset nvidia-device-plugin app.kubernetes.io/managed-by=Helm --overwrite
# same two commands for: runtimeclass nvidia (cluster-scoped, no -n)
```

## RustFS: credentials, buckets, policy (D19)

- **Credentials** — `rustfs.{accessKey,secretKey}`, or generated once (32
  random chars) and re-read from the existing Secret (`s3.secretName`) on
  every upgrade (Helm `lookup`); `helm template` has no cluster to look up
  and renders fresh random values each time. Leaving them empty, or using a
  value published in this repo, therefore needs `devStack.insecureDefaults:
  true` — the render refuses credentials nobody chose, because these are also
  the S3 credentials every batch Job gets. Read them back with
  `kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.
- **Console** — off by default (`RUSTFS_CONSOLE_ENABLE=false`, no NodePort);
  `rustfs.console.enabled=true` exposes it on `rustfs.console.nodePort`.
- **Buckets** — the `rustfs-init` Helm hook Job (post-install/upgrade,
  `rustfs.init`) creates `s3.bucket`, applies the bucket policy and CORS,
  idempotently.
- **Anonymous read is split** (audit X14): `<pipeline>/<volume>/*` and
  `sources/*` are always anonymous (the browser fetches them directly);
  `status/attempts.json`, `status/validation.json`, `status/volumes.json`
  and `status/failures/*` always need credentials;
  `status/logs/*` is anonymous only while `rustfs.publicLogs=true` (default
  — the campaign browser links run logs; they can carry a tokenised private
  IIIF URL on failure, so set it false behind an authenticated proxy). The
  policy is a single `Allow` with `NotResource` because RustFS applies a
  `Deny` to the root credential too and ignores anonymous-only conditions
  (verified 2026-08-26); `scripts/compose_init.py` renders the same shape
  for the compose stack. Listing stays denied.

## Sizing (O18)

- RustFS: results ≈ 1–3 MB per page (ALTO + PAGE + JPEG + log); 5Gi ≈ 2–4k
  pages. Nothing is ever deleted by the platform — grow the PVC (local-path
  cannot resize; recreate) or prune old pipelines by hand.
- Registry: 60Gi ≈ 5 GPU wrapper images (~10 GB each). No GC of its own:
  delete tags over the API, then run
  `registry garbage-collect /etc/distribution/config.yml` in the pod.

## Changelog

Everything below this line is history: each entry names the objects and
value keys as they were at that version.

### 0.1.1 — 2026-09-04 (B63 Task 28 fix round)

Fixed: `status/warmup/*` dropped from the bucket policy's private-key list
(`htrflow-devstack.bucketPolicy`) and from the paragraph above describing
it. Nothing has ever written that path — a warm-up pod mounts no S3
secret, so its failure reaches the campaign card as a termination message
instead, never a log under `status/` (`docs/how-it-works/
failure-handling.md`). No other behaviour changes.
