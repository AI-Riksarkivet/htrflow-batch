# Memory Budget

tmpfs is accounted memory: tmpfs pages count against the container memory
limit (cgroup), and overrun is an **OOMKill**, not a polite eviction — exit
137, no termination message, no final log ship, and a viewer that polls
forever. An `emptyDir` eviction is no better: it carries `DisruptionTarget`,
which `campaign-job.yaml`'s `Ignore` rule swallows, so the attempt goes
uncounted and the index is retried holding a GPU each time.

## What used to grow with the volume

Two things did, and both are fixed (audit
[X2](../audits/2026-09-07-repo-audit.md)):

- **Page outputs stayed in the workdir.** `stream.consume` deleted the
  downloaded image and nothing else, so every page's ALTO and PAGE XML stayed
  in the memory-backed workdir until the pod died. Measured: 20 pages left
  0 images and **40 output files / 8 000 280 B — ~300 KB per page**, which
  reaches the 2 Gi `sizeLimit` near **7 000 pages**, well inside the 6 h
  deadline. `consume` now unlinks both formats with the image as soon as the
  page's outcome is recorded (`stream.discard`), and a page that *fails* —
  one Export written, the other not — discards what it wrote before raising
  (`driver.process_page`), since `consume` can only reach the files a page
  returns.
- **htrflow kept every `Document`.** `htrflow.progress` holds module-global
  `_tasks`/`_exports`/`_steps` keyed by `Document` (which has no `__eq__`, so
  every page is a distinct key) plus a rich task each, and pops none of them.
  htrflow's CLI runs one process per volume and never notices; the wrapper
  runs one long-lived `Pipeline` over the whole volume, so each page's Region
  tree stayed reachable — ~0.5 GB at 10 000 pages. How many objects a page
  registers depends on its steps: `Segmentation`, `TextRecognition`, `Export`
  and the ordering steps all return the document they were given, so the
  shipped `demo-v1` pipeline (Segmentation + TextRecognition) registers
  **one**, while every `ProcessImages` step — `Binarization` is htrflow's
  only one today — returns a *new* `Document`, so a Binarization pipeline
  registers **two** (measured: two `_tasks` entries and two rich tasks per
  page) and two of them would register three. `driver.release_documents`
  therefore empties the registries outright once the page is done rather than
  naming objects it cannot enumerate — sound because this process has one
  caller of htrflow and one page in flight at a time.

So the footprint is now flat in pages: **tmpfs holds the lookahead window's
images plus the in-flight page's two XML files**, and RSS does not track how
far into the volume the run is. Nothing downstream depends on a file staying
local: resume and verify have always listed S3, and the only thing publish
needed from the ALTO — the page's WIDTH/HEIGHT for `iiif.json` — is taken
off the parse `store.upload_page` does before its first PUT, so a full
volume publishes without reading a single ALTO back (`store.page_dims`).
The `get_bytes` fallback in `publish.alto_dims` remains for exactly the
pages a *previous* run published, which this run never saw.

| Item | Budget |
|---|---|
| torch + models resident | ~6–8 Gi |
| page images in flight (`LOOKAHEAD_PAGES=64` × ~2 MB @ width 2500) | ~128 Mi |
| outputs awaiting upload (XML) | one page, ~300 KB |
| the source manifest and its `PageRef` list | ≤ `MANIFEST_MAX_BYTES` (16 MiB) |
| per-page outcomes (`StreamStats.results`) and dims (`store.page_dims`) | ~250 B × pages (~2.5 MB at 10 000) |
| run-log buffer | ≤ 4 MiB (capped in `logship.py`) |
| tmpfs `sizeLimit` | 2 Gi (generous) |
| pod memory **request** | 8 Gi (`manifests/campaign-job.yaml`; what Kueue's quota must cover) |
| pod memory **limit** | 16 Gi (what tmpfs and the OOM killer see) |

Those two rows are the only wrapper state still proportional to page count,
and both are bounded: the manifest the `PageRef`s point into cannot exceed
`MANIFEST_MAX_BYTES`, and the outcome-plus-dims records are a few megabytes
at volumes larger than any we run. Neither is within an order of magnitude
of the 2 Gi tmpfs or the 16 Gi limit.

- Width capping is **mandatory, enforced by the wrapper** for canvases with
  an IIIF image service — uncapped 6000 px masters (~15–20 MB each) would
  still fit the window, but waste IIIF bandwidth and slow the fetch path for
  nothing HTR can use.
- **Service-less canvases** (synthetic `images:` volumes, static painting
  bodies) cannot be downscaled server-side: they are fetched at native size
  and htrflow processes the full-resolution image. The only bound is
  `FETCH_MAX_BYTES` (64 MiB per image by default); keep such image lists
  pre-sized.
- `MANIFEST_MAX_BYTES` (`converter.yaml`'s `manifest_max_bytes`, 16 MiB
  default) bounds the manifest fetch the wrapper itself does — there is no
  separate pre-validation fetch any more, so this is the only place the cap
  applies.
- Disk escape hatch: the wrapper only sees `WORKDIR_PATH`; swapping the
  tmpfs `emptyDir` for a disk-backed one is a Job-manifest change, no
  wrapper flag involved. Should never be needed now.

**Still to confirm on the cluster:** the numbers above are measured in tests
(`test_workdir_holds_only_the_page_in_flight`, and
`test_progress_registries_are_empty_after_every_page` inside the wrapper
image), not on a long live run. B73's remaining acceptance box is a
**2 000-page volume showing constant tmpfs usage and constant RSS**.
