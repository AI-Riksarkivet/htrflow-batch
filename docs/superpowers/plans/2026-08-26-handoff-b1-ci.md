# HANDOFF — B1 CI / tests / dev loop (audit remediation 2026-08-26)

Branch: this worktree's branch off `feat/campaign-browser-visibility` (53c2be4).
Scope touched: `.dagger/**`, `.github/workflows/**`, `pyproject.toml` (root +
wrapper), `renovate.json` (new), `Makefile` (`format`/`lint`/`typecheck`/`ci`
and the new `test-driver-real`), `packages/*/tests/**`. Nothing under
`frontend/`, `docs/` (besides this file), `charts/` or `.docker/` was edited;
everything another package must do is listed here.

Verification on the last commit: `uv run --all-packages pytest -q` → 466
passed, 1 skipped (was 375); `ruff format --check .`, `ruff check .`,
`ty check packages/*/src` clean; `helm lint` on defaults and
`ci/full-values.yaml`; `dagger functions` lists the new functions;
`dagger call checks` (lint → typecheck → check-frontend → check-chart) passes
end to end on this host; `dagger call scan-reconciler --severity CRITICAL`
runs and is **red** (see §2); `make test-driver-real IMAGE_TAG=live-v3`
→ 5 passed in the native arm64 wrapper image.

## 1. What CI now is

| Gate | Where | Runs |
|---|---|---|
| `lint` | dagger `Lint` | `uv run --no-sync ruff format --check .` + `ruff check .` (locked ruff 0.16.0; `scripts/` included, `docs/` excluded by the root `[tool.ruff]`) |
| `typecheck` | dagger `Typecheck` + `make typecheck` | `uv run --no-sync ty check packages/wrapper/src packages/reconciler/src` |
| `check-frontend` | dagger `CheckFrontend` | `bun install --frozen-lockfile`, `bun run check`, `bun run test`, `bun run build` — node image + bun binary, both digest-pinned (see §3 for why node) |
| `check-chart` | dagger `CheckChart` | `helm lint` + `helm template` on defaults and `ci/full-values.yaml`, `kubeconform -strict -ignore-missing-schemas -summary` over both renders (49 resources, 7 CRD kinds skipped) |
| `checks` | ci.yml job `ci`, `make ci` | all four above |
| `test` | ci.yml job `ci`, `make ci` | workspace pytest |
| `scan-reconciler --severity CRITICAL` | ci.yml job `scan`, every run | Trivy 0.65.0 by digest, `--ignore-unfixed` (default; `--ignore-unfixed=false` gates on `will_not_fix` too) |
| `scan --severity CRITICAL` | ci.yml job `scan`, pushes to main + dispatch only | same, over the ~10 GB wrapper image |
| `test-driver` | opt-in (`dagger call test-driver`) | T4 Level 0 htrflow pin inside the image `Build` produces; `make test-driver-real` is the local twin |
| `publish-docker` | publish.yml, `workflow_dispatch` | matrix wrapper/reconciler/viewer, explicit `tag` input, refuses an existing tag, `HTRFLOW_BASE_REVISION` via `base_revision`, cosign keyless + build-provenance + Trivy-SPDX SBOM attestations (all actions SHA-pinned) |

`dagger-for-github` is pinned to `0.20.3` (= `engineVersion` in `dagger.json`);
Renovate keeps the two equal. Every image the dagger module pulls is
`tag@sha256` in the const block at the top of `.dagger/main.go`.

## 2. Open findings the gates surfaced (for A1/A2/A3 — out of my scope)

- **Reconciler image has 4 fixable CRITICALs** and the `scan` job in ci.yml
  is red until they are fixed: `libgnutls30` CVE-2026-33845 and
  CVE-2026-42010 (fix `3.7.9-2+deb12u7`), `libssl3`/`openssl` CVE-2026-31789
  (fix `3.0.19-1~deb12u2`). Fix in `.docker/htrflow-reconciler.dockerfile`:
  `apt-get update && apt-get upgrade -y --no-install-recommends && apt-get
  install -y --no-install-recommends ca-certificates`, and pin
  `ghcr.io/astral-sh/uv:python3.13-bookworm-slim` by digest (it is the one
  unpinned `FROM` left; Renovate will then track it).
- **Wrapper scan on GitHub-hosted runners:** the amd64 wrapper build pulls
  the 10 GB htrflow base plus cu128 torch; `ubuntu-latest` has ~14 GB free.
  The step is limited to main pushes/dispatch; if it fails on disk, it needs a
  larger or self-hosted runner, or an `actions/checkout`-time `docker system
  prune`. Not verified here (arm64 host cannot build the amd64 recipe).
- **`security.verifyImages.subject` in `charts/htrflow-batch/ci/full-values.yaml`
  (and the README example) is
  `…/.github/workflows/publish.yml@refs/tags/*`.** publish.yml is
  `workflow_dispatch`, so the Sigstore certificate identity of a real run is
  `https://github.com/Riksarkivet/htrflow-batch/.github/workflows/publish.yml@refs/heads/<branch>`
  (whatever ref the dispatch ran on). Either set the subject to
  `@refs/heads/main` (and dispatch only from main) or trigger publish.yml on
  `push: tags: [v*]` instead. A3/B2 decision; verified nothing against a
  Kyverno install.
- `make scan-reconciler` (Makefile, A3) and dagger `scan-reconciler` now agree
  on `--ignore-unfixed`.

## 3. Frontend half of T5 (for the coordinator / A4) — contract test spec

The reconciler's half is committed: `packages/reconciler/tests/fixtures/status.golden.json`
is one tick's `status.json` over a fixture that emits **every** volume
status (`done running queued retry needs-attention pending unreachable
unsupported deleting`), the three `terminal` variants (`exit-13`, `capped`,
sticky `capped` with no Job), an `images:` volume (synthetic manifest,
`thumbnail: null`), an orphan, a broken campaign (`error` set, no volumes) and
a campaign on an unknown pipeline. Regenerate with `UPDATE_GOLDEN=1 uv run
--all-packages pytest packages/reconciler/tests/test_status_golden.py`.

Add `frontend/src/lib/status.contract.test.ts` (vitest):

1. `import golden from "../../../packages/reconciler/tests/fixtures/status.golden.json"`
   (vite resolves JSON; `resolveJsonModule` is on in the SvelteKit tsconfig).
2. Build a **strict** twin of the schemas for the test only —
   `statusDocSchema` with every `z.object` replaced by `.strict()` (a small
   helper that walks `volumeEntrySchema`, `campaignEntrySchema`,
   `statusDocSchema.shape.totals`, `tick_summary`), so an unknown key in the
   golden fails the test. `safeParse(golden).success === true` is the
   assertion; print `error.issues` on failure.
3. Coverage both ways: `new Set(golden.campaigns.flatMap(c => c.volumes.map(v => v.status)))`
   must equal `new Set(volumeStatusSchema.options)` — the enum and the
   reconciler agree on the status vocabulary. Keep `catch("unknown")` on the
   parse path (plan contract), but the *strict* test must not rely on it.
4. Field-level: `terminal` is `"exit-13" | "capped" | null` on every volume;
   top-level `tick_summary` has `seconds s3_calls validations submitted
   retried` (numbers); `thumbnail` is `null` for the `vol-images` and
   `vol-done` rows (a done volume with no earlier validation carries no
   thumbnail — render the placeholder); `deleting` rows have a
   `run_manifest: null`.
5. The golden replaces the drifted dev fixture: point
   `frontend/static/status.sample.json` at the same content (copy it in the
   test setup, or make the dev server serve the golden), so the fixture cannot
   drift again (the audit's 9-vs-15-keys problem).

What the golden already shows the current `status.ts` is missing: `terminal`,
`tick_summary`, status `deleting` (all in the plan's contract; A4 is adding
them). Until they land the strict test is red — that is the point.

Two literals are now pinned on the reconciler side and must move together:

- `frontend/src/lib/runlog.ts` `TERMINAL_RE` is copied verbatim into
  `packages/reconciler/tests/test_wrapper_contract.py::FRONTEND_TERMINAL_RE`
  and tested against the wrapper's real terminal lines. Change one, change
  both.
- **Gap found while pinning it:** the wrapper's SIGTERM path
  (`packages/wrapper/src/htrflow_batch/main.py`) logs
  `SIGTERM in stage <stage>: shutting down` and exits 143 — that line does
  **not** match `TERMINAL_RE`, so the browser keeps polling a SIGTERM'd run
  log until the next tick retires it. Suggest
  `/\] COMPLETE \d+ pages|(permanent|transient) failure in \w+:|SIGTERM in stage \w+:/`
  (then update the reconciler literal and add the SIGTERM case there).

Runtime note: `bun run test` executes vitest under **node** when node is on
PATH (every developer host) and under bun otherwise; under the bun runtime
`status.test.ts` fails with `undefined is not an object (evaluating
'z.enum')` (zod 3.25's named `z` export through vite-node). CI therefore runs
the frontend in `node:20-bookworm` with the bun binary copied from
`oven/bun:1.3.14`. If the frontend ever wants bun-native tests, that import
shape is the thing to fix.

## 4. For B2 (docs)

- `docs/development/ci.md`: replace the "ruff + helm lint + pytest" story with
  §1; `version: 0.20.3` = engine pin; the scan job and its main-only wrapper
  half; Renovate (weekly grouped PRs: container images, github actions,
  dagger engine, python dev tools, python runtime deps, frontend deps;
  lockfile maintenance on; htrflow base tag and cu128 torch pins excluded on
  purpose).
- Publishing: `gh workflow run publish.yml -f tag=v0.2.0 -f
  base_revision="$(git -C ~/htrflow describe --tags --always --dirty)"`; tags
  are immutable (the run refuses an existing one; bump
  `packages/wrapper/pyproject.toml` version = tag); verify with
  `cosign verify docker.io/riksarkivet/htrflow-batch@sha256:… --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp '^https://github.com/Riksarkivet/htrflow-batch/'`
  and `gh attestation verify oci://docker.io/riksarkivet/htrflow-batch@sha256:… --owner Riksarkivet`
  (provenance and SBOM). The native arm64 GPU wrapper stays `make
  poc-push-arm64` (manual; the base only exists on the GB10 node).
- `docs/development/testing.md`: the Level 0 htrflow pin test now exists
  (`packages/wrapper/tests/test_driver_real.py`, marker `htrflow`, `make
  test-driver-real IMAGE_TAG=<tag>` / `dagger call test-driver`; skipped in
  the normal suite); the golden and `UPDATE_GOLDEN=1`; the contract tests
  (`test_wrapper_contract.py` imports the wrapper package — run from the
  workspace root); `test_k8s.py` stubs return real `kubernetes.client`
  models and honour label selectors; counts 375 → 466 + 1 skipped.
- `docs/development/index.md`: `make format`/`lint` are `uv run --no-sync
  ruff … .` (no `uvx`); `make typecheck` is one command; `make check` covers
  `scripts/`.
- T14: zensical 0.0.57 has **no** `exclude`/`not_in_nav`/`draft` option
  (only per-page `search: exclude` front matter), so
  `docs/superpowers/plans/**` is still built into the site. Options are B2's:
  move `docs/superpowers/` out of `docs/` (e.g. `plans/` at the repo root)
  or accept it. Nothing was deleted.
- `docs/development/test-log.md` line "torch 2.11 cu128" is stale (A2 note),
  still true.

## 5. Deliberately not done (with reasons)

- **T5 frontend half** — `frontend/**` is A4's; spec in §3.
- **T8 chart template goldens** — the chart is A3's and moving; render +
  kubeconform on both value sets is in CI, goldens would churn every A3
  commit. Add once the chart settles (`charts/htrflow-batch/ci/golden/`).
- **T10, T12** — docs (B2) and frontend component tests/eslint (A4).
- **`dagger call test-driver` not executed** — it builds the amd64 wrapper
  image (10 GB base under qemu on this arm64 host). The same test file was
  run for real with `make test-driver-real` in the native image: 5 passed.
- **Private-attribute asserts** (`slots._value`, `capture._thread`) left as
  they are: removing them needs public accessors in the wrapper sources
  (A2's files).
- **`corrupt JSON → None + warning` on `Bucket`** — the adapter raises
  `ValueError` (tested); the None-plus-warning behaviour lives in the tick's
  `_owned_json` and was already covered. Swallowing it in the adapter would
  hide corruption of non-owned files, so the test pins the raise.
- **Reconciler dockerfile CVE fix and the Kyverno subject mismatch** — §2.
- **Wrapper scan on every PR** — cost; main pushes + dispatch only.
