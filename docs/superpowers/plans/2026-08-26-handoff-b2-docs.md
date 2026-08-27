# Handoff — B2 docs (audit remediation 2026-08-26)

Branch: this worktree's branch off `feat/campaign-browser-visibility` at
`bc06f25` (A1 `9b36920`, A2 `ade2b14`, A3 `4399f19`, A4 `8e6f661` merged).
Scope touched: `docs/**`, `README.md`, `charts/htrflow-batch/README.md`,
`zensical.toml`, and the two stale docstrings in
`packages/reconciler/src/htrflow_reconciler/{k8s,jobspec}.py` (D-L8).
Not touched: `charts/htrflow-batch/values.yaml` (its Secret comment was
already correct after A3 — D-H1 needed no change there),
`frontend/README.md` (written by A4, current),
`docs/audits/2026-08-26-hardcoded-inventory.md`, `docs/development/test-log.md`
(a dated record), `docs/superpowers/specs/*`.

Verification: `uvx zensical build --clean` — 0 issues; a relative-link +
anchor checker over all 47 pages — 0 broken; `uv run --all-packages pytest
-q` = 375 passed (151 wrapper + 224 reconciler); `cd frontend && bun run
test` = 76 passed in 10 files; `ruff format --check` / `ruff check` clean
on the two edited source files.

## Pages rewritten or added

| Page | Findings |
|---|---|
| `reference/chart.md` | D-H1, D-M1, D-M2, D-M6, O12 — every value table incl. `network.*`, `modelCache.*`, `job.*`, `security.*`, devStack, reconciler, `viewer.statusBase`/`securityHeaders` |
| `reference/reconciler.md` | D-H1, D-M6 — every `RECONCILER_*` env with defaults from `__main__.py`, `deleting`, sticky verdict, the tick incl. Lease / warm-up gate / bounded validation / `volumes.json` / `tick_summary` |
| `how-it-works/failure-handling.md` | D-H2, X5 — rewritten around the reconciler |
| `how-it-works/wrapper.md` | D-H2, D-H3, D-L1, D-L4, D-L6, D-L7 — Job template as built, `htrq` section removed, illustrative-Kueue label, RustFS caveat, stages incl. `load`, exit 143 |
| `getting-started/run-a-volume.md` | D-H2, D-M5 — env table linked to the reference, real Job contract |
| `how-it-works/decision-log.md`, `architecture.md`, `roadmap/evolution.md`, `roadmap/open-items.md`, `index.md`, `roadmap/phase-2-cache.md` | D-H3, D-M7 — D7/D18 superseded, D20 added, `htrq` moved to a proposal, D9–D12/D14/reconciler marked built |
| `development/testing.md`, `development/index.md`, `development/ci.md` | D-M9, D-L5, D-L10, T4, T10 — real counts, level-0 as planned opt-in, B1 items marked planned |
| `charts/htrflow-batch/README.md`, `README.md`, `getting-started/deploy.md`, `development/deployment.md` | D-H4, D-L5, O12, A3 §6 |
| `reference/s3-layout.md`, `reference/frontend.md`, `reference/wrapper.md`, `reference/campaign-yaml.md`, `how-it-works/memory-budget.md`, `development/security.md` | D-M3, D-M4, D-M5, D-M8, D-M10, D-M11, D-M12, D-L2, D-L3, D-L10, F16, X12 (trust boundary as a section of security.md) |
| `how-it-works/campaigns.md`, `getting-started/campaigns.md` | D-M2, D-M4, D-M11, D-L2, D-L9, A1/A4 handoff lists |
| **new** `how-it-works/live-run-log.md` | D-M10, X12 |
| **new** `development/local-k3s.md` | D-M12, X12 |
| `audits/2026-08-26-repo-audit.md` | remediation preface only |

## Found in code that contradicts the plan / specs (not fixed — B2 does not edit code)

1. **Live-log tail size.** The spec (`2026-08-26-live-run-log-design.md`)
   says 1 MiB head + 3 MiB tail; `logship.py` has `TAIL_BYTES = 2 MiB`
   (`CAP_BYTES` 4, `HEAD_BYTES` 1). Documented as built.
2. **`isTerminalLog` does not know the SIGTERM line.** `runlog.ts` matches
   `] COMPLETE \d+ pages` and `(permanent|transient) failure in \w+:`; the
   wrapper's SIGTERM path logs `SIGTERM in stage <stage>: shutting down`.
   A killed attempt's live view keeps polling until the retry replaces the
   log or 20 polls miss. One-line frontend fix if wanted.
3. **`/log` route URL rule.** The plan (X13) says "same-origin/prefix URLs
   only"; the build accepts any absolute `http(s)` URL (`isHttpUrl`).
   Documented as built.
4. **Pre-validation verdict naming.** The reconciler's `fetch_json` maps a
   permanent 4xx to `SourceRejected("unreachable")` with `permanent: true`
   (cached forever) and non-JSON/over-cap/non-http(s) to `unsupported`.
   So the page can show `unreachable` for a volume that will never be
   re-probed; the chip's hover text in the frontend still says re-probed.
   Documented; a `gone`/`forbidden` verdict would be clearer.
5. **`unreachable_ticks`** (3) is a `ReconcilerConfig` field with no env or
   chart value (A1 noted this). Documented as "not an env var".
6. **B1 collision:** `packages/reconciler/tests/test_k8s.py` already exists
   on the branch (A1's Lease tests). B1's planned `test_k8s.py` must extend
   it, not add a second file.
7. **Names B1 should keep or fix in `ci.md`:** I documented the planned
   dagger functions as `check-frontend`, `typecheck`, `test-driver` and the
   make target `test-driver-real`; `build-reconciler` already exists. If B1
   lands different names, `development/ci.md` and `testing.md` need the
   one-word edits.
8. **`warmup.md`/`values.yaml` still say a chart-declared pipeline is warmed
   by `make warmup`** — true, and the two docstrings that said the chart
   renders the Job are fixed. The chart still renders no warm-up Job; if that
   is ever wanted, `templates/pipelines.yaml` is the place.
9. **PoC host addresses.** `getting-started/index.md` and `viewing.md` still
   carry the first PoC host's `10.16.51.53`; the current GB10 node is a
   different address. Left as the dated host notes they are; `local-k3s.md`
   uses `<node-ip>`.
10. **`test-log.md` §"torch 2.11 cu128"** is stale per the A2 handoff (the
    cu128 index has no cp310 wheel past 2.9.1). Left untouched as a dated
    log; the dockerfile header is the truth.
11. **`source_manifest` in `manifest.json`** is written verbatim (A2 flagged
    it); `status.json` publishes the campaign's URL too. If the S6 policy
    becomes "no query strings anywhere public", redact both together.

## Open for the operator (documented, not resolvable in docs)

- Two S3 principals (`security.md#trust-boundary`, `open-items.md`).
- Durable results bucket for anything past the PoC (D6).
- `security.allowedImageRepos` is empty on the live values — the reconciler
  warns every tick until it is set.
- A purpose-built git-daemon image so `security.psaEnforce` can be
  `restricted`.
