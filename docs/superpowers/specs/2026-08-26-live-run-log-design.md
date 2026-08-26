# Live run log — design

**Date:** 2026-08-26 · **Status:** approved (Morgan: "1 is fine")

## Problem

The run log only becomes visible after a volume finishes: the reconciler
copies a 500-line tail of the pod log to `status/logs/<pipeline>/<volume>.txt`
once the Job succeeds. While a 480-page volume runs for an hour and a half
there is nothing to look at from the frontend, and what arrives afterwards
is truncated.

## Options considered

1. **Wrapper ships its own log to S3 while running** — chosen.
2. Log proxy service streaming `pods/log` through the viewer nginx (SSE).
   Real-time, but a new Deployment + RBAC + NetworkPolicy + nginx route,
   exposes kube-API-derived data unauthenticated, and only works where the
   viewer has cluster reach.
3. Reconciler uploads the running pod's tail every tick. Latency = tick
   interval (minutes); pointless if 1 exists.

Option 1 keeps the architecture's invariant: the browser only ever reads
S3, the reconciler is the only kube-API client, and the same path works
against real AWS S3 in production.

## Design

### Wrapper (`htrflow_batch.logship`)

- `LogCapture.install()` replaces `sys.stdout`/`sys.stderr` with tees that
  write through to the originals (so `kubectl logs` is unchanged) and append
  to one in-memory buffer, in arrival order. Installed **before**
  `logging.basicConfig`, so the root `StreamHandler` binds the tee — htrflow's
  own `logging` output and its bare `print`s both land in the buffer.
- Once the `ResultStore` exists, `capture.start_shipping(upload, interval)`
  starts a daemon thread that uploads the buffer to
  `status/logs/<pipeline_id>/<volume_ref>.txt` (bucket root, same key the
  reconciler uses) every `interval` seconds **when it changed**. Upload
  errors are logged once and retried next interval; they never fail the run.
- `capture.finish()` (in a `finally`) does a last upload and restores the
  streams. The final object is therefore the complete log, not a tail.
- Buffer cap 4 MiB: beyond it the middle is dropped with a marker line
  (head 1 MiB + tail 3 MiB), so a pathological run cannot grow memory or
  upload size without bound.
- `LOG_SHIP_SECONDS` env (default 15, `0` disables) → `Config.log_ship_seconds`.
- A retry overwrites the key with the new attempt's log; the failed attempt's
  evidence stays in `status/failures/…` as before.

### Reconciler

`run_log` is emitted whenever `status/logs/<pid>/<vid>.txt` exists, for any
status that can have produced one (`done`, `running`, `retry`,
`needs-attention`, `queued`), not only `done`. The post-completion kube-API
upload stays as the fallback for pipelines whose image predates the shipper
(`done` + succeeded Job + no key). Cost: one HEAD per such volume per tick,
the same as today for done volumes.

### Frontend

- Campaign table: the `log` link already renders whenever `run_log` is set.
  For volumes that are not `done` it adds `&live=1`.
- Run viewer in live mode: re-fetches the log every 15 s, shows a
  "● live · updated HH:MM:SS" badge, keeps the view pinned to the bottom
  while the user is at the bottom, and stops polling once the log carries a
  terminal wrapper line (`COMPLETE`, `permanent failure in`, `transient
  failure in`) — `isTerminalLog(text)` in `runlog.ts`, unit-tested.
- The summary card appears automatically when `manifest.json` lands (the
  manifest fetch is retried on the same cadence while live).

### Out of scope

- Per-attempt log history.
- Streaming below 15 s latency (would need option 2).
