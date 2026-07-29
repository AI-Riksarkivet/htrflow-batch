# Setup

Clone the repo, then from the repo root:

```bash
make install   # uv sync --all-packages
make test      # uv run --no-sync pytest packages/wrapper/tests -q
```

The repo is a **uv workspace**: the root `pyproject.toml` declares
`members = ["packages/*"]`, the single root `uv.lock` pins every member, and
the shared virtualenv lives at the repo root. Always sync with
`--all-packages` — a plain `uv sync` prunes the venv back to the virtual root
plus the dev dependency group and drops the members. `uv` manages the
virtualenv and lockfile — there is no separate `pip install -e .` step.

`make check` runs `ruff format` + `ruff check --fix`
before you commit; `make ci` runs the same checks and tests through dagger,
which is what CI actually runs, so a green `make ci` locally is a strong
signal a PR will pass.

The wrapper was built test-first (TDD): new behavior gets a failing test
before the implementation that makes it pass, and every bug found on a real
cluster (see the [test log](test-log.md)) became a regression test, not just
a fix. Keep that norm — `driver.py`'s pipeline-construction bugs (test log,
2026-07-27) were exactly the kind of thing a targeted unit test against the
real `htrflow.pipeline.pipeline.Pipeline` would have caught, and now does.

## Testing and acceptance levels

Four levels, from fast/isolated to slow/real. See [Testing](testing.md) for
full detail and how to run each one.

| # | Level | Covers |
|---|---|---|
| 0 | Library-API pin test | `Pipeline.from_config` + a 1-page fixture against the exact htrflow version in the pinned image — the canary that a version bump broke the D16 driver |
| 1 | Wrapper unit tests | manifest walking, resume-list diffing, the streaming loop, the verification gate, exit-code mapping; mocked HTTP + S3 |
| 2 | Container smoke | the built image against a real 2-page manifest with a MinIO/RustFS target; asserts ALTO files + `manifest.json` land |
| 3 | Cluster acceptance | Kueue gating, kill-and-resume, `htrq report`'s fetch-vs-HTR numbers — the Phase 2 gate input |
