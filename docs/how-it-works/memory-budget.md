# Memory Budget

tmpfs is accounted memory: tmpfs pages count against the container memory
limit (cgroup), and overrun is an **OOMKill**, not a polite eviction.

The D16 streaming loop makes the budget **independent of volume size**: tmpfs
holds at most the lookahead window (uploader rolling-deletes processed images).

| Item | Budget |
|---|---|
| torch + models resident | ~6–8 Gi |
| page images in flight (`LOOKAHEAD_PAGES=64` × ~2 MB @ width 2500) | ~128 Mi |
| outputs awaiting upload (XML) | noise |
| run-log buffer | ≤ 4 MiB (capped in `logship.py`) |
| tmpfs `sizeLimit` | 2 Gi (generous) |
| pod memory **request** | 8 Gi (`jobspec.py`; what Kueue's quota must cover) |
| pod memory **limit** | 16 Gi (what tmpfs and the OOM killer see) |

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
- A 1000-page volume can no longer OOMKill a pod — the old preflight
  size-guard reduces to sanity checks (non-empty manifest → else exit 13).
- Disk escape hatch: the wrapper only sees `WORKDIR_PATH`; swapping the
  tmpfs `emptyDir` for a disk-backed one is a Job-manifest change, no
  wrapper flag involved. Should never be needed now.
