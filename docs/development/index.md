# Setup

Clone the repo, then from the repo root:

```bash
make install   # uv sync --all-packages
make test      # uv run --all-packages pytest -q — both Python packages
cd frontend && bun install && bun run test   # the campaign browser
```

The repo is a **uv workspace**: the root `pyproject.toml` declares
`members = ["packages/*"]`, the single root `uv.lock` pins every member, and
the shared virtualenv lives at the repo root. Always sync with
`--all-packages` — a plain `uv sync` prunes the venv back to the virtual root
plus the dev dependency group and drops the members. `uv` manages the
virtualenv and lockfile — there is no separate `pip install -e .` step. The
frontend is a separate Bun/SvelteKit project under `frontend/`
([Campaign Browser](../reference/frontend.md)).

`make check` runs `ruff format` + `ruff check --fix`; `make typecheck` runs
`ty` on both packages against the workspace venv; `make ci` runs typecheck
plus the dagger `checks` and `test` functions, which is what CI runs, so a
green `make ci` locally is a strong signal a PR will pass. Cluster-local
constants (registry, S3 NodePort, namespace, release) come from `.env`
(copy `.env.example`; see [Local k3s development](local-k3s.md)).

The wrapper was built test-first (TDD): new behavior gets a failing test
before the implementation that makes it pass, and every bug found on a real
cluster (see the [test log](test-log.md)) became a regression test, not just
a fix. Keep that norm — `driver.py`'s pipeline-construction bugs (test log,
2026-07-27) were exactly the kind of thing a targeted unit test against the
real `htrflow.pipeline.pipeline.Pipeline` would have caught.

## Testing and acceptance levels

Four levels, from fast/isolated to slow/real. See [Testing](testing.md) for
full detail and how to run each one.

| # | Level | Covers |
|---|---|---|
| 0 | Library-API pin test | `Pipeline.from_config` + a 1-page fixture against the exact htrflow version in the pinned image — the canary that a version bump broke the D16 driver. **Planned, opt-in** (`make test-driver-real`); not run today |
| 1 | Unit tests | wrapper (manifest walking, fetch acceptance, resume, the streaming loop, the verification gate, exit-code mapping, log shipping), converter (parse, render, append-only, the 10 000-volume split), read API (pure Job/Pod/ConfigMap projection functions), frontend (schemas, derivation, components); everything mocked |
| 2 | Container smoke | the built image against a real 2-page manifest with a RustFS target; asserts PAGE/ALTO files + `manifest.json` land |
| 3 | Cluster acceptance | Kueue gating, kill-and-resume, a campaign rendered and applied; the fetch-vs-HTR numbers from `manifest.json` — the Phase 2 gate input |
