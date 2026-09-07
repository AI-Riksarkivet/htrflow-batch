# Repository audit — 2026-09-07 (after B63)

## 0. Scope

**Code:** `htrflow-batch` at `main`, commit `b515bce` (worktree
`~/htrflow-batch/.worktrees/audit`), i.e. after B63 "campaigns as Indexed Jobs"
and its Tasks 1–12: no `packages/reconciler`, no `status.json`, no CRD and no
controller — campaigns are rendered Kubernetes Indexed Jobs, applied from git.

**Live evidence:** k3s v1.35.5+k3s1 on the GB10 (arm64), namespace `htr-batch`:
Helm release `htr` = `htrflow-batch-0.6.0` revision 35 (2026-09-04) and
`htr-devstack` = `htrflow-devstack-0.2.0` revision 6; Kueue v0.18.1; Kyverno
v1.19.0 with `images-allowed` and `images-pinned` live in `Enforce`
(`verify-images` and `model-revision` are not rendered there:
`verifyImages` off, `requireModelRevision` false).

**Method:** eight independent read-only reviews, one per angle — A converter,
B wrapper, C read API + frontend, D Kubernetes/Helm/Kueue, E security and
supply chain, F CI/tests/reproducibility, G documentation and spec drift,
H scale/cost/operations. Each reviewer had to verify every finding in code or
by probe (throwaway renders, `TestClient`, `helm template`, `kubectl apply
--dry-run=server` against the PoC, `uv lock --check`, `make ci`) before
reporting it. Findings were then cross-checked, deduplicated and ranked here;
where two angles disagreed the code was read again and the disagreement is
resolved in the finding text. Nothing was changed in the repo or the cluster.

**Baseline at audit time:** tests — wrapper 210 passed / 1 skipped, converter
160, web 100, frontend 153 (vitest); `make ci` 469 passed / 2 skipped in 26.6 s.
`scripts/loc-budget.sh` — wrapper 2117, converter 1283, web 667, frontend 3100,
chart 738: **every one of the five budgets is exactly at its cap**.
`uv lock --check` fresh, `bun install --frozen-lockfile` clean, strict docs
build (`zensical build --clean --strict`) exit 0 with no warnings.

**Previous audit:** [2026-08-26](2026-08-26-repo-audit.md) (same format, so the
two are comparable); docs pass: [2026-09-04](2026-09-04-docs-audit.md).
Stories that already exist for known gaps are cited, never re-proposed.

**Counts by severity** (after dedup across angles): critical **2** · high **17**
· medium **20** · low **20** — 59 distinct findings from 67 reported, 8 pairs or
triples merged.

---

## 1. Verdict

The B63 system is sound in its shape and, at the scale it has actually been run
(tens of volumes, one GPU), in its behaviour: the Indexed Job carries retries,
progress and pause natively, Kueue admits one Workload per campaign, the
converter's parse/render/prune/append-only semantics hold under adversarial
input, the wrapper's stage/classification/redaction machinery is careful and
well tested, and the supply chain — digest pinning, native arm64 builds,
keyless cosign, SLSA provenance, Trivy gates — is the strongest part of the
repo. What the audit found is that **three of the design's load-bearing
assumptions are not yet enforced in code**: (1) *"10 000 volumes fit in a
ConfigMap"* — the split counts volumes and never measures bytes, so an
`images:` campaign of 45 volumes already renders an un-appliable ConfigMap that
CI commits to git (X1); (2) *"the wrapper's memory is independent of volume
size"* — page outputs are never deleted from the memory-backed workdir and
htrflow's progress registry retains every `Document`, so a long volume ends in
an OOMKill whose disruption is swallowed by the `Ignore` rule and retried
uncapped (X2); (3) *"the warm-up either worked or failed"* — the marker write is
best-effort while the batch pods gate on it in an unbounded `until [ -f … ]`
loop holding `nvidia.com/gpu: 1`, so a silent marker failure costs 4 × 6 h of
GPU per index with no log line (X3, X4). Around those sit a set of gates that
exist but do not run: `rendered/` is applied verbatim yet only two of its
subdirectories are ever regenerated (X9), no policy constrains anything but the
image reference (X10), every supply-chain control ships off by default (X11),
the Kyverno policies are executed only in a downstream repo (X21), and CI's
strongest checks are post-merge or hand-dispatched (X14, X15, X16). The
read API is correct but sized for the PoC: it lists every Pod a campaign ever
made on every poll and turns one non-integer label into a bare 500 with no
security headers (X12, X13) — both of which matter the moment B12 makes the
service reachable. **Top risks:** the two criticals, the warm-up GPU hold, the
un-audited `rendered/` tree as a code-execution path, and defaults that enforce
nothing.

---

## 2. Cross-cutting findings, ranked

Each item names the angles that found it independently; the bracketed ids are
the per-angle appendix. `file:line` as at `b515bce`.

### X1 — CRITICAL · The campaign split counts volumes and never measures bytes
*Converter A1 · Kubernetes D5 · Scale H1*

`render.split` cuts at `MAX_VOLUMES_PER_JOB = 10_000` (`render.py:17,39-42`) and
the whole list goes into one ConfigMap key (`render.py:112`). The design
justifies the number with "ConfigMaps ≤ 1 MiB"
(`specs/2026-09-01-indexed-jobs-design.md:32`); nothing measures bytes. The three
angles probed different line shapes and their numbers are consistent, not
contradictory: a bare `arkis` reference is ~63 B (630 000 B at 10 000 volumes,
safe); a two-URL `images:` volume is 151–198 B (1.51–1.98 MB, over the limit); an
`images:` volume of 300 pages with 90-character URLs is **23 115 B on one line**
(`models.py:133-137`), so **45 such volumes exceed 1 MiB** and 200 render a
4.41 MiB ConfigMap in a single part. `validate` and `render` both exit 0, CI
commits the file, and only the apply fails — `[]: Too long: may not be more than
1048576 bytes` (server dry-run) — leaving an uncreatable campaign in git and, when
it is a `-part1`, blocking the whole campaign.
**Fix:** split on cumulative `len(v.source_line())` (~900 KiB) as well as count.
**Story:** new **B72** (with X8 and X35, same code path).

### X2 — CRITICAL · The wrapper's memory grows linearly with pages, against its own budget
*Wrapper B-1 (critical), B-3 (medium)*

`stream.consume`'s `finally` unlinks only `item.path`, the downloaded image
(`stream.py:196-202`), never `files["alto"]`/`files["page"]`, and
`main.py:198-200` deliberately dropped workdir cleanup. The workdir is
`emptyDir {medium: Memory, sizeLimit: 2Gi}` (`manifests/campaign-job.yaml:161-164`)
— accounted memory. Probe: 20 pages at 200 KB/format left 0 images and **40 output
files / 8 000 280 B**; at ~300 KB/page that reaches 2 Gi near 7 000 pages, inside
the 6 h deadline. Compounding it, htrflow's module-level `progress` registries
(`~/htrflow/src/htrflow/progress.py:19-21`) key on `Document`, which has no
`__hash__`/`__eq__` and is never popped, while the wrapper runs one long-lived
`Pipeline` over a whole volume in one process (`main.py:106-121`,
`driver.py:540-544`) — a shape htrflow's per-invocation CLI never exercises:
~0.5 GB at 10 000 pages on top of 6–8 Gi of models against a 16 Gi limit.
`docs/how-it-works/memory-budget.md:6,13` claims the budget is "independent of
volume size" with outputs as "noise". End state is OOMKill (exit 137, no
termination message, no final log ship, viewer polls forever) or an emptyDir
eviction that carries `DisruptionTarget` and is swallowed by the `Ignore` rule
(`campaign-job.yaml:20-23`) — the attempt goes uncounted and the index retries
uncapped, holding a GPU each time.
**Fix:** unlink each `files[fmt]` after a successful `upload()`
(`publish.alto_dims` already falls back to `store.get_bytes`,
`publish.py:279-285`); drop the document from htrflow's registries in
`driver.process_page`, inside one `try/except Exception`.
**Story:** new **B73**.

### High-severity findings (17)

**X3 · The warm-up gate is unbounded and holds the GPU while it waits**
(*B-2, D3, H2*). `render.py:190-196` renders
`until [ -f /data/warmup/<id>.done ]; do sleep 10; done` into the `warmup-wait`
init container (`campaign-job.yaml:169-196`), and the pod reserves
`nvidia.com/gpu: 1` (`:142-150`) for its whole lifetime, init included — Kueue
holds the quota through it. If the marker never appears (X4, or a failed warm-up)
each index waits `activeDeadlineSeconds` (21 600 s), exits 143 and is retried
three times: **4 × 6 h of held GPU per index, no work, no signal**.
`failure-handling.md:213` admits a campaign can get "stuck at its init container"
but names neither the cost nor a command. Fix: bound the wait (~900 s), exit 13,
and a `podFailurePolicy` rule on `containerName: warmup-wait → FailIndex`;
surface "waiting for warm-up" as a lifecycle stage (**C13**). → new **B74**.

**X4 · A warm-up that could not write its marker still exits 0, and a deadline
kill says nothing** (*B-2, B-5*). `_write_marker` (`warmup.py:118-134`) returns
silently when `PIPELINE_ID` or `HF_HOME` is missing and only `log.warning`s on
`OSError`, yet `main` returns `EXIT_OK` (`warmup.py:114-115`) — a "successful"
warm-up with no marker, which is exactly the state X3 turns into 18 GPU-hours.
There is no warm-up log (the Job mounts no S3 secret), so nothing says why.
Separately `warmup-job.yaml:23` sets a Job-level `activeDeadlineSeconds: 3600`
(terminal, no retry) and `warmup.main` installs no SIGTERM handler, unlike
`main.py:136-139`: a slow first download fails with an empty termination message —
the hole `_fail` (`warmup.py:95-108`) exists to close. Fix: write the marker
before the success log and route its failure through `_fail` (permanent → 13 →
`FailJob`); install the same SIGTERM handler. → new **B75**.

**X5 · The warm-up Job gets no `runtimeClassName`, `nodeSelector` or
`tolerations`** (*D2*). `render.py:198-207` puts all three on the campaign Job;
`_warmup_job` (`render.py:83-99`) sets none — verified: it touches only name,
namespace, labels, image, `PIPELINE_ID`, the pipeline ConfigMap and the PVC. Where
GPU nodes are labelled or tainted (the production target) the warm-up lands
elsewhere, fills a *different* `ReadWriteOnce` model-cache PV, and the marker never
appears on the node that needs it — X3 again, on a correctly configured cluster.
Fix: the same three fields from the same `cfg`. → **B74**, with X3 (one story:
a warm-up that did not work never becomes a silent GPU hold).

**X6 · A TTL-reaped campaign Job is recreated by the next apply and every index
re-runs** (*D1*; root shared with X19). `campaign-job.yaml:30` hard-codes
`ttlSecondsAfterFinished: 86400` — not a value, not per pipeline. `cli.py:237-242`
applies **every** rendered campaign on every run, and an apply-patch of a missing
object *creates* it (`apply-rbac.yaml:11-14`). Twenty-four hours after a campaign
finishes its Job is gone; the next apply — any commit, or Argo CD self-heal —
recreates it and re-runs all indexes. Nothing tells an operator to delete a
finished campaign file (`template/README.md:44-51` covers cancelling only). This
is X2 of the August audit in a new shape. Fix: TTL as a `converter.yaml` value
with a long default; document deleting the file. → new **B76**.

**X7 · Pipelines are mutable in place, and the apply then dies mid-loop on an
immutable field** (*A3, D6, A8*). Campaigns get an immutability guard
(`cli.py:109,158-166`); pipelines get none — `campaigns.md:163` calls pipeline-id
immutability "enforced by review". Probe: editing `pipelines/demo-v1.yaml` after a
render re-renders `htr-pipeline-demo-v1` with a new `pipeline-sha256`, exit 0. If
only the ConfigMap changed, the warm-up Job manifest is byte-identical, the apply
is a no-op, the warm-up never re-runs and `/data/warmup/demo-v1.done` stays —
under `HF_HUB_OFFLINE=1` the new model is simply absent. If `image:` or
`max_seconds:` changed, the warm-up Job and every campaign Job's pod template
change and both are immutable → 422 at apply; `cli.py:230-255` wraps the whole
loop in one `try`, so the first failure blocks every later campaign. Either way
indexes not yet started mount a different recipe from finished ones, under one
pipeline id and one S3 prefix. `ClusterError` has no case for a 422
immutable-field either (`cluster.py:50-72` covers 401/403 and a missing Kueue
CRD), so the commonest failure of this design prints raw webhook prose. Fix:
refuse a changed `pipeline-sha256` against `rendered/pipelines/<id>.yaml`; put the
sha in the warm-up marker path; report apply errors per object; one sentence for
422. → new **B77**.

**X8 · A split campaign's Job name can exceed 63 characters** (*A2*).
`models.py:36` allows a 63-character name and `render.py:136,216` appends
`-partN`. The Job's own name is a DNS-1123 subdomain and would be fine, but the
Job controller copies it into `batch.kubernetes.io/job-name`, a label **value**:
server dry-run of 58 chars + `-part1` returns `spec.template.labels: … must be no
more than 63 bytes` and `metadata.name: … will not able to create pod with invalid
DNS label`. Any campaign file over 57 characters that splits is un-appliable — and
`tests/test_render.py:198-208` asserts that exact name is fine; the comment at
`render.py:130-133` reasons about the subdomain limit and misses the label. Fix:
cap the name at 63 − len(`-partN`) when splitting. → **B72**.

**X9 · `rendered/` is applied verbatim, but only two subdirectories are ever
regenerated** (*E1, A4*). `cli.py:173-174` prunes `rendered/pipelines` and
`rendered/campaigns` only, and non-recursively, while Argo CD is the documented
applier (`examples/campaigns/README.md:116-118`, `campaigns.md:112-114`: "nothing
applies to the cluster outside of what CI committed"). Probe: `rendered/evil.yaml`
and `rendered/extra/evil.yaml` both survived a re-render, and
`render.yml:135-142` only `git add rendered`. The pull-request half is blind the
same way: `render.yml:96` renders into `$RUNNER_TEMP`, so `_existing_campaign_text`
(`cli.py:109`) finds nothing and the append-only guard never fires in a PR — it
fires only post-merge (`:118-134`), producing a red workflow, no commit, and a
`rendered/` tree still describing an older `campaigns/` that Argo CD keeps
applying. Write access to the campaigns repo — or a PR touching only `rendered/`,
which the Policy job never reads because it checks a *fresh* render — therefore
puts an arbitrary manifest into the namespace. Fix: prune `rendered/` recursively
and fail on any file this render did not write; in PR CI render into the
checked-out `rendered/` and `git diff --exit-code`; run Kyverno over the committed
tree too. → new **B78**.

**X10 · No policy constrains anything but the image reference** (*E2*). All four
ClusterPolicies (`charts/htrflow-batch/templates/policies/*`) inspect only `image`
and the pipeline ConfigMap — never `command`, `args`, `env`, `volumes` or
`initContainers`. The S3 Secret is mountable by any pod in the namespace
(`campaign-job.yaml:165-168`), the wrapper image has a shell
(`.docker/htrflow-batch.dockerfile:124-137`), and the label `app: htrflow-warmup`
grants public egress on 443 (`network.yaml:105-121`). A Job on the *approved,
signed, digest-pinned* wrapper image, labelled `app: htrflow-warmup`, mounting
`htr-batch-s3` and running `sh -c 'curl … </secrets/s3/credentials'` passes every
gate — reachable through X9. Fix: a policy pinning the pod shape (allowed
`command`, no Secret mount outside the wrapper container, the egress label only on
rendered warm-ups). → new **B79**; the CEL rewrite stays **B69**, the allow-list
split **B68**.

**X11 · Every supply-chain control ships off** (*E3, E10*).
`values.yaml:95,99,108,115,127`: `allowedImageRepos: []`,
`requireModelRevision: false`, `policies.enabled: false`, `psaEnforce: baseline`,
`verifyImages.enabled: false`; PSA labels are an out-of-band Makefile step
(`Makefile:316-321`, `deploy.md:78`), so a Helm-only install has no PSA at all.
The default install enforces nothing the repo built. Same family:
`network.yaml:40`'s `except` list omits `169.254.0.0/16`, so the
`iiifCidrs: ["0.0.0.0/0"]` catch-all leaves link-local addresses reachable from
batch pods. Fix: a `prod-values.yaml` profile `deploy.md` starts from; `fail` in
`htrflow-batch.validate` on `policies.enabled: false` without an explicit opt-out;
render PSA labels when the chart owns the namespace. → new **B80**.

**X12 · An unhandled exception is a bare 500 with none of the security headers**
(*C-2, H7*). `parse_index_ranges` (`projection.py:20-36`) and
`_pod_completion_index` (`:203`) call `int()` on label and status strings with no
guard. Probe: a Pod carrying `job-completion-index: NaN` under the Job's
`job-name` label makes `GET /api/v1/jobs/ns/j` return `500 Internal Server Error`
as plain text; Kubernetes also caps and *truncates*
`completedIndexes`/`failedIndexes` at scale, which crashes the whole page the same
way, and an apiserver failure does it too (`_read` re-raises every non-404
`ApiException`, `kube.py:76-79`). Worse, the response then carries **none** of
`SECURITY_HEADERS`: the middleware at `app.py:92-96` assigns
`response = await call_next(request)` and never reaches `response.headers.update`
when the call raises (probe: `headers: {}`) — no `nosniff`, no `Referrer-Policy`,
no CSP. The reader is told "Can't reach the campaign service right now (HTTP 500)"
for a data bug. Fix: an exception handler returning a one-sentence 502/503 *with*
`SECURITY_HEADERS`; tolerate a non-integer index and a truncated range. → new
**C15**.

**X13 · The detail route lists every Pod a campaign ever made, on every poll**
(*C-1, H6*). `kube.py:112-119` lists pods by `batch.kubernetes.io/job-name` with
no `field_selector`, no `limit` and no continue-token, decoding every full Pod
object into memory. Pods are retained (`restartPolicy: Never`,
`backoffLimitPerIndex: 3`, `ttlSecondsAfterFinished: 86400`), so a 10 000-volume
campaign leaves 10 000–40 000 Pods alive for its whole run plus 24 h. `app.py:132`
calls this on **every** `GET /api/v1/jobs/{ns}/{name}`, plus the whole campaign
ConfigMap (630 KB at 10 000) and a fresh warm-up list (`app.py:133`), and
`CampaignCard.svelte:146-150` polls it every `RELOAD_MS` per open card. The
projection itself is fast (probe: 10 000 rows in 0.03 s, 84 KB out) — the apiserver
round-trips are the cost, and they are uncached (X38). Fix: `field_selector` +
paging, pods only for failed/active indexes, a short-TTL cache behind `Reader`.
→ new **C16**; the O(volumes) projection folds into **C08**.

**X14 · Every gate is post-merge; branch CI is opt-in** (*F-2*). `ci.yml:2-6`
triggers on `push: branches: [main]`, `pull_request` and `workflow_dispatch`.
`gh pr list --state all` returns one PR (a closed dependabot bump): the
`pull_request` trigger has never gated a merge, the last merge commit is `32e550c`
(2026-08-27), and work lands as direct pushes to main with hand-dispatched branch
runs. Run `34108845568` (push to main) failed at "Checks (ruff, ty, frontend,
chart)" after 44 s and main stayed red until `96ffc89`. Fix: `push:` on all
branches (scans and the arm64 build already gate on `event_name`), or branch
protection so the existing `pull_request` trigger becomes the gate. → new **B81**.

**X15 · `make ci` is not what CI runs, and all five budgets are exactly full**
(*F-1, G1, F-7*). `Makefile:75-78` runs `typecheck` + `dagger call checks` +
`dagger call test`; `ci.yml:48` adds a third gate, `scripts/loc-budget.sh`, that no
make target runs. Probe: wrapper 2117/2117, converter 1283/1283, web 667/667,
frontend 3100/3100, chart 738/738 — **zero headroom**, so one added comment line
fails the build and only a post-merge run (X14) says so; every fix in this audit
needs a matching removal. Three documents quote three different budget sets and
none matches the script (spec `:14-21`, plan `:15`, B63 `:63-65`), and the script
counts blank lines and comments (`loc-budget.sh:5`) while omitting `.dagger/*.go`
(867), `scripts/*.py` (333), the devstack chart templates (557) and the frontend's
css/html (204). Fix: append the script to `ci:` and give every budget a margin in
the same commit. → new **B82** (the documents' own drift is **B85**).

**X16 · The wrapper↔htrflow contract test never runs automatically** (*F-3*).
`.dagger/test.go:38` `TestDriver` runs `test_driver_real.py` — the real
`Pipeline.from_config`/`Export`/`auto_import` pin — inside the wrapper image, and
no workflow mentions `test-driver`, while the upstream pin `HTRFLOW_ARM64_BASE_REF`
(`ci.yml:15`, `publish.yml:48`) is renovate-tracked (`renovate.json:81-89`): a bot
can bump htrflow's commit with nothing exercising its API. `scan-wrapper` already
builds that image on push to main (`ci.yml:87-95`). Fix: one
`dagger call test-driver` step there. → **B25**.

**X17 · One campaign owns the queue to the end, and `priority:` is decorative**
(*H3*). Kueue admits a Job's Workload for the Job's whole life, so a 10 000-index
campaign holds the single-GPU quota for weeks. `kueue.yaml` renders no
`WorkloadPriorityClass` and no preemption; live, `withinClusterQueue: Never` and
`kubectl get workloadpriorityclass` returns none — yet `render.py:142-143` renders
the priority label and `reference/campaign-yaml.md:48` offers `priority:`, so a
campaign that sets it is rejected by Kueue's webhook. → **B18**, whose "admitted
next" is unreachable without the classes and preemption.

**X18 · The shipped defaults cannot admit a campaign** (*H4*). Chart quota is
cpu 4 / memory 8Gi / `nvidia.com/gpu` 1 — one pod (`values.yaml:57-62`); the
converter's default `window` is 20 (`models.py:265`) and partial admission was
deliberately removed (`render.py:126-130`). A first campaign on default values
renders `parallelism: 20` = 80 CPU / 20 GPU → inadmissible forever, shown only as
"Queued"; `test_chart_agreement.py` checks names, not arithmetic. Fix: `apply`
already talks to the API server — warn when
`parallelism × per-pod request > nominalQuota`. → new **B84**.

**X19 · Twenty-four hours on, nothing says which volumes failed** (*H5*).
`ttlSecondsAfterFinished: 86400` takes `completedIndexes`/`failedIndexes` with it;
the API then answers 404 (`app.py:126-128`) and no campaign-level record reaches
S3 — `publish.py:135-147` writes per-volume keys only. "What failed" then costs
10 000 HEADs. → **C08**, which still describes the deleted reconciler's
`status.json` and needs rewriting in Indexed-Job terms.

### Medium-severity findings (20)

Each names the angles that found it; the fix is the minimal one.

**X20 · Resume does not care which pipeline produced the pages it keeps** (*B-6*).
`_changed_sources` (`main.py:386-402`) compares only the redacted image URL
against `page_sources`; the `pipeline_sha256`/`image_digest` that `manifest.json`
records (`publish.py:343,347`) are read back nowhere. Bump a model revision and
re-run: every done page is kept while the fresh `manifest.json` claims the new sha
and digest produced all of them, and the ALTO `<Processing ID="htrflow-batch">`
block (`provenance.py:444-454`) is absent on exactly those pages, so the claim is
not per-page detectable either. Fix: clear `done` when `pipeline_sha256` differs,
or `image_digest` differs and is not `"unknown"` — `guards.check_drift`'s rule.
→ new **B83**.

**X21 · The Kyverno policies are never executed in this repo** (*E6*).
`.dagger/checks.go` renders and kubeconforms the chart; there is no
`kyverno apply` in `.dagger/` or `.github/workflows/` — only the downstream
campaigns template runs them (`examples/campaigns/.github/workflows/render.yml:108-116`).
The JMESPath (`split(image,'@')[0]`, `parse_yaml(… || 'steps: []')`, the
path-boundary regex) is asserted by comment, not test: a typo ships as an
admit-all policy. Fix: a dagger check — `helm template --show-only` plus
`kyverno apply` over a good/bad fixture pair. → **B21** (a fixture pair is a contract test).

**X22 · Nothing is deleted or measured, and run-log keys collide** (*H8*). No
lifecycle, TTL or quota for results, run logs or the model cache. Run logs sit at
a bucket-root, unprefixed key `status/logs/<pipeline>/<volume>.txt`
(`store.py:119-123`), so two namespaces — or two campaigns sharing a pipeline id
— overwrite each other. Warm-up Jobs have no TTL (`warmup-job.yaml`): live pods
3d5h old. `modelCache` is 30Gi RWO, no eviction, no usage metric
(`modelcache.yaml`); a full cache becomes every-index-fails with no signal.
`logship` re-PUTs the whole 4 MiB buffer every `LOG_SHIP_SECONDS: 15`
(`logship.py:28`) — ~115 k PUT/day at window 20. → **B10**.

**X23 · The apply ServiceAccount cannot reach the API server under `defaultDeny`**
(*D4*). `network.yaml:42-54` default-denies egress for `podSelector: {}` and only
the web pod gets apiserver egress (`web.yaml:163-177`); `apply-rbac.yaml` renders
SA/Role/RoleBinding but no NetworkPolicy, so the in-cluster apply the flag exists
for is DNS-only and dies with `cluster.py:82`'s "cannot reach the API server".
Fix: an `htr-campaigns-apply` NetworkPolicy behind the same flag. → **B12**.

**X24 · Workloads are patched over Kueue `v1beta1`; the chart renders `v1beta2`**
(*D7*). `cluster.py:39` vs `kueue.yaml:1,7,29`. Probe:
`workloads.kueue.x-k8s.io` serves v1beta1 with `storage=false` — deprecated. When
it goes, `sync_pause` 404s and `cluster.py:60-64` says "Kueue is not installed",
which is false. → **B66**, which replaces this mechanism anyway.

**X25 · A newline or tab in a manifest URL injects lines into `volumes.txt`**
(*E4*). `_http_url` (`models.py:67-70`) validates with `urlsplit`, which *strips*
`\n\r\t`; `source_line()` (`:133-137`) emits the original string. Probe: a URL
containing `\n../../status\thttps://evil/m` rendered a three-line `volumes.txt`
while `spec.completions` stayed 2 — the last volume never runs yet the Job
succeeds, and an index fetches an attacker URL with an unvalidated `VOLUME_REF`
(`config.py:38,94-97`) used as an S3 key component. Fix: reject control
characters. → **B86**.

**X26 · `/uv.html` has no CSP** (*E5*). The CSP is a SvelteKit prerender meta tag
(`svelte.config.js:16-23`); the UV build is copied in separately
(`.docker/htrflow-web.dockerfile:83`) and the header sets only
`frame-ancestors 'none'` (`app.py:31-35`) — so the one page that loads an
attacker-supplied `#?manifest=` URL has no `script-src`, `object-src` or
`base-uri`. Fix: send the full policy as a header for that path. → **B32**.

**X27 · An empty campaign renders `completions: 0`** (*A9, D8*). `models.py:145`
allows an empty `volumes:`; probe: `completions=0`, `parallelism=20`,
`volumes.txt=""` — accepted by validate, render and the API server alike: an
instantly-complete Job and a burned, append-only name. Fix: reject in validate;
clamp `parallelism` to `completions`. → **B86**.

**X28 · No `Cache-Control` anywhere** (*C-3*). Probe: `/`, `/config.js`, `/log`
and `/api/v1/jobs` answer with `ETag`/`Last-Modified` but no `Cache-Control`
(`app.py:31-35,164-166`). Browsers apply heuristic freshness from
`Last-Modified` — the image build time — so a redeploy can leave a stale HTML
shell and a stale `/config.js` (the `window.API_BASE` deploy hook) for days,
while `_app/immutable/*` gets no `immutable`. → new **C17**.

**X29 · A missing campaign ConfigMap gives a "load more" that never loads**
(*C-4*). `detail(job, configmap=None, …)` returns `volumes: []`, `latest: null`,
`failures: []` while `counts.total` keeps `spec.completions`
(`projection.py:308,334-346`); `CampaignCard.svelte:95` derives
`hasMore = volumes.length < counts.total`, so the card shows an empty table and a
permanent `load more (0/N)` whose click is a no-op, with no sentence saying why.
→ **C14**.

**X30 · `HTRFLOW_NAMESPACES` is a list; the RBAC is one namespaced Role**
(*C-5, E7*). `kube.py:60-63` parses a comma-separated list and `_list_jobs` loops
it, but `web.yaml:17-34` grants a `Role` in the release namespace only (its own
comment at `:88-91` concedes it). A second namespace 403s and, per X12, that
surfaces as a whole-list 500 rather than "namespace X unreadable". `get_job`
never checks `namespace` against `cfg.namespaces` either (`app.py:118-125`).
→ **B67**.

**X31 · The web↔cluster read path is only ever a fake** (*F-5*).
`packages/web/tests/test_kube.py` has six tests, all `Config.from_env`; it never
names `Reader`, and every app test injects `FakeReader` (`test_app.py:63`). The
selectors, the namespace fan-out and `_read`'s error-swallowing
(`kube.py:83-119`) are unexercised — which is why X12 and X13 survived.
→ **B21**.

**X32 · The compose test can only fail, and nothing calls it** (*F-4*).
`.dagger/compose.go:29` curls the web service; the services are
`riksarkivet/htrflow-batch:latest` and `riksarkivet/htrflow-web:latest`
(`docker-compose.yml:82,116`) — tags never published (`Makefile:91-97` says so) —
and `rustfs/rustfs:latest` (`:31`) floats against the digest rule
`.dagger/main.go:22-39` sets everywhere else. → **B44**.

**X33 · The campaigns repo's CI copies the policy values by hand, already drifted**
(*A7, E8*). `render.yml:44-46` repeats
`POLICY_ALLOWED_IMAGE_REPOS: "{docker.io/riksarkivet/}"`; the live PoC admits only
`127.0.0.1:30500/, rustfs/, docker.io/amazon/aws-cli`, and a server dry-run of
`examples/campaigns` was blocked by `htrflow-batch-images-allowed-htr-batch` for
the very image `pipelines/demo-v1.yaml` pins: green CI, rejected apply, no test.
The same workflow runs `CONVERTER_REF: main` with floating action tags in a job
holding `contents: write` (`:38,57,123`). Fix: read the values from the release.
→ **B11**.

**X34 · A failed render leaves `rendered/` half-written** (*A5*).
`cli.py:153-156` writes every pipeline file before the campaign loop and `_prune`
(`:173-174`) runs only on success; probe: adding a pipeline *and* editing a
campaign leaves `rendered/pipelines/demo-v2.yaml` behind with exit 1. Fix: render
to a temp dir, then move it into place. → **B78**.

**X35 · Names differing only by `-partN` collide** (*A6*).
`campaigns/foo-part1.yaml` plus a `foo.yaml` that splits share a file, Job and
ConfigMap name; probe: `foo` is refused with the misleading "campaign foo is
append-only". Fix: reject a stem matching `-part\d+$`. → **B72**.

**X36 · The PoC quickstart's allow-list misses images it installs** (*G6*).
`getting-started/deploy.md:131-140` enables `registry` + `nvidiaDevicePlugin`,
then sets `allowedImageRepos='{127.0.0.1:30500/,rustfs/,docker.io/amazon/aws-cli}'`
with `policies.enabled=true`. Probe: `helm template charts/htrflow-devstack` with
those flags renders four images; `docker.io/library/registry` and
`nvcr.io/nvidia/k8s-device-plugin` are missing, and
`images-allowed.yaml:33-36,53-56` matches every Job *and* Pod in the namespace. On
a restart those pods are denied and the GPU disappears, for a reader who followed
the docs. → **B68**.

**X37 · The written record of 2026-09 does not match what was built**
(*G2, G3, G4, G8, G9*). Spec D6 `:36` gives the wrapper `MAX_SECONDS`
(`grep -rn MAX_SECONDS packages/*/src` is empty since Task 25); D8 `:38` names
`packages/api` "proxied by the viewer nginx" (it is `packages/web`; nginx retired
in Task 17); D9 `:39` and §5 `:75` name `legacyLayout`, gone since Task 15; §5
says chart 0.3.0, `Chart.yaml:7` is 0.6.0. The plan's Global Constraints `:16`
still require the `job-min-parallelism: "1"` annotation that spec D1 `:31`
forbids and no code renders, and "`suspend` unset" against `campaign-job.yaml:13`'s
shipped `suspend: false`. `decision-log.md` — the self-declared "index into
everything else" — ends at D20 (2026-07-29): "models never baked into the image"
appears nowhere in `docs/`, "everyone logs in" only in story B67, "policy is
Kyverno's" only in `open-items.md:16`, "no CRD" only in `evolution.md:99`; and
D13 is "proposed, open" (`:18`) while `open-items.md:15` and
`render.py:31,142-143` say it was built. B63's own delivery text still describes
`MAX_SECONDS`, `legacyLayout`, the stale budgets and chart 0.3.0, and syncs to
PBI 2978 that way; the spec's one open decision (D11, log shipping) is tracked in
no open-items list. → new **B85**.

**X38 · Polling has no server-side amortisation** (*C-6*). `/api/v1/jobs` lists
full Job objects per namespace per request (`kube.py:93-98`) and `app.py:133`
re-lists every warm-up Job on each detail request; with M cards open in N tabs
that is M·N warm-up lists plus N job lists per minute, uncached. → **C08**.

**X39 · `finish()`'s worst case exceeds the 120 s grace period** (*B-4*).
`logship.py:230-235` joins with `timeout=30`, then `ship()` takes `_upload_lock`
**without a timeout**; the log client is connect 5 s / read 30 s /
`max_attempts: 2` ≈ 75 s per PUT (`store.py:141-147`). An in-flight PUT adds ~45 s
after the join expires plus ~75 s for the final one ≈ 150 s, over
`terminationGracePeriodSeconds: 120` (`campaign-job.yaml:50`, whose comment
estimates "~100 s"). Result: SIGKILL mid-cleanup — no complete run log, exit 137
instead of the clean 143 the manifest relies on, and no terminal line in the
frontend. Fix: `_upload_lock.acquire(timeout=…)`, or `max_attempts: 1`.
→ **B02**, whose Job contract the grace period belongs to.

### Low-severity findings (20)
Listed in the appendices, and each mapped to a story in
[the stories file](2026-09-07-audit-stories.md): the converter's three small
ones (A9), the wrapper's missing `gt=0` constraints (B-7), the frontend's five
verified defects (C-7), two chart-hygiene items (D9, D10), four supply-chain
ones (E7–E10), two CI ones (F-6, F-7) and four documentation ones (G7–G10).

---

## 3. Recommended order of work

1. **The two criticals, first and alone.** X1 (split on bytes) and X2 (delete
   page outputs; release htrflow's progress refs). Both are contained — one
   function in `render.py`, one `finally` in `stream.py` plus three lines in
   `driver.py` — and both are the difference between "works on 15 volumes" and
   "works on a real volume". Neither needs a cluster to prove: a render probe and
   a page-count RSS/tmpfs probe do it.
2. **The warm-up trio, together.** X3 (bound the wait, `FailIndex`), X4 (marker
   failure is fatal; SIGTERM writes a termination message) and X5 (the warm-up
   Job gets the campaign's scheduling fields). They are one failure story — a
   warm-up that did not work must never become an eighteen-hour silent GPU hold
   — and X5 is literally three fields.
3. **Two-line fixes to do now, in one commit each:** X15's first half (append
   `scripts/loc-budget.sh` to `make ci` and give every budget headroom — do this
   *before* anything else in this list, because every fix below needs the room),
   X24 (v1beta1 → v1beta2), X8 (cap the split name), X28 (`Cache-Control`).
4. **Before B12 stands up the DEV cluster** — B12 is what turns "reachable only
   through an SSH tunnel" into "reachable from the work network", so these must
   land first: X12 (a 500 without security headers on an unauthenticated
   surface), X13 (a detail poll that lists every Pod), X9 (`rendered/` is a
   code-execution path into the namespace), X10 (policy that constrains the pod
   shape) and X11 (defaults that enforce something), alongside the existing
   **B67** (everyone logs in), **B68** and **B69**. X23 comes with B12 itself.
5. **Before the gates can be trusted at all:** X14 (CI runs before merge), X16
   (the htrflow API pin test runs), X21 (the policies are tested), X31 (the
   cluster read path is tested). These do not change behaviour; they are what
   keeps steps 1–4 from silently regressing.
6. **Before archive scale (B16, and ATRaaS T04):** X6 (a finished campaign must
   not re-run), X17 and X18 (a campaign that is admitted, and priority that
   works), X19 and X22 (what failed, and what gets deleted), X20 (resume must
   not lie about provenance).
7. **Hygiene, ongoing:** X25–X39 and the twenty low findings. X37 (spec, plan,
   story and decision-log drift) is cheap and should ride along with whichever
   fix touches the same page.

---

## 4. What holds

Checked and found correct, so nobody re-audits them next month.

**Converter (A).** Parse: duplicate ids, non-ASCII and zero-width ids,
non-http(s) sources, both-or-neither sources, tag-not-digest images and unknown
keys each give one file-prefixed sentence plus a counted summary. Two renders of
`examples/campaigns` are byte-identical, with no YAML anchors from the cached
skeletons, and the split *mechanics* are right (10 001 volumes →
`-part1`/`-part2`, each ConfigMap wired to its own Job,
`completions == maxFailedIndexes == len(part)`). Volume-list immutability holds
for append, removal and reorder, also on an already-split campaign. Every
rendered object carries `managed-by=converter`, `Cluster.prune` lists by it,
deleting a source file removes its rendered manifest, and `--out` inside the repo
is refused before anything is deleted. `parallelism = min(campaign, config)`, no
`job-min-parallelism`, `suspend:` enforced against the Workload's `spec.active`
and failing loudly when no Workload appears, `apply --dry-run` contacts no API
server, and `apply` re-renders from source into a temp dir so a hand-edited
`rendered/` never applies on that path. `kubeconform -strict` passes; the Indexed
contract holds (`backoffLimitPerIndex: 3`, exit-13 `FailIndex`,
`DisruptionTarget Ignore`, per-pipeline `activeDeadlineSeconds`). No shell
injection: `WARMUP_WAIT_COMMAND` and every object name come from
`_NAME_RE`-validated file stems.

**Wrapper (B).** `config` is its own stage and `load` is split out of `stream`;
`OSError` is caught *before* `ValueError`, keeping `LocalEntryNotFoundError`
transient offline, and `TransientManifestError` is deliberately not a
`ManifestError`. `Terminated` is a `BaseException`, so SIGTERM unwinds through
`consume`'s per-page `except Exception`, and `_hard_exit` skips the executor join
that would run a stuck download into the SIGKILL. The termination `error` field —
not the JSON — is redacted and truncated; `_verify` puts counts and causes ahead
of name lists; `done_pages` intersects PAGE ∩ ALTO; the redacted-vs-redacted URL
compare is required. Lookahead bounds submission and a slot frees only after the
consumer returns, so *images* on tmpfs stay bounded; `stop` short-circuits queued
fetches; consecutive upload failures abort while malformed-output `ValueError` is
correctly excluded. Fetch checks magic bytes, Content-Type and both byte caps,
unlinks partials, and falls back to `/full/max/` on 400. PAGE is PUT before ALTO,
both parsed first; publish order is `iiif.json` → `pipeline.yaml` →
`manifest.json` last. The htrflow 0.2.6 contract is correct (`Export(dest,
format)` order, `Pipeline.from_config(path)`, `Pipeline(steps)`, `auto_import`),
YAML `Export` steps are rejected up front, and a serializer returning `None`
becomes a page failure, not a silent gap. No `Config` field is a credential and
redaction sits in `LogCapture._append`, covering bare `print()`s.

**Read API and frontend (C).** Fail-soft row parsing with the whole-list fallback
and the `describeUnreadable` banner; `reasons.ts` is one place and every branch
ends in a next step; `isHttpUrl` gates every query-driven URL on `/alto` and
`/log`; `BuiltSite` resolves `/log` without turning `config.js` into
`config.js.html` and `/../etc/passwd` 404s; the CSP intersection reasoning between
`app.py` and `svelte.config.js` is correct; `NoCluster` answers 503, not a static
404; `PartiallyFailed` and computing `latest`/`failures` over every volume are
right at thousand-volume scale; `offset`/`limit ≤ 1000` is enforced. Accessibility
is careful: conditional `aria-controls`, roving tabindex in `PageGrid`,
`role="alert"`/`role="status"`, `sr-only` captions, visible `:focus-visible`,
token contrast holding in both themes.

**Kubernetes, Helm, Kueue (D, H).** `podFailurePolicy` is shaped correctly for
`backoffLimitPerIndex` + `restartPolicy: Never`, `containerName: wrapper` matching
the real container. NetworkPolicy selectors match the labels the converter
renders; Job pods are restricted-clean, tokenless and tmpfs size-limited. The
apply Role's verbs are exactly what `cluster.py` issues; the web Role is read-only
and namespaced. `values.schema.json` rejects unknown keys in lint *and* template;
`publicResultsBase` is `required`; `_helpers.tpl:16-18` catches `--reuse-values`;
the ClusterQueue `namespaceSelector` is pinned to the release namespace. Kueue
scales as designed — one Workload per campaign, `podSets[0].count = parallelism`
(live probe), not one per index — and retries, progress and pause are the Job's
own, with no state file. Live `htr-batch` matches the render.

**Security and supply chain (E).** Digest pinning end to end: `_IMAGE_RE`,
`requireDigest`, the Kyverno pinned rule, every `FROM` tag+digest, every action
SHA-pinned, all renovate-tracked including the arm64 base ref and the UV4 commit.
`publish.yml` refuses to overwrite an immutable tag, builds arm64 natively (no
qemu), and does keyless cosign + SLSA provenance + Trivy SBOM in one shared
composite action, signing the manifest list too; Trivy CRITICAL gates both images
on main. `verifyImages` fails the render without both issuer and subject; the
allow-list regex matches on a path boundary, so `ghcr.io/riksarkivet` does not
admit `ghcr.io/riksarkivet-evil`. Nothing credential-shaped is in the tree; S3
credentials are a mounted ini file, never env, with a test covering literal env
reads; the bucket policy grants no `ListBucket`.

**CI and reproducibility (F).** `make ci` and the workflow share code — `Checks`
is the workflow's step, and the probe matched the baseline exactly. Every image
the dagger module pulls is tag *and* digest pinned; installs are lockfile-exact,
with ruff and ty from the locked venv rather than `uvx`. Both wrapper
architectures are built before merge, arm64 natively. The CI source mount is an
exclude-list with a test proving it; chart↔converter drift is test-enforced and
the helm gate includes a must-fail render. No flakiness, deterministic order, and
without the GB10 a contributor can still run `make test` and `make ci`.

**Documentation (G, H).** `zensical build --clean --strict` exits 0 with no
warnings and every relative link in `docs/` resolves;
`scripts/config_reference.py` rewrites `reference/configuration.md`
byte-identically; the walkthroughs run in order (`init` writes exactly the
documented five-file shape, `validate examples/campaigns` → 0, every `make` target
named in a live page exists); spec decisions D1, D2, D7, D10, D11 and D12 match
the code; the E2E log is in the nav and dated per task.
`docs/reference/wrapper.md:73-80` quantifies the tmpfs bound and
`values.yaml:36-45` the RWO single-node constraint; durability is a documented PoC
gap carried by B10; append-only is enforced at render time.

**ATRaaS (T01–T18).** B63 unblocks the *shape* of T03, T06 and T08: the converter
is a library, `images:` takes arbitrary URL lists, `Cluster.apply` needs no git,
cancel = prune, and `manifest.json` carries pipeline sha, image digest and
version. Still far: T04 (X1), T05/T13 (one queue, one namespace, one S3
credential, shared log keys), T10 (X22), T14 (B40).

## Appendix — per-angle findings

Severity: **C** critical · **H** high · **M** medium · **L** low. Story = the new
story id, or the existing story it folds into.

### A. Converter correctness

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| A1 | H | Split is a volume count, not a byte budget | `render.py:17,39-42,112` | B72 (X1) |
| A2 | H | A split campaign's Job name can exceed 63 chars | `models.py:36`, `render.py:136,216` | B72 (X8) |
| A3 | H | Pipelines are mutable in place; warm-up never re-runs | `cli.py:109,165`, `warmup.py:118-134` | B77 (X7) |
| A4 | M | PR CI renders to `$RUNNER_TEMP`, so the immutability guard never fires | `render.yml:59,96-97,118-134` | B78 (X9) |
| A5 | M | A failed render leaves `rendered/` half-written | `cli.py:153-156,173-174` | B78 (X34) |
| A6 | M | Names differing only by `-partN` collide | `cli.py:165`, `render.py:216` | B72 (X35) |
| A7 | M | Campaigns CI's policy values are a hand copy, already drifted | `render.yml:44-46` | B11 (X33) |
| A8 | M | `ClusterError` has no case for a 422 immutable-field | `cluster.py:50-72` | B77 (X7) |
| A9 | L | `volumes: []` → `completions: 0`; vague append-only text; stale `init` text | `models.py:145`, `cli.py:25,165` | B86 (X27) |

### B. Wrapper correctness

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| B-1 | C | Page outputs are never unlinked from the memory-backed workdir | `stream.py:196-202`, `main.py:198-200`, `campaign-job.yaml:161-164` | B73 (X2) |
| B-3 | M | htrflow's `progress` registries retain every `Document` | `htrflow/progress.py:19-21`, `driver.py:540-544` | B73 (X2) |
| B-2 | H | A warm-up with no marker still exits 0 | `warmup.py:114-115,118-134` | B75 (X4) |
| B-5 | M | Warm-up writes no termination message on SIGTERM/deadline | `warmup-job.yaml:23`, `warmup.py:95-108` | B75 (X4) |
| B-4 | M | `finish()`'s worst case (~150 s) exceeds the 120 s grace period | `logship.py:230-235`, `store.py:141-147` | B02 (X39) |
| B-6 | M | Resume ignores `pipeline_sha256` and `image_digest` | `main.py:386-402`, `publish.py:343,347` | B83 (X20) |
| B-7 | L | Four numeric limits have no `gt=0` | `config.py:48,53,57,58` | B86 |

### C. Read API and frontend

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| C-1 | H | `list_pods` has no field selector, limit or paging; called per poll | `kube.py:112-119`, `app.py:132` | C16 (X13) |
| C-2 | H | Unhandled exception → bare 500 with no security headers | `app.py:92-96`, `projection.py:33,203` | C15 (X12) |
| C-3 | M | No `Cache-Control` on HTML, `/config.js` or the API | `app.py:31-35,164-166` | C17 (X28) |
| C-4 | M | Missing campaign ConfigMap → empty table + dead "load more" | `projection.py:308,334-346`, `CampaignCard.svelte:95` | C14 (X29) |
| C-5 | M | `HTRFLOW_NAMESPACES` is a list; RBAC is one namespaced Role | `kube.py:60-63`, `web.yaml:17-34` | B67 (X30) |
| C-6 | L | No server-side amortisation of polling | `kube.py:93-98`, `app.py:133` | C08 (X38) |
| C-7 | L | `AbortController` never passed to `fetchJobs`; duplicate volume ids crash the card; volume ids unencoded in links; `newest()` ties at one-second granularity; the Role grants an unused `watch` | `+page.svelte:20-38`, `CampaignCard.svelte:299,328,166-169`, `projection.py:215-220,314-316`, `web.yaml:28` | C14, B32, C13 |

### D. Kubernetes, Helm, Kueue

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| D1 | H | TTL-reaped Job recreated by the next apply; every index re-runs | `campaign-job.yaml:30`, `cli.py:237-242` | B76 (X6) |
| D2 | H | Warm-up Job gets no `runtimeClassName`/`nodeSelector`/`tolerations` | `render.py:83-99` vs `:198-207` | B74 (X5) |
| D3 | H | The warm-up gate is unbounded and holds the GPU | `render.py:190-196`, `campaign-job.yaml:142-150,169-196` | B74 (X3) |
| D4 | M | `apply.rbac` cannot reach the API server under `defaultDeny` | `network.yaml:42-54`, `apply-rbac.yaml` | B12 (X23) |
| D5 | M | The 10 000 split does not bound the ConfigMap's 1 MiB | `render.py:17,39-42` | B72 (X1) |
| D6 | M | Edited pipeline → 422 mid-apply; one `try` blocks later campaigns | `cli.py:164,230-255` | B77 (X7) |
| D7 | M | Workloads patched over `v1beta1`; the chart renders `v1beta2` | `cluster.py:39`, `kueue.yaml:1,7,29` | B66 (X24) |
| D8 | M | An empty campaign renders `completions: 0`, `parallelism: 20` | `models.py:145` | B86 (X27) |
| D9 | L | Two releases collide on `ResourceFlavor`/`ClusterQueue` | `kueue.yaml:4,10` | B23 |
| D10 | L | `helm lint` passes what `helm template` rejects; PSA is not applied by the chart | `web.yaml:176`, `values.yaml:115`, `Makefile:174-184` | B23, B80 |

### E. Security and supply chain

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| E1 | H | `rendered/` is applied verbatim; only two subdirs are pruned | `cli.py:173-174`, `render.yml:135-142` | B78 (X9) |
| E2 | H | No policy constrains anything but the image reference | `templates/policies/*`, `campaign-job.yaml:165-168`, `network.yaml:105-121` | B79 (X10) |
| E3 | H | Every supply-chain control ships off | `values.yaml:95,99,108,115,127`, `Makefile:316-321` | B80 (X11) |
| E4 | M | Control characters in a URL inject lines into `volumes.txt` | `models.py:67-70,133-137` | B86 (X25) |
| E5 | M | `/uv.html` has no CSP | `svelte.config.js:16-23`, `app.py:31-35` | B32 (X26) |
| E6 | M | The policies are never executed in this repo | `.dagger/checks.go`, `.github/workflows/*` | B21 (X21) |
| E7 | L | `get_job` never checks `namespace` against `cfg.namespaces` | `app.py:118-125` | B67 |
| E8 | L | Campaigns CI runs `CONVERTER_REF: main` and floating action tags with `contents: write` | `render.yml:38,57,123` | B11 |
| E9 | L | The sample cosign subject carries a tag ref; publish is `workflow_dispatch` | `ci/full-values.yaml:32`, `values.yaml:129` | B14 |
| E10 | L | Redirects unchecked post-hop; `except` omits `169.254.0.0/16` | `main.py:83`, `network.yaml:40` | B80 |

### F. CI, tests and reproducibility

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| F-1 | H | `make ci` omits the LOC gate; all five budgets are 100 % full | `Makefile:75-78`, `ci.yml:38-48` | B82 (X15) |
| F-2 | H | Every gate is post-merge; branch CI is opt-in | `ci.yml:2-6` | B81 (X14) |
| F-3 | H | The wrapper↔htrflow contract test never runs automatically | `.dagger/test.go:38`, `ci.yml:15,87-95` | B25 (X16) |
| F-4 | M | The compose test can only fail, and nothing calls it | `.dagger/compose.go:29`, `docker-compose.yml:31,82,116` | B44 (X32) |
| F-5 | M | The web↔cluster read path is only ever a fake | `packages/web/tests/test_kube.py`, `kube.py:83-119` | B21 (X31) |
| F-6 | L | The developer's dagger engine is not CI's | `dagger.json:3` | B27 |
| F-7 | L | Budget blind spots: `.dagger/`, `scripts/`, devstack chart, blank lines | `scripts/loc-budget.sh:5` | B82 |
| — | L | `bun run lint` is not in `CheckFrontend`; `docs.yml` is still `workflow_dispatch`-only; no Zod↔projection fixture | `checks.go:107-111`, `docs.yml:2-6`, `api.ts:43+` | B22, B58, B21 |

### G. Documentation and spec drift

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| G1 | H | Three documents, three LOC budget sets, none matching the script | spec `:14-21`, plan `:15`, B63 `:63-65` | B82, B85 (X15) |
| G2 | M | Spec decisions superseded by Tasks 14–28 are unmarked | spec `:36,38,39,75` | B85 (X37) |
| G3 | M | The plan's Global Constraints contradict spec D1 and the code | plan `:16,19` | B85 (X37) |
| G4 | M | The decision log records none of the 2026-09 rulings; D13 is wrong | `decision-log.md:18,27-33` | B85 (X37) |
| G6 | M | The PoC quickstart's allow-list misses images it installs | `deploy.md:131-140` | B68 (X36) |
| G7 | L | `htrflow-campaigns init` contradicts the file it writes | `cli.py:25` | B86 |
| G8 | L | B63's delivery text describes what was later removed | B63 `:45-48,59,96-100` | B85 |
| G9 | L | The one open decision the spec names (D11) is tracked nowhere | spec `:41` | B85 |
| G10 | L | The 2026-09-02 handoffs are outside the nav and unmarked | `zensical.toml:193-195`, `frontend.md:35-44` | B58 |

### H. Scale, cost and operations

| ID | Sev | Finding | Where | Story |
|---|---|---|---|---|
| H1 | C | `volumes.txt` is split by count, never by bytes | `render.py:17,39-42`, `models.py:133-137` | B72 (X1) |
| H2 | H | A failed warm-up wedges the GPU quota for hours | `render.py:190-196`, `campaign-job.yaml:142-150` | B74 (X3) |
| H3 | H | One campaign owns the queue; `priority:` is decorative | `kueue.yaml`, `render.py:142-143` | B18 (X17) |
| H4 | H | The shipped defaults cannot admit a campaign | `values.yaml:57-62`, `models.py:265` | B84 (X18) |
| H5 | H | After the TTL nothing says which volumes failed | `campaign-job.yaml:30`, `app.py:126-128` | C08 (X19) |
| H6 | M | The read API is O(volumes) and O(pods) per request | `app.py:126-135`, `projection.py:307-345` | C16, C08 (X13) |
| H7 | M | Progress parsing has no guard against truncated ranges | `projection.py:20-36` | C15 (X12) |
| H8 | M | Nothing is deleted or measured; run-log keys collide | `store.py:119-123`, `modelcache.yaml`, `logship.py:28` | B10 (X22) |
| — | — | The runbook explains mechanism but gives no commands | `failure-handling.md:213` | B57 |
