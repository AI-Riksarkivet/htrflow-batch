# htrflow-devstack (Helm chart)

**PoC-only.** This chart renders in-cluster stand-ins for infrastructure a
real deployment gets from elsewhere: an S3-compatible object store
(RustFS), a container image registry, the NVIDIA device plugin +
`RuntimeClass`, and a git daemon for a campaigns repo. Every component is
off by default (`rustfs.enabled`, `registry.enabled`,
`nvidiaDevicePlugin.enabled`, `gitDaemon.enabled`) — nothing renders on a
plain `helm install`.

It exists to reproduce the [htrflow-batch](../htrflow-batch) chart's PoC on
a bare k3s node without hand-applied manifests. **Do not install this on a
cluster with real data or real S3 credentials.** None of these objects are
production-shaped: the registry is unauthenticated, the git daemon runs
part-root, RustFS's console (when enabled) has no auth in front of it
beyond the root credential.

## Split from htrflow-batch (0.3.0)

Through htrflow-batch 0.2.0 these objects (`templates/devstack-*.yaml`) lived
in the main chart behind `devStack.*` values. They moved here in 0.3.0 (B63)
because they have nothing to do with what the platform actually runs
(campaigns are Indexed Jobs rendered by `packages/converter` and applied
outside any chart) — bundling PoC scaffolding with the production chart's
values and size budget was the wrong shape. `devStack.allowTagImages` moved
the other way, into htrflow-batch's `security.allowTagImages`, because it
gates htrflow-batch's own control-plane images (`viewer.image`, `api.image`),
not anything here.

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
  pod it creates in that namespace (RustFS, RustFS-init, the git daemon) so
  they still work under that default deny. The registry lives in its own
  `registry` namespace and is unaffected by it.

```bash
helm install htr-devstack charts/htrflow-devstack -n htr-batch --create-namespace \
  --set rustfs.enabled=true --set registry.enabled=true \
  --set nvidiaDevicePlugin.enabled=true --set gitDaemon.enabled=true
```

`make install-devstack` wraps this with the repo-root `.env` constants
(`.env.example` has the PoC defaults). See
[docs/getting-started/deploy.md](../../docs/getting-started/deploy.md) for
the full PoC replay flow (wrapper/viewer/api image builds, warm-up, the
smoke campaign).

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. On a cluster where the
`nvidia` RuntimeClass, the kube-system device-plugin DaemonSet or a
`git-daemon` Deployment/Service were applied by hand, either keep them
outside this chart (`nvidiaDevicePlugin.enabled=false`,
`gitDaemon.enabled=false`) or adopt them once before enabling the
corresponding value:

```bash
kubectl -n kube-system annotate daemonset nvidia-device-plugin \
  meta.helm.sh/release-name=htr-devstack meta.helm.sh/release-namespace=htr-batch --overwrite
kubectl -n kube-system label daemonset nvidia-device-plugin app.kubernetes.io/managed-by=Helm --overwrite
# same two commands for: runtimeclass nvidia (cluster-scoped, no -n);
# -n htr-batch deployment git-daemon + service git-daemon
```

## RustFS: credentials, buckets, policy (D19)

- **Credentials** — `rustfs.{accessKey,secretKey}`, or generated once (32
  random chars) and re-read from the existing Secret (`s3.secretName`) on
  every upgrade (Helm `lookup`); `helm template` has no cluster to look up
  and renders fresh random values each time. Read them back with
  `kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.
- **Console** — off by default (`RUSTFS_CONSOLE_ENABLE=false`, no NodePort);
  `rustfs.console.enabled=true` exposes it on `rustfs.console.nodePort`.
- **Buckets** — the `rustfs-init` Helm hook Job (post-install/upgrade,
  `rustfs.init`) creates `s3.bucket` (and `gitDaemon.bucket`, credentials
  only), applies the bucket policy and CORS, idempotently.
- **Anonymous read is split** (audit X14): `<pipeline>/<volume>/*`,
  `sources/*` and `status/status.json` are always anonymous (the viewer
  fetches them directly); `status/attempts.json`, `status/validation.json`,
  `status/failures/*` and `status/warmup/*` always need credentials;
  `status/logs/*` is anonymous only while `rustfs.publicLogs=true` (default
  — the campaign browser links run logs; they can carry a tokenised private
  IIIF URL on failure, so set it false behind an authenticated proxy). The
  policy is a single `Allow` with `NotResource` because RustFS applies a
  `Deny` to the root credential too and ignores anonymous-only conditions
  (verified 2026-08-26); `scripts/compose_init.py` renders the same shape
  for the compose stack. Listing stays denied.

## git daemon

Historically fed the GitOps reconciler, which polled it over `git://`. The
reconciler is gone as of htrflow-batch 0.3.0 (B63): campaigns are rendered
by `packages/converter` and `kubectl apply`d (or committed to a repo Argo
CD watches), so nothing in the platform talks to this daemon any more. It is
kept here as a convenience for anyone who still wants an in-cluster clone of
a campaigns repo (e.g. to keep image pins registry-local) — its
NetworkPolicy allows any pod in the namespace to reach it on 9418, not a
fixed consumer.

## Sizing (O18)

- RustFS: results ≈ 1–3 MB per page (ALTO + PAGE + JPEG + log); 5Gi ≈ 2–4k
  pages. Nothing is ever deleted by the platform — grow the PVC (local-path
  cannot resize; recreate) or prune old pipelines by hand.
- Registry: 60Gi ≈ 5 GPU wrapper images (~10 GB each). No GC of its own:
  delete tags over the API, then run
  `registry garbage-collect /etc/distribution/config.yml` in the pod.
