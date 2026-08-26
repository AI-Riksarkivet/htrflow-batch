# HANDOFF — work package A3 (chart / ops), 2026-08-26

Branch: this worktree's branch off `652a5eb` (`feat/campaign-browser-visibility`).
Scope touched: `charts/**`, `scripts/**`, `Makefile`, `.docker/docker-compose.yml`,
`.env.example` (new, requested), this file (moved here from the worktree root on request). Nothing else was edited; everything another
package must do is listed here.

Verification run on every commit: `helm lint` (defaults + `ci/full-values.yaml`),
`helm template` on both, `kubeconform -strict -ignore-missing-schemas` on both
(installed via brew during this session), plus runtime checks in docker:
RustFS policy semantics, the rendered `rustfs-init` script (restricted, uid 1000,
read-only rootfs, idempotent), nginx-unprivileged read-only with the rendered
`default.conf` (headers present, `/log` resolves), `registry:3` read-only as
uid 1000, `rustfs` read-only as uid 10001, `alpine/git` + `apk add git-daemon`
under drop-ALL/no-new-privs. A `helm upgrade --dry-run=server` with the live
values (`+ devStack.allowTagImages=true modelCache.create=false`) renders and
keeps the existing `rustfsadmin` credentials through `lookup`.

## 1. Operator decisions before upgrading the live release `htr`

| Decision | Why | What to do |
|---|---|---|
| **Tag images** | `reconciler.image=…:dev11`, `viewer.image=…:dev12` are refused by the digest gate (S3). | `--set devStack.allowTagImages=true` for the PoC loop, or pin digests: `make poc-push` prints them; `kubectl -n htr-batch get pod -l app=uv4-viewer -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'` gives the viewer's. |
| **Model-cache PVC** | `htr-test-data` exists hand-made; `modelCache.create` defaults to true and Helm refuses to import it (dry run confirmed). | Either `--set modelCache.create=false`, or adopt once (cluster mutation, so not done here): `kubectl -n htr-batch annotate pvc htr-test-data meta.helm.sh/release-name=htr meta.helm.sh/release-namespace=htr-batch --overwrite && kubectl -n htr-batch label pvc htr-test-data app.kubernetes.io/managed-by=Helm --overwrite`. Adoption is the better end state (chart owns it, `resource-policy: keep` protects it). |
| **git daemon** | `network.defaultDeny` (on by default) cuts the hand-applied `git-daemon` Deployment off: no ingress from the reconciler, no egress for the seed clone. | Preferred: delete the hand-applied `deploy/git-daemon` + `svc/git-daemon` and `--set devStack.gitDaemon.enabled=true` (same names, same seed URL `http://rustfs.htr-batch.svc.cluster.local:9000/git-repos/campaigns-local.git`, same `rollout restart` re-seed flow); the chart adds the reconciler egress rule itself, so drop `network.reconciler.extraEgress` from the live values. Alternative: adopt them (annotate/label as above). Last resort: `--set network.defaultDeny=false`. |
| **RuntimeClass + device plugin** | Hand-applied `runtimeclass/nvidia` and `kube-system/daemonset/nvidia-device-plugin` (O14). `devStack.nvidiaDevicePlugin.enabled` renders the same objects (image now digest-pinned to the running `v0.19.3`). | Leave disabled (current) or adopt both with the annotate/label commands, then enable. |
| **Registry uid** | `devStack.registry.runAsUser` defaults to 1000 with a read-only rootfs; the live `registry-data` PVC was written by root. | Before the upgrade: `chown -R 1000:1000` from a throwaway pod on `registry-data` (local-path ignores fsGroup), or `--set devStack.registry.runAsUser=0` to keep root. |
| **RustFS console** | Off by default now (`RUSTFS_CONSOLE_ENABLE=false`, NodePort 30901 gone). | `--set devStack.rustfs.console.enabled=true` if the console is still wanted. |
| **Public run logs** | `devStack.rustfs.publicLogs=true` keeps `status/logs/*` anonymous (campaign browser links them); logs can carry a tokenised IIIF URL on failure (X14). | Keep for the PoC; set false once the log viewer sits behind auth. |
| **Reconciler egress** | Default narrowed to GitHub's four aggregate `git` ranges; the live repo is in-cluster via git://. | Nothing for the PoC (`egressCidrs: []` would be tighter still). For a GitHub-hosted campaigns repo check `https://api.github.com/meta` "git" if clones fail. |
| **PSA level** | `security.psaEnforce` defaults to `baseline` because the git daemon runs as root. | After a purpose-built git-daemon image (below), set `restricted` and re-run `make psa-labels`. |
| **Upgrade flag** | Values tree changed shape (`devStack.rustfs.console.*`, removed `image.*`/`s3.endpoint`). | `helm upgrade … --reset-then-reuse-values` (the chart now fails on a nil `network`). The live values contain no removed keys, so they pass the schema as-is. |
| **rustfs-init hook** | Post-install/upgrade Job re-applies bucket policy + CORS (idempotent, verified). It changes the live policy from "everything anonymous" to the split. | Nothing; run logs stay public by default. Frontend/reconciler only ever read the public keys anonymously (`status.json`), the reconciler uses credentials. |

Suggested live upgrade (after the PVC adoption and git-daemon replacement):

```bash
helm upgrade htr charts/htrflow-batch -n htr-batch --reset-then-reuse-values \
  --set devStack.allowTagImages=true --set devStack.gitDaemon.enabled=true \
  --set network.reconciler.extraEgress=null
make psa-labels
```

## 2. Env names as rendered on the reconciler CronJob

Exactly the contract table in the plan; A1 must read these (pydantic
`Settings`, `RECONCILER_` prefix, all strings):

| Env | Rendered from | Default value |
|---|---|---|
| `RECONCILER_TICK_SECONDS` | `reconciler.tickSeconds` | `300` |
| `RECONCILER_DATA_PVC` | `modelCache.name` | `htr-test-data` |
| `RECONCILER_ALLOWED_IMAGE_REPOS` | `security.allowedImageRepos` joined with `,` | `""` |
| `RECONCILER_REQUIRE_MODEL_REVISION` | `security.requireModelRevision` | `false` (literal `"true"`/`"false"`) |
| `RECONCILER_JOB_MIN_DEADLINE_SECONDS` | `job.minDeadlineSeconds` | `21600` |
| `RECONCILER_JOB_SECONDS_PER_PAGE` | `job.secondsPerPage` | `30` |
| `RECONCILER_JOB_RUNTIME_CLASS` | `job.runtimeClassName` | `nvidia` (`""` = none) |
| `RECONCILER_JOB_NODE_SELECTOR` | `job.nodeSelector` as JSON | `{}` |
| `RECONCILER_JOB_TOLERATIONS` | `job.tolerations` as JSON | `[]` |
| `RECONCILER_JOB_MANIFEST_MAX_BYTES` | `job.manifestMaxBytes` | `16777216` (→ Job env `MANIFEST_MAX_BYTES`) |
| `RECONCILER_JOB_FETCH_MAX_BYTES` | `job.fetchMaxBytes` | `67108864` (→ Job env `FETCH_MAX_BYTES`) |
| `RECONCILER_MAX_VALIDATIONS_PER_TICK` | `reconciler.maxValidationsPerTick` | `50` |
| `RECONCILER_FETCH_MAX_BYTES` | `reconciler.fetchMaxBytes` | `16777216` |
| `RECONCILER_LEASE_NAME` | fixed | `htr-reconciler` (Role grants get/update on that name, create unscoped) |

Unchanged: `RECONCILER_NAMESPACE` (downward API), `RECONCILER_QUEUE`,
`RECONCILER_S3_SECRET`, `RECONCILER_WINDOW`, `RECONCILER_ATTEMPT_CAP`,
`CAMPAIGNS_REPO_URL`, `CAMPAIGNS_REPO_WEB_URL`, `PUBLIC_RESULTS_BASE`,
`S3_ENDPOINT`, `S3_BUCKET`, `AWS_SHARED_CREDENTIALS_FILE`, `HOME`.

CronJob: `startingDeadlineSeconds: 120`, `activeDeadlineSeconds: 600`
(`reconciler.tickDeadlineSeconds`). Job contract mirrored in
`templates/job-example.yaml`: `backoffLimit: 0`, `podFailurePolicy`
(exactly two rules: Ignore on `DisruptionTarget`, FailJob on wrapper exit 13 —
exit 143/SIGTERM matches neither and stays transient), `MANIFEST_MAX_BYTES` /
`FETCH_MAX_BYTES` env from `job.*`, `runtimeClassName` / `nodeSelector` /
`tolerations` from `job.*`, PVC from `modelCache.name`.
Default `queue.resources` = cpu 4 / memory 8Gi / nvidia.com/gpu 1 (one Job).

## 3. For A1 (reconciler)

- Read the env above. `RECONCILER_JOB_RUNTIME_CLASS=""` must mean "no
  runtimeClassName".
- Lease: name `htr-reconciler` in the release namespace; RBAC is
  `create` (unscoped) + `get`,`update` scoped by `resourceNames`. No `delete`,
  no `patch` — use update.
- The chart's warm-up NetworkPolicy and Job policy select `app=htrflow-warmup`
  / `app=htrflow-batch` as before; the new default-deny does not change what
  Jobs can reach.

## 4. For A2 (wrapper / dockerfiles) — out of my scope

- `.docker/git-daemon.dockerfile` (proper fix for the one non-restricted pod):
  Debian/Alpine base with `git-daemon` installed at build time, `USER 1000`,
  so `devStack.gitDaemon` can drop root, get `readOnlyRootFilesystem`, lose
  the Alpine-CDN egress rule, and `security.psaEnforce` can become
  `restricted`. Then change `devStack.gitDaemon.image` default and simplify
  `templates/devstack-gitdaemon.yaml` (remove the `apk add`, add
  restrictedPod/restrictedContainer includes) and the git-daemon egress in
  `templates/network.yaml`.
- The wrapper image is now tag-independent for the chart (`exampleJob.image`
  is not digest-gated because per-volume Jobs pin digests in the pipeline
  YAML); nothing to do.

## 5. For B1 (CI) — out of my scope

- Add a chart step to `.dagger/checks.go`: `helm lint` on defaults AND
  `-f ci/full-values.yaml`, `helm template` on both, `kubeconform -strict
  -ignore-missing-schemas`. `make helm-template` is the local equivalent.
- `.gitignore` needs `.env` (root); `.env.example` is committed. (I did not
  touch `.gitignore`.)
- `renovate`/pins: the chart now carries digests for `rustfs/rustfs`,
  `registry`, `alpine/git`, `amazon/aws-cli`, `nvcr.io/nvidia/k8s-device-plugin`
  in `values.yaml`/`devstack-rustfs.yaml` — good Renovate targets.
- Trivy for the reconciler lives in `make scan-reconciler`
  (`aquasec/trivy:0.65.0`, HIGH/CRITICAL, `--ignore-unfixed`).

## 6. For B2 (docs)

Docs that now describe the old chart and need updating:

- `docs/reference/chart.md`: drop `image.*` and `s3.endpoint` rows; add
  `modelCache.*`, `job.*`, `security.*`, `network.defaultDeny`,
  `network.viewer.ingressCidrs`, `viewer.securityHeaders`,
  `devStack.allowTagImages`, `devStack.rustfs.{accessKey,secretKey,console,
  storage,init,publicLogs}`, `devStack.registry.{image,runAsUser,storage}`,
  `devStack.nvidiaDevicePlugin.image`, `devStack.gitDaemon.*`,
  `reconciler.{tickSeconds,tickDeadlineSeconds,maxValidationsPerTick,
  fetchMaxBytes}`; digest requirement; `--reset-then-reuse-values`.
- `docs/getting-started/deploy.md` and `README.md`: remove
  `--set image.repository/tag` and `--set s3.endpoint`; the PVC is rendered
  now; bucket policy/CORS is the `rustfs-init` hook (no manual aws-cli pod);
  RustFS creds are generated (how to read them back is in the chart README).
- `docs/development/security.md`: RustFS creds are no longer `rustfsadmin`
  by default; viewer/RustFS/registry are restricted-clean; default-deny
  policy table (viewer, rustfs, rustfs-init, git-daemon rows); the git daemon
  is the one root pod and why; `psaEnforce` value; Kyverno option.
- `docs/reference/reconciler.md`: env table gains the section-2 rows;
  `RECONCILER_S3_SECRET` description says "via envFrom" — it is a mounted
  file (D-H1, already flagged).
- `docs/development/deployment.md`: `poc-push` is arm64-aware, stamps
  `HTRFLOW_BASE_REVISION` from the `HTRFLOW_DIR` checkout (`.env`, default
  `~/htrflow`) into the arm64 wrapper image and prints digests;
  `.env`/`.env.example`; `build-reconciler`/`scan-reconciler`; `psa-labels`
  reads `security.psaEnforce`.
- Chart README already carries the 0.2.0 changelog, the replay commands with
  the new flags, the adoption commands and the bucket-policy split.

## 7. Deliberately not done (with reasons)

- **Two S3 principals** (X8 fix suggestion: Job creds scoped to their prefix,
  reconciler to `status/*`): needs RustFS IAM users/policies created at init
  and a second Secret consumed by `jobspec.py` (A1) — a cross-package change
  beyond the numbered list; the split bucket policy covers the anonymous side.
- **Registry behind auth**: Harbor already runs on the cluster; the devStack
  registry stays unauthenticated by design and is documented as such. The
  digest gate is the compensating control in the chart.
- **`.gitignore` `.env` entry**: outside my file scope (section 5).
- **git-daemon as non-root**: impossible with `alpine/git` (no git-daemon in
  the image; `apk add` needs root + writable rootfs — verified). Section 4.
- **Kyverno policy not exercised against a Kyverno install** (none on the
  cluster); it renders and passes kubeconform with the schema skipped.
- **`helm upgrade` on the live cluster**: not run (no cluster mutation);
  `--dry-run=server` was.
