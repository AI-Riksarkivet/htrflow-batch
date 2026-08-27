# Repository audit — 2026-08-26

> **Remediation (2026-08-26/27).** The findings below were worked in five
> packages on `feat/campaign-browser-visibility`, per the
> [remediation plan](../superpowers/plans/2026-08-26-audit-remediation-plan.md):
> A1 reconciler (merge `9b36920`), A2 wrapper (`ade2b14`), A3 chart/ops
> (`4399f19`), A4 frontend (`8e6f661`); B1 CI/tests and B2 docs were pending
> when this note was written. Each package left a handoff under
> `docs/superpowers/plans/2026-08-26-handoff-*.md`. The docs pass (B2)
> rewrote the pages this report names in Appendix F and X12 to describe the
> code as merged; its own notes on what still contradicts the plan are in
> [the B2 handoff](../superpowers/plans/2026-08-26-handoff-b2-docs.md).
> The report itself is left as written, with `file:line` pointers at
> commit `44dddb6`.

**Scope:** `htrflow-batch` at `feat/campaign-browser-visibility` (commit `44dddb6`, 42 commits ahead of `campaign-gitops`), plus the live release `htr` (revision 21) on the GB10 k3s node used as evidence.
**Method:** seven independent read-only reviews, one per angle, each required to verify every finding in code or by probe (throwaway tests, Playwright, `kubectl`/`helm` read-only, `curl`) before reporting. Findings were then cross-checked and deduplicated here; each keeps its evidence pointer. Nothing below is inferred.
**Baseline at audit time:** reconciler 121 tests (81 % coverage), wrapper 84 (93 %), frontend 26 (lib only), `svelte-check` 0/0, `helm lint` clean, `ruff` clean, `uv lock` fresh. `make typecheck` was red on one annotation (fixed in `44dddb6` during the audit).

---

## 1. Verdict

The platform is sound at the scale it has been run at (≤ 15 volumes, one GPU) and its hardening story for the pods that matter — volume Jobs, warm-ups, reconciler — is genuinely restricted-clean. What the audit found is that **three assumptions the design leans on do not yet hold in the code**:

1. **"S3 is the authority and the reconciler is idempotent."** The terminal verdict `needs-attention` is derived from a Kubernetes Job that is garbage-collected after 24 h; once it is gone the volume reverts to `pending` and is resubmitted forever. The retry path is not atomic either (delete first, persist the attempt counter last).
2. **"The tick is cheap."** The tick performs 3–4 sequential S3 round-trips per *done* volume plus serial 30 s-timeout HTTP fetches, with every write and every submission at the end, under a 240 s CronJob deadline. At the archive scale the docs advertise (thousands of volumes) the first large campaign wedges the reconciler and the page shows STALE forever.
3. **"The campaigns repo is configuration."** It is a code-execution boundary: any digest-pinned image from any registry, any HF model repo (pickled YOLO weights) without a revision, fetched over unauthenticated `git://`. Write access to that repo equals cluster operator with the bucket's write keys.

Alongside those: a handful of wrapper defects that lose or mis-classify work (transient manifest errors treated as permanent; PAGE XML silently dropped on resume), a frontend that goes blank on one bad field, and documentation that in several places describes a system that was never built (`htrq`, `podFailurePolicy`, `envFrom` secrets). None of it is exotic; nearly all of it is a day or two of focused work each, and the section 3 roadmap orders it.

**Counts by severity** (after dedup across angles): critical 1 · high 17 · medium 41 · low 44.

---

## 2. Cross-cutting findings, ranked

Each item names the angles that found it independently. IDs in brackets refer to the per-angle appendix.

### X1 — CRITICAL · Tick cost is O(volumes) with all effects last; archive scale wedges the reconciler
*Reconciler R4 · Operability O1*
`main.py` per tick: `done_volumes` LIST + HEAD per volume prefix, a GET of the synthetic manifest for every `images:` volume every tick (`:391`), `count_pages` LIST per done volume (`:431`), run-log HEAD per done/running/queued volume (`:446`); pre-validation is a serial `httpx.get(timeout=30)` per not-yet-validated manifest and `unreachable` is re-fetched every tick by design. Submissions and all three JSON writes happen after the loop (`:502-509`). 5 000 volumes ≈ 15–20 k calls ≈ 5–7 min > `activeDeadlineSeconds: 240`; ten unreachable hosts alone cost 300 s. A deadline-killed tick submits nothing, writes nothing, and repeats. Today's 15 volumes take 4 s, which is the only reason it works.
**Fix:** don't probe done volumes (cache `pages_done`/mtime per volume, or read `pages` from the `manifest.json` you already HEAD); one LIST per pipeline; thread-pooled HEADs; bounded validations per tick with a short timeout and *incremental* persistence of `validation.json`; write `status.json` and submit before enrichment; raise the deadline to the schedule interval; log tick duration.

### X2 — HIGH · `needs-attention` is not sticky; capped/exit-13 volumes are resubmitted every 24 h forever
*Reconciler R1 (+ R6)*
`status.py:67-73` computes the terminal verdict only while a Job object exists; Jobs carry `ttlSecondsAfterFinished: 86400` and the needs-attention path never deletes them, so after the reap `derive` returns `pending`, the volume re-enters the lane and burns a GPU run — exit-13 volumes never bump `attempts`, so this is unbounded. Live: `htr-eng-modern-v1-iam-examples-57c8fbdc` (Failed, 19 h) already has no pod, and with `exit_code=None` (`k8s.py:344-352`) a permanent failure additionally degrades to `retry`. Docs promise "never auto-resubmitted".
**Fix:** persist the terminal verdict (in `attempts.json` or `status/terminal.json`) when first derived and honour it regardless of Job presence; fall back to the Job's Failed-condition reason; use a `podFailurePolicy` so exit 13 is carried by the Job itself (see X5).

### X3 — HIGH · The retry path is not atomic
*Reconciler R2, R3 · Operability O8, O9*
Retry deletes the Job with Foreground propagation and creates the same name in the same tick (409 swallowed → retry lands next tick, window slot wasted); a pod stuck Terminating keeps the Job listed Failed, so `attempts` is bumped again every tick until the cap with no re-run. The bump is persisted only at tick end (`:508`) after the destructive delete (`:415`); any abort in between (S3 error, deadline, manual tick — 8 `UnexpectedJob` events in the last hour show manual ticks bypass `concurrencyPolicy: Forbid`) loses it.
**Fix:** persist `attempts.json` immediately after each bump; treat Jobs with `deletionTimestamp` as "deleting" (not in lane, not bumped); per-volume try/except so one bad S3 response doesn't kill the tick; a Lease per tick or "suspend the CronJob first" for manual runs.

### X4 — HIGH · The campaigns repo is an unauthenticated code-execution boundary
*Security S1*
`parse.py:157-159` accepts any image with `@sha256:` in it; the Job runs it with the GPU, `runtimeClassName: nvidia`, the S3 write credentials and the model cache; the warm-up additionally has internet egress and mounts the cache read-write. `model_settings.model` is any HF repo; YOLO weights are `hf_hub_download(*.pt)` — a pickle load — and no pipeline pins `revision:` although htrflow supports it. Transport is `git://` (unauthenticated, unencrypted, no signature check).
**Fix:** image-repository allow-list enforced in `parse_pipeline`; Kyverno/Sigstore `verifyImages` + cosign in `publish.yml`; mandatory 40-hex `revision:` per model (or OCI model artifacts so warm-ups need no internet); HTTPS with a read-only deploy token; branch protection; document "campaign-repo write == cluster operator".

### X5 — HIGH · The Job contract the docs describe is not the one built; disruptions burn attempts; long volumes can't finish
*Operability O2, O3 · Docs D-H2 · Wrapper (SIGTERM)*
Docs: `backoffLimit: 2`, `podFailurePolicy`, 7-day TTL, "disruption does not consume a retry", `htrq retry`. Code: `backoffLimit: 0`, no `podFailurePolicy`, TTL 24 h, reconciler-level retries — so a node drain/preemption/OOMKill burns an attempt and re-pulls a 10 GB image. Measured throughput on the GB10 is ~12.9 s/page (R0001696), so with `activeDeadlineSeconds: 21600` any volume ≳ 1 650 pages cannot finish one attempt; the wrapper has no SIGTERM handler, so the deadline kill exits 143 with no termination log and is classified `retry`.
**Fix:** `podFailurePolicy: [Ignore on DisruptionTarget; FailJob on exit 13]` (works with `backoffLimit: 0`); deadline derived from `page_count` (e.g. `max(6 h, pages × 30 s)`); don't charge an attempt when `pages_done` advanced; SIGTERM handler that writes the termination log and ships the final log; rewrite `failure-handling.md`/`wrapper.md` to match.

### X6 — HIGH · A fresh install loops warm-ups forever, and a green tick logs nothing
*Operability O4, O5 · Reconciler R7 · Wrapper W12*
The model-cache PVC `htr-test-data` is a hard-coded prerequisite (`jobspec.py:53`, `__main__.py`, Makefile) — not rendered, not a value, not in the production install doc. Without it warm-ups sit Pending for 3 600 s, fail, and are deleted/recreated every tick with no cap; the same loop applies to any `load()` exception (typo'd model id → transient). Meanwhile a successful tick emits no log line at all, there are no metrics or Events, and the STALE threshold is `3 × 300 s` hard-coded regardless of the chart's schedule.
**Fix:** chart renders the PVC (`modelCache.{create,name,size,storageClass}`) and passes `RECONCILER_DATA_PVC`; cap warm-up attempts and stop on exit 13 (`warmup.py` should classify `ValueError`/YAML errors as permanent); one summary line per tick; `TICK_SECONDS` from the chart; documented kube-state-metrics alerts.

### X7 — HIGH · Wrapper misclassifies transient manifest failures as permanent, and can lose PAGE XML silently
*Wrapper W1, W2 (+ W3, W4)*
`iiif.py:22-28` turns every `httpx.HTTPError` and every non-200 (502/503/429) into exit 13 → `needs-attention`, no retry; a DNS blip during manifest fetch parks the volume. `store.py` uploads ALTO before PAGE while `done_pages()` and the verify gate list only `alto/`: a PAGE upload failure followed by a resume yields a "complete" volume with `page/NNNN.xml` missing (probe-confirmed). Related: unparseable local ALTO fails publish then is accepted on retry; an HTTP 200 HTML body is saved as a JPEG and burns an attempt.
**Fix:** only 4xx/non-JSON are permanent; upload PAGE before ALTO and verify both; parse XML at upload time; reject non-`image/*` or empty bodies as retryable.

### X8 — HIGH · devStack exposes S3 (default creds) and an unauthenticated registry on NodePorts; control-plane images are tag-pinned
*Security S2, S3*
`rustfsadmin/rustfsadmin` hard-coded; S3 30900 and the admin console 30901 as NodePorts; `registry:3` on 30500 with anonymous catalog (verified). Volume Jobs pull by digest (good) but `reconciler.image`/`viewer.image` are tags (`dev11`/`dev12`) with `IfNotPresent`; anyone on the network can push over the tag. The production path still gives one S3 credential to the reconciler and every Job.
**Fix:** never run devStack near real data; `required`-check `@sha256:` for `reconciler.image`/`viewer.image`; registry behind auth (Harbor already runs) or ClusterIP; two S3 principals (Job creds scoped to its own prefix + run-log key; reconciler to `status/*`/`sources/*`).

### X9 — HIGH · One bad field blanks the whole frontend; a transient poll error hides a rendered page
*Frontend F1*
An unknown volume status or a missing `attempts` → zero campaigns and a raw ZodError on screen; a single HTTP 500 on the 60 s poll replaces the rendered page with an error until the next good poll (error is checked before `doc`).
**Fix:** render `doc` whenever non-null with the error as a banner; `safeParse` per campaign/volume and degrade the bad row; `volumeStatusSchema.catch("unknown")` with a neutral chip.

### X10 — HIGH · Thumbnails cost 6.7 MB to paint eight 26 px images
*Frontend F2 (introduced 2026-08-26 by the synthetic-thumbnail change)*
Five Hugging Face originals (up to 3 MB, 2479×3542) load on every visit; the one IIIF-sized thumbnail is 4 KB. `loading="lazy"` does nothing above the fold.
**Fix:** reconciler emits sized URLs where a IIIF service exists and skips (or serves a resized copy under `status/thumbs/`) for service-less manifests; `fetchpriority="low"`; hide non-IIIF thumbnails until then.

### X11 — HIGH · CI does not run what `make ci` runs; typecheck was red
*Tests T1, T2 · Frontend F15*
`.dagger/checks.go` = ruff + `helm lint`; `test.go` = pytest. No `bun run test`/`check`, no `ty`, no chart render in CI. `uvx` (unpinned latest) is used while `uv.lock` pins ruff/ty, so results drift. The annotation bug in `_source_manifest_url` (fixed in `44dddb6`) would have shipped.
**Fix:** `uv run --no-sync ruff|ty`; `CheckFrontend` + `Typecheck` dagger functions in `Checks`; a chart-render step (`ci/full-values.yaml` + kubeconform).

### X12 — HIGH · Docs actively mislead a new operator in three places
*Docs D-H1, D-H2, D-H3, D-H4*
`chart.md`/`reconciler.md`/`values.yaml` describe an S3 Secret consumed via `envFrom` with `AWS_ACCESS_KEY_ID…` — the code mounts a `credentials` ini file; a Secret built per those pages has no credentials. `failure-handling.md`/`wrapper.md` document a Job contract that was never built (X5). `htrq` is presented as settled tooling across five pages and two decision-log entries; it does not exist and the reconciler CronJob contradicts D7/D18. The chart README pins `uv4:v3`, which the rest of the docs say won't work.
**Fix:** as listed in the docs appendix; `security.md` and `deploy.md` are already correct and can be the source.

### X13 — MEDIUM · URLs from campaign data and the query string reach `href` unvalidated
*Security S4, S5 · Frontend F6*
`javascript:` in `source_manifest`/`thumbnail`/`?log=` renders into `href`/`src` (verified); no CSP or security headers on the viewer. Pre-validation and wrapper fetches have no scheme allow-list, size cap or redirect limit (a multi-GB "manifest" OOMs the 512 Mi reconciler every tick).
**Fix:** `parse.py` rejects non-http(s) `manifest:`/`images:`; zod `url().refine(http/https)`; the `/log` route accepts same-origin/prefix URLs only; CSP on nginx; byte caps + `max_redirects` on every fetch; cache `unreachable` for N ticks.

### X14 — MEDIUM · The whole `status/` tree (full pod logs included) is world-readable
*Security S6*
Anonymous `GetObject` on `*` (listing denied, but every key is derivable from `status.json`). Logs would publish a tokenised private IIIF URL on failure (`iiif.py:26,28`).
**Fix:** split public (`<pipeline>/<volume>/*`, `status/status.json`, `sources/`) from operator keys in the bucket policy; strip userinfo/query from URLs before logging.

### X15 — MEDIUM · Chart/values hygiene
*Operability O6, O7, O10, O11, O12, O13, O14 · Tests T8*
No `values.schema.json` and the version never bumped (21 revisions at 0.1.0) — `--reuse-values` once silently dropped every NetworkPolicy; no `startingDeadlineSeconds`; default `queue.resources` cannot admit the Job the reconciler builds; the devStack Secret/registry Namespace/PVC are release-owned (uninstall deletes them); `.Values.image.*`/`s3.endpoint` are dead but documented as core; `:dev` tags with `IfNotPresent` never land; RuntimeClass, device plugin and git-daemon live outside Helm.

### X16 — MEDIUM · Reproducibility of images and pins
*Wrapper W8 · Security S7 · Tests T6, T9*
`uv:latest`, `--upgrade torch torchvision` floating, `sentencepiece`/`transformers<5`/apt unpinned; wrapper deps installed without the lock; the arm64 GPU base was built 77 commits past `v0.2.6` while `manifest.json` records "0.2.6"; cosign/SLSA removed from `publish.yml`; Trivy not wired into CI; Publish covers one image/one arch/one immutable tag.

### X17 — MEDIUM · Fairness, drift-hash and other reconciler semantics
*Reconciler R5, R8, R9, R10, R11, R14*
Round-robin restarts from the alphabetically first campaign each tick (big campaigns starve small ones in steady state); corrupt `attempts.json`/`validation.json` is a poison pill for every tick; `images:` edits are ignored (synthetic manifest is write-once); the drift hash is over PyYAML's serialisation rather than a canonical form; the guard reads one arbitrary manifest.

### X18 — MEDIUM · Frontend accessibility and small screens
*Frontend F3, F4, F5, F7*
At 390 px the fixed colgroup (630 px) collapses the volume column to 0 px and the page scrolls sideways; the pipeline chip is a `role=button` span nested in the campaign `<button>` (Enter opens the YAML *and* collapses the campaign); light-theme status chips fail AA (done 2.5:1, queued 2.0:1); the log viewer overflows on unbroken URLs.

### X19 — MEDIUM · Test gaps at the boundaries
*Tests T3, T4, T5, T7*
`k8s.py` is 21 % covered and `FakeCluster` never applies the label selector that keeps the window honest; the documented "Level 0 library-API pin test" for htrflow does not exist; no contract test ties the reconciler's `status.json` to the frontend schema (the dev fixture has already drifted: 9 vs 15 volume keys) or the Job env to the wrapper's required env; `gitrepo.py` 41 %.

---

## 3. Recommended order of work

**Before real data or a real campaign repo (blocking):** X1, X2, X3, X4, X5, X7, X8. These are correctness-of-the-loop and trust-boundary issues; each is contained (reconciler main/status/k8s, jobspec, parse, iiif/store, chart values) and unit-testable with the existing fakes once the fakes model `deletionTimestamp` and 409s.

**Next (a sprint):** X6, X9, X10, X11, X12, X13, X14 — install-ability, observability, the frontend's failure mode, CI parity, the misleading docs, URL trust.

**Hygiene (ongoing):** X15–X19 plus the low-severity items in the appendix; the test list in T3/T5/T7 is concrete enough to hand to a contributor.

---

## 4. What holds

Things the auditors checked and found correct, so nobody re-audits them next month:

- **Pod security:** volume Jobs, warm-ups and the reconciler are restricted-clean (uid 1000, drop ALL, no privilege escalation, RO rootfs, seccomp RuntimeDefault, no SA token on Jobs); creds as a `0440` file; live Job spec matches `jobspec.py`; NetworkPolicies live equal the chart (Jobs reach only DNS, RustFS and the IIIF host); reconciler Role-only with no secrets/configmap-update; anonymous apiserver → 401.
- **Reconciler semantics that are right:** CronJob `Forbid` + `backoffLimit 0`; deterministic, collision-free Job names; drift guard runs before `ensure_configmap` and ConfigMaps are immutable; S3 is authoritative in `derive`; blocked pipelines neither bump nor delete; window counts only managed non-terminal Jobs; `Bucket.exists` handles 404 codes; UTC everywhere; URL audience split; campaign/pipeline parse errors contained; `gitrepo` redacts userinfo.
- **Wrapper:** stream/fetch cannot deadlock (semaphore in `finally`, sentinel always delivered); `manifest.json` is truly last and never written on failure; termination log written before the failure publish; `ValueError`→13 vs `OSError`→1 correct; memory budget measured on R0001696 (~31 MB outputs, ~23 MB download window); IIIF P2/P3 parsing incl. narrow-canvas `max` and the 400 fallback; log shipping lock order and finish serialisation correct and working in-cluster.
- **Frontend:** no `{@html}`, everything escaped, `rel="noopener"`; zod strips unknown keys so additive reconciler fields don't break older builds; effects clean up; prerender-safe; a 4 MB live log renders in 1.7 s and re-renders in ~200 ms; RustFS honours `If-None-Match`; zero console errors; strict TS trio on.
- **Chart/CI:** rendered chart == live manifest byte-for-byte; Kueue v1beta2 with an Active queue and explicit pending reasons; warm-ups never hold the GPU; actions SHA-pinned, `permissions: contents: read`, publish manual, `uv sync --locked`.
- **Docs that are accurate today:** `reference/wrapper.md`, `reference/campaign-yaml.md`, `reference/reconciler.md` (bar one line), `development/security.md`, `getting-started/deploy.md`, `viewing.md`, `deployment.md`, the 2026-08-25 test log.

---

## Appendix — per-angle findings

Severity: **C** critical · **H** high · **M** medium · **L** low. `file:line` as at commit `44dddb6` unless noted.

### A. Reconciler correctness

| ID | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| R1 | H | `needs-attention` not sticky; reaped Job → `pending` → resubmitted every 24 h forever | `status.py:67-73`, `jobspec.py:180` | persist terminal verdict |
| R2 | H | same-tick delete→create of the same Job name (409 swallowed); slow Foreground delete double-charges attempts | `main.py:414-418,502-505`, `k8s.py:365,371` | honour `deletionTimestamp`; bump once |
| R3 | H | attempt bump persisted at tick end, after the delete; no per-volume try/except | `main.py:414-416,508` | persist immediately; contain per volume |
| R4 | H | O(volumes) serial S3/HTTP work under 240 s deadline; `validation.json` written last | `main.py:110,236,431,446,507`, `__main__.py:482` | see X1 |
| R5 | M | round-robin restarts from the same lane each tick — starvation in steady state | `plan.py:97-104` | order by in-flight count / persisted cursor |
| R6 | M | `exit_code=None` when the pod is gone → permanent failure degrades to `retry`, empty failure log | `k8s.py:344-352` | Job condition reason; podFailurePolicy |
| R7 | M | warm-up retries unbounded, ignore exit 13 | `main.py:312-318`, `warmup.py:45-49` | stop on 13; cap |
| R8 | M | corrupt reconciler-owned JSON kills every tick | `s3.py:193-198` | catch `ValueError`, treat as absent |
| R9 | M | `images:` edits ignored — synthetic manifest write-once | `main.py:110-111` | hash list into key / overwrite when not running |
| R10 | L | drift hash over PyYAML serialisation | `parse.py:338-343` | canonical JSON |
| R11 | L | drift guard reads one arbitrary manifest; legacy manifest blocks forever | `main.py:274` | skip w/o provenance |
| R12 | L | `tick_seconds` hard-coded 300 vs chart schedule | `main.py:20` | from env |
| R13 | L | `_source_manifest_url` return type lied | `main.py:107` | **fixed `44dddb6`** |
| R14 | L | orphans miscounted when a sibling campaign fails to parse | `main.py:336-338` | include errored campaigns' ids |

### B. Wrapper correctness

| ID | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| W1 | H | all HTTP errors / non-200 on manifest fetch → exit 13 (no retry) | `iiif.py:22-28`, `main.py:297` | 4xx/non-JSON permanent only |
| W2 | H | ALTO-first upload + ALTO-only verify/resume loses PAGE XML silently | `store.py:34-54`, `main.py:205-210` | PAGE first; verify both |
| W3 | M | unparseable local ALTO fails publish, then accepted on retry | `main.py:224,231-235` | parse at upload |
| W4 | M | any 200 body accepted as image (HTML) → burns an attempt | `fetch.py:39-45` | reject non-image |
| W5 | M | no width cap or byte bound for service-less canvases (synthetic manifests); partial file on ENOSPC | `iiif.py:61-70`, `fetch.py:39-42` | size guard; unlink |
| W6 | M | S3 outage → 6 h zombie (default boto timeouts, upload failures swallowed) | `store.py:19`, `stream.py:57` | bounded config; abort after N failures |
| W7 | M | `manifest.json` lacks canvas→source mapping; resume by position only | `main.py:266-273` | record image_url per page |
| W8 | M | image recipes not reproducible; base built 77 commits past the tag it reports | `.docker/*.dockerfile` | pin everything; label base |
| W9 | L | model-load failure attributed to stage `stream` | `main.py:162,195` | `load` stage |
| W10 | L | non-daemon executor delays exit on early failure | `fetch.py:103` | cancel_futures |
| W11 | L | non-int canvas width → TypeError → retried to cap | `iiif.py:50` | `int()` guard |
| W12 | L | warm-up returns transient for permanent config errors | `warmup.py:53-59` | classify permanent |
| W13 | L | `_thumbnail` missing `rstrip("/")` | `viewer.py:43` | rstrip |

### C. Security & supply chain

| ID | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| S1 | H | campaigns repo = RCE boundary (any image, any HF repo, pickle weights, `git://`) | `parse.py:157-159`, `jobspec.py:139-140,191-250`, `gitrepo.py:43` | allow-list, verifyImages, `revision:`, HTTPS token |
| S2 | H | devStack S3 default creds + console on NodePorts; one credential for all pods | `devstack-rustfs.yaml` | never off-laptop; two principals |
| S3 | H | anonymous registry; reconciler/viewer tag-pinned with `IfNotPresent` | `devstack-registry.yaml`, values | digest-required; auth |
| S4 | M | `javascript:` reaches `href`/`src`; no CSP | `+page.svelte:186,220`, `log/+page.svelte:205` | scheme checks; CSP |
| S5 | M | unbounded fetches driven by campaign data (SSRF/DoS) | `__main__.py:362-367`, `iiif.py:22-32`, `fetch.py:108-111` | caps, http(s) only |
| S6 | M | `status/` tree world-readable incl. full logs | bucket policy, `s3.py:28-49` | split policy; redact URLs |
| S7 | M | floating tags/binaries in every image; cosign removed; Trivy unwired | dockerfiles, `.dagger/*`, `publish.yml` | digest-pin; restore signing |
| S8 | L/M | viewer/rustfs/git-daemon not restricted-clean, SA token mounted, git-daemon unmanaged | `viewer.yaml`, scratch `git-daemon.yaml` | securityContext; default-deny |
| S9 | L | ClusterQueue `namespaceSelector: {}`; reconciler egress `0.0.0.0/0`; `git-repos` bucket readable | `kueue.yaml`, `values.yaml:66` | narrow |

### D. Kubernetes / Helm / operability

| ID | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| O1 | C | tick O(N) under 240 s deadline, effects last | `main.py`, `reconciler.yaml:48` | X1 |
| O2 | H | 6 h Job deadline vs 12.9 s/page; SIGTERM unhandled; deadline burns attempts | `jobspec.py:56`, `k8s.py:333` | deadline from pages; handler |
| O3 | H | no `podFailurePolicy`; drains burn attempts; docs say otherwise | `jobspec.py:177-180` | add policy |
| O4 | H | cache PVC hidden prerequisite; warm-up loop uncapped | `jobspec.py:53`, `main.py:312-318` | render PVC; cap |
| O5 | H | zero observability on success; STALE threshold ignores schedule | reconciler src, `main.py:20` | tick summary; alerts |
| O6 | M | no values schema; version never bumped; `--reuse-values` dropped NetworkPolicies once | chart | schema; `fail` guards |
| O7 | M | no `startingDeadlineSeconds`; 240 s deadline < 300 s git timeout | `reconciler.yaml:43-48`, `gitrepo.py:253` | set/align |
| O8 | M | manual ticks bypass `Forbid` (8 `UnexpectedJob` events) | — | Lease |
| O9 | M | attempts not crash-safe | `main.py:415,508` | X3 |
| O10 | M | default `queue.resources` cannot admit the built Job | `values.yaml:26-31`, `jobspec.py:149` | match |
| O11 | M | release owns the S3 Secret, registry Namespace and 60 Gi PVC | devstack templates | `resource-policy: keep` |
| O12 | M | dead `image.*`/`s3.endpoint` values documented as core | chart, `chart.md`, `deploy.md` | remove |
| O13 | M | `:dev` tags + `IfNotPresent` never land; `poc-push` builds amd64 on arm64 | `Makefile:86-90` | digest or Always |
| O14 | M | RuntimeClass, device plugin, git-daemon outside Helm | cluster | adopt or document |
| O15 | L | GPU placement hard-coded (no selector/tolerations) | `jobspec.py:185` | values |
| O16 | L | cache RWO/node-pinned, no eviction | — | document |
| O17 | L | Kueue down looks like GPU busy | — | warn on queued>X |
| O18 | L | devStack sizing (5 Gi results PVC, 60 Gi registry no GC) | devstack templates | — |

### E. Frontend

| ID | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| F1 | H | one bad field blanks the page; poll error hides rendered doc | `+page.svelte:62-71,112-116` | fail-soft |
| F2 | H | 6.7 MB of thumbnails for 26 px images | `+page.svelte:184-192`; reconciler thumb fallback | sized URLs |
| F3 | H | 390 px: volume column 0 px, horizontal scroll | `+page.svelte:549-576` | scroll container / media query |
| F4 | M | nested `role=button` chip inside `<button>`; Enter collapses + opens | `+page.svelte:126-149` | sibling buttons + aria |
| F5 | M | AA contrast failures light (done 2.5, queued 1.98) and some dark | tokens `:264-266` | per-theme semantics |
| F6 | M | `javascript:` URLs unvalidated | `+page.svelte:186,220,228`, `log:54,205` | zod refine |
| F7 | M | log viewer horizontal overflow on long URLs | `log/+page.svelte:654-659` | `minmax(0,1fr)` |
| F8–F14 | L | reduced-motion, aria-live, live-404 spins, three timestamp formats, duplicated theme code, unnamed tables, no in-flight guard | — | as listed |
| F15 | L | no lint/format/engines; frontend not in CI; zero component tests | `frontend/`, `.dagger` | add |
| F16 | L | `frontend.md`/`s3-layout.md`/`campaigns.md` drift | docs | update |

### F. Documentation & spec drift

| ID | Sev | Finding | Doc | Truth |
|---|---|---|---|---|
| D-H1 | H | S3 Secret via `envFrom` with AWS_* keys | `chart.md:12`, `reconciler.md:48`, `values.yaml:8-16` | mounted `credentials` file (`reconciler.yaml:93-130`, `jobspec.py:75-93`) |
| D-H2 | H | `backoffLimit 2`, `podFailurePolicy`, 7 d TTL, disruption-safe retries, `htrq retry` | `failure-handling.md:5-29`, `wrapper.md:152-158`, `run-a-volume.md:53-54` | `backoffLimit 0`, none, 24 h, reconciler retries |
| D-H3 | H | `htrq` CLI as settled/existing (D7, D18) | decision-log, architecture, wrapper, run-a-volume, testing | doesn't exist; reconciler contradicts D7/D18 |
| D-H4 | H | chart README pins `uv4:v3` (port 80) | `charts/…/README.md:41,82` | viewer on 8080 |
| D-M1–M12 | M | `network.*` values undocumented; `campaignsRepoWebUrl` undocumented; `status.json` example stale; `frontend.md` still "cards"; duplicated wrapper env tables behind; reconciler settings "chart supplies all"; open-items D9–D12 done; memory-budget 16 Gi request (is 8 Gi); test counts 141→205; live-log spec vs delivery (head/tail sizes, statuses, retry deletes key, `run_manifest`); table spec "steps line" → tooltip/YAML; arm64 image, git-daemon flow, `internal_results_base` undocumented | various | see auditor report |
| D-L1–L10 | L | `batch_run.py` mention, DNS-1123 vs regex, drift ground-truth incl. `image_digest`, "last log lines", `make test` scope, illustrative Kueue YAML, HCP vs RustFS, code comments claiming chart-rendered warm-ups, scaffold generator that doesn't exist, npm/bun wording | various | — |

### G. Tests, CI, developer loop

| ID | Sev | Finding | Fix |
|---|---|---|---|
| T1 | H | `make typecheck`/`make ci` red; `uvx` unpinned vs lock | **annotation fixed**; `uv run --no-sync`; ty in CI |
| T2 | H | CI runs ruff + pytest + helm lint only | `CheckFrontend`, `Typecheck`, chart render in `Checks` |
| T3 | H | `k8s.py` 21 % covered; fake ignores label selectors | `tests/test_k8s.py` (10 named tests in auditor report) |
| T4 | H | documented "Level 0 htrflow pin test" doesn't exist | dagger `TestDriver` in the Build container, or drop the claim |
| T5 | M | no contract tests across reconciler↔frontend↔wrapper; fixture drifted (9 vs 15 keys); duplicated literals | golden `status.json` + strict-zod test; env/key contract tests |
| T6 | M | Publish: one component, one arch, immutable `v0.1.0` re-pushed; arm64 recipe manual | matrix; explicit tag; `poc-push-arm64` |
| T7 | M | `gitrepo.py` 41 %, `__main__.py` 70 %, `Bucket.exists/read_text` never via boto | named tests |
| T8 | M | chart lint-only; `lookup`/`fail` untested | `ci/full-values.yaml` + kubeconform + goldens |
| T9 | M | inconsistent pins (uvx, dagger `latest` vs engine 0.20.3, `alpine/helm:latest`…); no Renovate | pin + Renovate |
| T10 | M | dev-loop docs drift | update |
| T11 | L | root ruff red on `scripts/` + `docs/superpowers/plans` fenced code, but nothing runs it | lint scripts; exclude docs |
| T12 | L | no component tests, no eslint/prettier | add |
| T13 | L | fake-fidelity nits (`FakeBucket.exists` vs `stored`), private-attr asserts, wall-clock sleeps | clock injection |
| T14 | L | `docs/superpowers/plans` (7 224 lines, 251 unticked boxes) is scaffolding, not docs | move out of `docs/` or exclude |

---

*Evidence files (screenshots, probe outputs) are in the session scratchpad under `audit/` and `audit-*.png`; the per-auditor reports are summarised in `audit/0N-*.md`.*
