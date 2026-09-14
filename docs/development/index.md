# Setup

Clone the repository, then from its root:

```bash
make install   # uv sync --all-packages
make test      # uv run --all-packages pytest -q — the wrapper, converter and web packages
cd frontend && bun install && bun run test   # the campaign browser
```

The repository is a **uv workspace**: the root `pyproject.toml` declares
`members = ["packages/*"]`, the single root `uv.lock` pins every member, and
the shared virtualenv lives at the repository root. Always sync with
`--all-packages` — a plain `uv sync` prunes the venv back to the virtual root
plus the dev dependency group and drops the members. `uv` manages the
virtualenv and the lockfile; there is no separate `pip install -e .` step.
The frontend is a separate Bun/SvelteKit project under `frontend/`
([Campaign Browser](../reference/frontend.md)).

`make check` runs `ruff format` and `ruff check --fix`; `make typecheck` runs
`ty` on the wrapper, converter and web packages against the workspace venv;
`make ci` runs the type check and then the dagger `checks` and `test`
functions, which is what CI runs — a green `make ci` locally is a strong
signal that a pull request passes ([CI](ci.md)). Constants for a dev cluster
(registry, S3 endpoint, namespace, release) come from `.env`
([Dev cluster](dev-cluster.md#env)).

New behaviour gets a failing test before the implementation that makes it
pass, and every bug found on a real cluster becomes a regression test, not
only a fix. Bugs in how the driver builds an htrflow pipeline are exactly
what a targeted test against the real `htrflow.pipeline.pipeline.Pipeline`
catches — that is the level-0 test below.

## Testing and acceptance levels

Four levels, from fast and isolated to slow and real. [Testing](testing.md)
has the detail and how to run each one.

| # | Level | Covers |
|---|---|---|
| 0 | Library-API pin test | `Pipeline.from_config` and a one-page fixture against the htrflow inside the built wrapper image — the canary for an htrflow bump that breaks the driver. Opt-in (`make test-driver-real`, `dagger call test-driver`) |
| 1 | Unit tests | wrapper (manifest walking, fetch acceptance, resume, the streaming loop, the verification gate, exit-code mapping, log shipping), converter (parse, render, append-only, the 10 000-volume split), read API (pure Job/Pod/ConfigMap projection functions), frontend (schemas, derivation, components); everything mocked |
| 2 | Container smoke | the built image against a real two-page manifest with a RustFS target; asserts PAGE/ALTO files and `manifest.json` land |
| 3 | Cluster acceptance | Kueue gating, kill-and-resume, a campaign rendered and applied; the fetch-vs-HTR numbers from `manifest.json`, the evidence the [cache layer](../roadmap/cache-layer.md) proposal waits on |
