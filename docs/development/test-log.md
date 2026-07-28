# Test Log

This page is a historical record, carried over verbatim from the original
design document's §13 and §14. It documents the exact hosts, paths (`k8s/`,
`docker/`), and image tags in use on those dates — do not "fix" the paths
below to match the current repo layout; see [Getting Started](../getting-started/index.md)
and [Deploy](../getting-started/deploy.md) for the current layout
(`charts/htrflow-batch`, `.docker/`).

## 13. PoC test log — 2026-07-27, bare k3s on dmlpai01

Smoke test of the Phase 1 skeleton with a **miniature wrapper** (real page
downloads, simulated 20 s/page HTR, real S3). Manifests in `k8s/`
(`README.md` there has the replay steps).

**Environment:** bare k3s v1.36 (systemd), Kueue latest (note: `v1beta1`
deprecated → this doc's YAML updated to `v1beta2`), RustFS in-cluster as S3
(NodePort 30900 S3 / 30901 console), pages = Riksarkivet `htr_demo` HF-space
example images (IIIF ids guessed blind 400'd; real ids resolvable via the RA
API when needed — e.g. volume A0068065 verified working with `/full/300,/`;
`!w,h` size syntax returns 501 on lbiiif).

**Proven:**

| Design element | Result |
|---|---|
| Kueue gating (D2, §5.2) | 6 Jobs vs quota 2 → exactly 2 Running / 4 Suspended at all times; three clean FIFO waves; zero custom logic |
| Volume-per-Job lifecycle (D3, §5.3) | suspend→admit→Complete; Job status was the only tracking |
| Streaming per-page upload (D16) | ALTOs landed in S3 ~20 s apart *during* runs (object timestamps) |
| Resume after kill (§9.3c) | 4-page volume, pod force-killed after 2 pages: retry pod logged `resume: 2 pages already done`, processed only 3–4, manifest records `skipped/skipped/ok/ok` |
| Verify gate + completion contract (D8, §5.4) | `manifest.json` written last; no false-complete window across the kill; no duplicate/corrupt objects |
| Output layout (§5.4) | `demo-v0/<vol>/alto/NNNN.xml` + `manifest.json` with `wall_seconds`/`gpu_stall_seconds` |

**Follow-up same day — real htrflow image, CPU-only (`k8s/htr-real-test.yaml`):**
`airiksarkivet/htrflow:v0.2.6-35f48a7` pulled straight from Docker Hub by k3s
and ran **unmodified** under Kueue: models auto-downloaded from HF Hub into a
PVC (`HF_HOME` swap per §5.6), YOLO regions → lines → TrOCR
(`trocr-base-handwritten-hist-swe-2`) on 4 CPUs, 2 real pages (htr_demo
images) → valid ALTO 4.4, verify 2/2, Job Complete. Timing: 797 s HTR
(≈400 s/page on CPU — GPU expected ~2 orders faster), wall 13m22s incl. model
downloads. **Bonus finding for D11:** htrflow already embeds full provenance
in the ALTO `<Processing>` blocks — pipeline steps, model names AND resolved
HF model revisions (commit hashes) — so per-page provenance comes free; the
wrapper's `manifest.json` only needs the volume-level rollup.

**GPU rung (same day):** k3s auto-detected the NVIDIA runtime (RuntimeClass
`nvidia` pre-existing); device plugin v0.19.3 exposed `nvidia.com/gpu: 3`;
ClusterQueue gained a GPU quota. **Finding: the stock image cannot run on
Blackwell** — its torch supports ≤ sm_90, the RTX PRO 6000 is sm_120
(`cuda.is_available()` returns True, kernels then fail). Consequences:
(a) on dev-kuberay the stock image is ada-only, matching the gpu-ada flavor
assumption; (b) Blackwell support = the derived image's job — first real
content of the D2 `htrflow-batch` image is a torch/torchvision swap to cu128
wheels (`.docker/htrflow-batch.dockerfile`).

**GPU end-to-end (same day): PASSED.** Derived image
(`.docker/htrflow-batch.dockerfile` = stock + uv-installed torch 2.11 cu128)
served from an **in-cluster registry** (`k8s/registry.yaml`, NodePort 30500;
push via port-forward to 127.0.0.1:30500 — no docker daemon changes; pulls via
`/etc/rancher/k3s/registries.yaml` mirror mapping, one-time sudo). 7 GB image
pulled in ~40 s. All models on `cuda:0` (Blackwell sm_120): **2 pages in 19 s
(9.7 s/page) vs 399 s/page CPU — 41×**, whole run incl. model load 27 s,
verify 2/2. Image-iteration workflow from here: `docker build` + `docker push`
(only changed layers) — no sudo.

**D16 wrapper smoke — 2026-07-27 — PASSED on the third image (took 3
rounds).** Real `htrflow-batch` image against mocked IIIF (4 `htr_demo`
fixture pages served from a new anonymous-read `htr-fixtures` RustFS
bucket, no live lbiiif dependency — see `k8s/README.md`), Job
`htr-vol-301` (`k8s/job-real-wrapper.yaml` + `k8s/pipeline-demo-v1.yaml`).

- **Round 1 (`v1`) — blocked:** `driver.py::load_pipeline` called
  `Pipeline.from_config(pipeline_path)` with the raw path string instead
  of a parsed YAML dict → `TypeError: string indices must be integers`,
  exit 1. Fixed upstream as commit `7e7b30c` (version-tolerant
  `from_config` with a dict fallback).
- **Round 2 (`v2`) — blocked further in:** pipeline now loaded and GPU
  segmentation/TrOCR ran correctly, but `driver.py` appended the two
  `Export` steps onto `pipeline.steps` via `.append()` *after*
  `Pipeline.__init__` had already wired `parent_pipeline` on the
  constructor-supplied steps, so the appended `Export` steps kept the
  class-default `parent_pipeline = None` → `Export.run()`'s `metadata`
  came back `None` → `TypeError: 'NoneType' object is not iterable` in the
  ALTO/PAGE Jinja2 templates, verify gate correctly failed all 4 pages.
  Fixed upstream as commit `858b1d0` (Export steps now wired the same way
  YAML-built steps are).
- **Round 3 (`v3`) — PASSED end to end.** Job went `Running` → `Complete`
  in ~40s wall-clock (image already resident, no pull wait). Log:
  `4 pages in manifest` → `resume: 0 done, 4 to process` → YOLO
  regions/lines + TrOCR all on `cuda:0` → `COMPLETE 4 pages (4 processed)
  in 31.8s, viewer: http://10.16.51.53:30900/htr-results/demo-v1/mock-vol/iiif.json`.
  `manifest.json`: all 4 results `"status": "ok"` (per-page 2.45–14.4 s,
  the first page paying model-load cost), **`wall_seconds: 31.8`,
  `gpu_stall_seconds: 0.0`, `pages_per_second: 0.126`**,
  `bytes_fetched: 2953047`. `iiif.json` verified: `type: Manifest`, 4
  canvases, canvas 1 `seeAlso` ends `alto/0001.xml`, canvas 1 image body
  id starts with the `htr-fixtures` base (browser-viewable end to end, no
  image service → dims are the real fixture-image dims, 2864×2288 for
  page 1, not the 2000×3000 manifest placeholders or the 2500 width cap —
  confirming the no-image-service fallback path). `alto/0001.xml` served
  `200 application/xml`. **Resume-rerun:** deleted and reapplied the
  identical Job; logged `resume: 4 done, 0 to process`, completed in 8.3 s
  (`COMPLETE 4 pages (0 processed)`, dominated by model-import overhead
  since nothing was actually processed), `manifest.json` on the rerun
  shows all 4 pages `"status": "skipped"`, `bytes_fetched: 0` — idempotent
  re-run confirmed.

**Takeaway:** both round-1/2 bugs were in `driver.py`'s hand-rolled
pipeline construction (config parsing, then post-construction step
wiring) rather than in htrflow itself or in the mocked fixtures/pipeline
YAML — neither was caught by unit tests because `driver.py` keeps all
htrflow imports function-local so the wrapper can import cleanly without
torch, meaning `load_pipeline`'s actual behavior against the real
`htrflow.pipeline.pipeline.Pipeline` was previously untested. Both are now
fixed and this smoke test is the first real coverage of that path.

**Not yet tested:** the D16 library-driver wrapper's Kueue-contention
behavior under >1 concurrent Job on GPU (single-Job smoke only), the
`htrq` CLI, priority lanes (D13), NetworkPolicy (D14) — remain §10 opens.

**Host gotchas fixed en route** (persisted; also in memory notes):
`fs.inotify.max_user_instances=128` was exhausted by root's services → kubelet
silently never registered the node (`/etc/sysctl.d/99-k3s-inotify.conf` now
sets 1024/1048576); `dmlpai01` resolves IPv6-only → `node-ip: 10.16.51.53`
pinned in `/etc/rancher/k3s/config.yaml`.

## 14. PoC test log — 2026-07-28: viewer deployment + cluster incident

**DiskPressure incident (overnight):** the shared 7.4 TB root filesystem
sat at 96 % (350 G free — plenty in absolute terms, largely Docker: 543 G
images + 399 G build cache from many users). kubelet's *default* eviction
threshold is percentage-based (`nodefs.available<10%` = 740 G on this
disk), so it tainted the node `disk-pressure:NoSchedule`, evicted
RustFS/Kueue/registry, and replacements sat Pending ~10 h. Fix (persisted
in `/etc/rancher/k3s/config.yaml`):
`kubelet-arg: [eviction-hard=nodefs.available<25Gi,imagefs.available<25Gi]`
— absolute thresholds; a big-disk shared box can never satisfy 10 %-free.
Data survived (host-path PVCs); evicted-pod husks needed manual delete
before their Deployments respawned them.

**UV4 viewer deployed (D19 closed for the PoC):** Riksarkivet
`universalviewer4` fork built (node 20; `NODE_EXTRA_CA_CERTS=
/etc/ssl/certs/ca-certificates.crt` required — RA firewall TLS
interception breaks npm's bundled CA list with a misleading npm-internal
crash), served as nginx image `uv4:v3` (NodePort 30800,
`k8s/uv4-viewer.yaml`). `/` 302-redirects (relative — nginx
`absolute_redirect off`, else the NodePort is dropped) to
`uv.html#?manifest=…/mock-vol/iiif.json`. Access is via
`ssh -L 30800/-L 30900` (laptop can't route to the node IP), so the demo
`iiif.json` + redirect use `http://localhost:{30900,30800}` URLs — a
PoC-only artifact; production needs a browser-reachable
`PUBLIC_RESULTS_BASE` behind an ingress (D6/D14). End state verified in a
real browser: page images, thumbnails, ALTO text panel, and clickable
per-line outlines on the image, all live. Fork bugs found + patched en
route are listed under D19 in §5.4 (`docker/uv4-uv-html.patch`).

**Wrapper hardening (post-merge, TDD):** `viewer.py` now emits the
search-service stub + per-canvas `thumbnail` itself (previously
hand-patched into the published `iiif.json`); `driver.py` validates the
pipeline YAML up front so only file/parse errors map to exit 13 while an
`OSError` out of `from_config`'s HF model downloads stays transient
(exit 1) — closes the final-review parked finding; `podFailurePolicy`
`FailJob`-on-13 (§5.3) is now safe to wire. 47 wrapper tests green.
NOTE: the `htrflow-batch:v3` **image predates these wrapper changes** —
rebuild/push before the next cluster run.

---

*Editorial note (not part of the original §14 text): the wrapper-hardening
paragraph above landed in commit `af8df6a` ("Harden wrapper + sync design
doc"), which is also where §14 itself was first appended to the design
document. See [The Wrapper](../how-it-works/wrapper.md#job-template-one-volume-one-job)
for where that fix is referenced going forward (the `podFailurePolicy`
`FailJob`-on-13 wiring it unblocked).*
