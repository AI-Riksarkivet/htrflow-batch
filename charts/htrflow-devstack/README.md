# htrflow-devstack (Helm chart)

**Development only.** In-cluster stand-ins for what a real deployment gets
from elsewhere: an S3-compatible object store (RustFS), a container image
registry, and the NVIDIA device plugin with its `RuntimeClass`. Every
component is off by default (`rustfs.enabled`, `registry.enabled`,
`nvidiaDevicePlugin.enabled`); a plain `helm install` renders nothing. Do
not install it next to real data or real S3 credentials: the registry is
unauthenticated, and the RustFS console, when enabled, has nothing in front
of it but the root credential.

## Installing

Install into the **same namespace** as the htrflow-batch release. The two
charts share no values; they wire together by name:

- `s3.secretName` must equal htrflow-batch's `s3.existingSecret` (both
  default to `htr-batch-s3`). The bucket is `s3.bucket`, written into that
  Secret as `S3_BUCKET`, which is where every pod reads it.
- htrflow-batch's namespace default deny applies to these pods too; this
  chart renders its own allows for RustFS and its init Job. The registry
  lives in its own `registry` namespace.

```bash
helm install htr-devstack charts/htrflow-devstack -n <namespace> --create-namespace \
  --set rustfs.enabled=true --set registry.enabled=true \
  --set nvidiaDevicePlugin.enabled=true \
  --set devStack.insecureDefaults=true   # accept generated RustFS credentials
```

`make install-devstack` runs this with the repo-root `.env` constants, and
refuses `NVIDIA_DEVICE_PLUGIN=false` while a GPU pod is running: disabling
the plugin deletes the `nvidia` RuntimeClass and the DaemonSet every GPU
pod depends on (`FORCE=1` skips the check). See
[Dev cluster](../../docs/development/dev-cluster.md) for the whole loop.

## RustFS: credentials, buckets, login

- **Root credentials** are `rustfs.accessKey` / `secretKey`, or generated
  once (32 random characters) and read back from the Secret on every upgrade
  (Helm `lookup`). They are also the S3 credentials every campaign pod gets,
  so the render refuses a value this repository publishes outright, and
  refuses empty keys unless `devStack.insecureDefaults: true` says the stack
  is a toy (`helm template` has no cluster to look up and renders fresh
  random values each time). Read them back with
  `kubectl -n <namespace> get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.
- **Buckets.** The `rustfs-init` hook (post-install and post-upgrade,
  `rustfs.init`) creates `s3.bucket` and, when set, `s3.imageCacheBucket`
  for the converter's `image_cache.bucket`. Both stay private: no bucket
  policy, no CORS, and the hook removes any an earlier version left.
- **The login user.** The hook also creates the policy `htr-read`
  (`s3:GetObject` on `s3.bucket/*` only) and the RustFS user `s3.loginUser`
  (default `htr-reader`) with it attached. Its password lives in the Secret
  `s3.loginSecret` (default `htr-results-login`, key `password`): generated
  once, kept on uninstall, read back on upgrade, and set on RustFS again by
  every run of the hook. Install htrflow-batch with
  `results.keyDerivation=none` and log in as that user with
  `kubectl -n <namespace> get secret htr-results-login -o jsonpath='{.data.password}' | base64 -d`.
- **Console** off by default; `rustfs.console.enabled=true` exposes it on
  `rustfs.console.nodePort`.
- **The hook's image** is RustFS's own client, `rustfs/rc`, pinned by
  digest. With htrflow-batch's policies on, the `rustfs/` entry that admits
  RustFS in `security.allowedImageRepos` admits it too.

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. Where the `nvidia`
RuntimeClass or the device-plugin DaemonSet were applied by hand, keep them
outside the chart (`nvidiaDevicePlugin.enabled=false`) or adopt them once:

```bash
kubectl -n kube-system annotate daemonset nvidia-device-plugin \
  meta.helm.sh/release-name=htr-devstack meta.helm.sh/release-namespace=<namespace> --overwrite
kubectl -n kube-system label daemonset nvidia-device-plugin app.kubernetes.io/managed-by=Helm --overwrite
# the same two commands for: runtimeclass nvidia (cluster-scoped, no -n)
```

## Sizing

- RustFS: results are about 1 to 3 MB per page (ALTO, PAGE, JPEG and log);
  5Gi holds a few thousand pages. Nothing is ever deleted by the platform:
  grow the PVC (local-path cannot resize; recreate) or prune old pipelines
  by hand.
- Registry: 60Gi holds about five GPU wrapper images. It has no GC of its
  own: delete tags over the API, then run
  `registry garbage-collect /etc/distribution/config.yml` in the pod.

## Changelog

### 0.6.0 — 2026-10-20

Changed: a `rustfs.accessKey` / `secretKey` this repository publishes is
refused whatever `devStack.insecureDefaults` says; the flag now accepts the
generated (empty) case only, which is what `make install-devstack` sets.
`ci/full-values.yaml` sets no credentials. The packaged chart no longer
carries `ci/`. This README is the short version; older notes are in its
history.

### 0.5.0 — 2026-10-01

The results bucket is private and a read-only login user is created for the
results proxy; `rustfs.publicLogs` and `rustfs.init.corsOrigins` are gone.

Older entries: `git log -p -- charts/htrflow-devstack/README.md`.
