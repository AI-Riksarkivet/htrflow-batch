# Wrapper → Kubernetes layer: what to move (2026-09-02)

Handoff for an implementing agent. Companion to
`2026-09-02-wrapper-audit.md` and `2026-09-02-wrapper-simplifications.md`.
Against HEAD `9e074e0` on `b63-indexed`. Scope: the wrapper
(`packages/wrapper/src/htrflow_batch/`), the Job templates the converter
renders (`packages/converter/src/htrflow_converter/manifests/campaign-job.yaml`,
`warmup-job.yaml`) and `render.py`, and the read API's pod projection
(`packages/api/src/htrflow_api/projection.py`).

Ground rules: one item per commit, subject ends in `(B63)`, no
Co-Authored-By trailer, `uv` never pip, work in this worktree. The
converter has a hard LOC budget (see `docs/features/` stories); every
template change here is a net deletion or a one-liner. Regenerate the
golden file `packages/converter/tests/golden/kyrk.job.yaml` when the
template changes and read the diff before committing it.

## Already delegated — keep as is

Index → volume line (`sh -c` prologue in the Job), the warm-up gate
(`warmup-wait` init container on `/data/warmup/<pipeline>.done`), exit 13 →
`FailIndex` (`podFailurePolicy`), `DisruptionTarget` → `Ignore`,
`terminationGracePeriodSeconds: 120`, credentials as a mounted file,
`HF_HUB_OFFLINE=1` against a read-only cache, the 2 Gi memory emptyDir.

## Must stay in the wrapper

Resume by S3 listing, per-page fetch retries (a pod retry costs a model
reload and the whole volume), the verify gate, URL redaction, the SIGTERM
handler with `os._exit` (the download pool would otherwise block interpreter
shutdown), and run-log shipping (see item 4).

---

## 1. MAX_SECONDS watchdog → pod `activeDeadlineSeconds` (do this)

**Today.** `main._start_max_seconds_timer` runs a daemon `threading.Timer`;
on expiry `on_expiry` races the main thread through `RunState.terminating`,
writes `{"error": "MAX_SECONDS"}`, ships the log and calls `_hard_exit(1)`
from the timer thread. The converter sets the value per pipeline:
`render.py:164` → `"MAX_SECONDS": str(p.max_seconds or cfg.max_seconds)`
(golden: 21600).

**Move.** Render the same number into the pod spec as
`spec.template.spec.activeDeadlineSeconds` and stop rendering the env var.
At the deadline the kubelet sends SIGTERM; the existing handler writes the
termination log (`"error": "SIGTERM"`), ships the run log and exits 143, and
the index is retried like any transient failure (`backoffLimitPerIndex`).

**Delete from the wrapper** (`main.py`):
`_start_max_seconds_timer`, `on_expiry`, the `timer` variable and its
`finally: timer.cancel()`, `Config.max_seconds` and its env parsing, and
`RunState.terminating` with every `acquire(blocking=False)` on it — with the
timer gone, SIGTERM is raised in the main thread and nothing races. Keep
`_terminate` (still writes the structured message) and `_hard_exit` (still
needed for SIGTERM). Delete the MAX_SECONDS tests in `test_main.py` and the
`MAX_SECONDS` rows in `docs/reference/wrapper.md` and
`docs/how-it-works/failure-handling.md`; add a line saying the per-volume
budget is the pod deadline.

**Verify on the cluster first (blocking).**
1. Whether a pod killed by `activeDeadlineSeconds` carries a
   `DisruptionTarget` condition. If it does, the Job's existing
   `Ignore onPodConditions DisruptionTarget` rule would not count the
   failure and the index would retry forever. In that case add a
   `FailJob`/count rule keyed on the exit code 143, or narrow the Ignore
   rule — do not ship item 1 without settling this.
2. That the read API shows the reason. `projection._wrapper_message` returns
   the container's termination message; the pod also gets
   `status.reason: DeadlineExceeded`. Surface that when the message says
   `SIGTERM`, so the card still distinguishes "budget exceeded" from "node
   drained".

**Test.** Converter: the rendered Job carries `activeDeadlineSeconds` equal
to the pipeline's `max_seconds` and no `MAX_SECONDS` env. Wrapper: existing
SIGTERM tests cover the runtime path.

## 2. Writable-dir creation → the Job's shell prologue (small, do this)

**Today.** `main.prepare_writable_dirs` / `WRITABLE_DIR_VARS` mkdir `HOME`,
`TMPDIR`, `YOLO_CONFIG_DIR` at start, called from both `main._main` and
`warmup.main`, because the root filesystem is read-only. The Job sets them to
`/work/home`, `/work/tmp`, `/work/ultralytics` (`campaign-job.yaml:88–93`).

**Move.** Add `mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR"` to the
`sh -c` prologue in `campaign-job.yaml` (before the `exec`) and to the
warm-up Job's command in `warmup-job.yaml`. Delete `prepare_writable_dirs`,
`WRITABLE_DIR_VARS`, both call sites, the `HF_HOME` mkdir in `warmup.main`
if the warm-up Job mounts it read-write already, and their tests.

The alternative — mounting the `work` emptyDir three more times with
`subPath` — also works but adds mounts for no gain; use the prologue.

**Test.** Converter golden diff shows the added line; wrapper tests that
asserted the directories exist go away.

## 3. Workdir cleanup → nothing (trivial, do this)

`main.py` calls `shutil.rmtree(cfg.workdir, ignore_errors=True)` on success
and comments that on failure the workdir is left "for postmortem
inspection". Neither does anything under Kubernetes: the memory-backed
emptyDir dies with the pod and a terminated container's tmpfs cannot be
inspected. Delete the call, the comment and the `shutil` import. If any test
asserts the workdir is gone after success, delete that assertion.

## 4. Run-log shipping → keep in-process (decided: do not move)

Options considered: a native sidecar (`initContainers[].restartPolicy:
Always`, k8s ≥ 1.29) tailing a shared file to S3, or the read API proxying
pod logs. Both remove `logship.py` on paper, but the wrapper would still
have to write the file and redact it, S3 persistence after the Job's
`ttlSecondsAfterFinished` (one day) is part of the live-run-log contract,
and a sidecar adds an image, an S3 credentials mount and lifecycle
ordering. Not worth it.

## 5. Termination-message JSON → `FallbackToLogsOnError` (optional, UX call)

`_terminate` writes `{"stage","permanent","error"}` to
`/dev/termination-log`. Nothing parses `permanent` (the exit code carries
it); `projection._wrapper_message` returns the raw message and
`CampaignCard.svelte` displays it. Setting
`terminationMessagePolicy: FallbackToLogsOnError` on the container would
let `_terminate` and `TERMINATION_LOG_PATH` go, at the price of the card
showing the last log lines (up to 2 KiB / 80 lines) instead of one clean
sentence. Only do this if that UX change is wanted; otherwise leave it.

---

## Checked, not an issue

The template's `MAX_SECONDS`, `MANIFEST_MAX_BYTES` and `FETCH_MAX_BYTES`
read `"0"` in the raw YAML; `render.py:164–166` fills them from the
converter config (golden: 21600 / 16777216 / 67108864). No Job ships a
zero cap.

## Verification after every commit

```bash
cd /home/morgan/htrflow-batch/.worktrees/b63-indexed
uv run --all-packages pytest -q packages/wrapper packages/converter
make typecheck
uv run --no-sync ruff check packages/wrapper packages/converter
make helm-template   # if the chart embeds the Job template
```

Item 1 additionally needs the cluster check above before it is merged;
record the outcome (the `DisruptionTarget` answer and the k8s version) in
the commit body.
