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
| tmpfs `sizeLimit` | 2 Gi (generous) |
| pod memory request/limit | 16 Gi |

- Width capping is **mandatory, enforced by the wrapper** — uncapped 6000 px
  masters (~15–20 MB each) would still fit the window, but waste IIIF
  bandwidth and slow the fetch path for nothing HTR can use.
- A 1000-page volume can no longer OOMKill a pod — the old preflight
  size-guard reduces to sanity checks (non-empty manifest → else exit 13).
- Disk escape hatch: the wrapper only sees `WORKDIR_PATH`; swapping the
  tmpfs `emptyDir` for a disk-backed one is a Job-manifest change, no
  wrapper flag involved. Should never be needed now.
